"""Iceberg side of the sink.

* ``bronze.cdc_events``: append-only changelog of every change event (audit, replay, backfill).
* ``silver.<table>``: current state per source table, maintained with copy-on-write deletes + appends.

Exactly-once *effect* without a two-phase commit: the Kafka offsets a table has applied are stored in
the summary of the very snapshot that contains the data, so data and progress commit atomically.
On restart each table resumes from its own offsets and skips what it already has, which makes
replays (crash between commits, rebalances, re-running the consumer group) harmless.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import AlwaysTrue, And, BooleanExpression, EqualTo, In, Or
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import DayTransform
from pyiceberg.types import IntegerType, LongType, NestedField, StringType, TimestamptzType

from ingestor.batch import Offsets, TopicPartition, advance, latest_per_key, newer_than_target, not_yet_applied
from ingestor.config import Settings
from ingestor.envelope import ChangeEvent, Column

log = structlog.get_logger()

OFFSETS_PROP = "ingestor.offsets"
SOURCE_TS_PROP = "ingestor.max-source-ts-ms"

TABLE_PROPERTIES = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    # Keep the metadata.json chain bounded: one commit per batch adds up quickly.
    "write.metadata.delete-after-commit.enabled": "true",
    "write.metadata.previous-versions-max": "20",
}

META_COLUMNS: tuple[pa.Field, ...] = (
    pa.field("_op", pa.string()),
    pa.field("_lsn", pa.int64()),
    pa.field("_source_ts", pa.timestamp("us", tz="UTC")),
    pa.field("_ingested_at", pa.timestamp("us", tz="UTC")),
)

BRONZE_SCHEMA = Schema(
    NestedField(1, "event_topic", StringType(), required=True),
    NestedField(2, "event_partition", IntegerType(), required=True),
    NestedField(3, "event_offset", LongType(), required=True),
    NestedField(4, "source_table", StringType(), required=True),
    NestedField(5, "op", StringType(), required=True),
    NestedField(6, "pk", StringType(), required=True),
    NestedField(7, "before", StringType()),
    NestedField(8, "after", StringType()),
    NestedField(9, "lsn", LongType(), required=True),
    NestedField(10, "tx_id", LongType()),
    NestedField(11, "source_ts", TimestamptzType(), required=True),
    NestedField(12, "ingested_at", TimestamptzType(), required=True),
)
BRONZE_SPEC = PartitionSpec(PartitionField(source_id=11, field_id=1000, transform=DayTransform(), name="source_day"))


def _encode_offsets(offsets: Offsets) -> str:
    return json.dumps({f"{t}|{p}": o for (t, p), o in sorted(offsets.items())}, separators=(",", ":"))


def _decode_offsets(raw: str) -> Offsets:
    out: Offsets = {}
    for tp, offset in json.loads(raw).items():
        topic, _, partition = tp.rpartition("|")
        out[(topic, int(partition))] = int(offset)
    return out


def committed_offsets(table: Table) -> Offsets:
    """Offsets recorded by the latest snapshot that carries them (walks ancestors, e.g. past maintenance)."""
    snapshot = table.current_snapshot()
    while snapshot is not None:
        raw = snapshot.summary.additional_properties.get(OFFSETS_PROP) if snapshot.summary else None
        if raw:
            return _decode_offsets(raw)
        snapshot = table.snapshot_by_id(snapshot.parent_snapshot_id) if snapshot.parent_snapshot_id else None
    return {}


def key_filter(pk: Sequence[str], keys: Iterable[tuple[Any, ...]]) -> BooleanExpression:
    keys = list(keys)
    if len(pk) == 1:
        return In(pk[0], [k[0] for k in keys])
    clauses = [And(*(EqualTo(col, val) for col, val in zip(pk, k, strict=True))) for k in keys]
    return clauses[0] if len(clauses) == 1 else Or(*clauses)


def _same_type(a: pa.DataType, b: pa.DataType) -> bool:
    if pa.types.is_string(a) or pa.types.is_large_string(a):
        return pa.types.is_string(b) or pa.types.is_large_string(b)
    return a.equals(b)


def _json(value: dict[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, default=str)


class IcebergWriter:
    def __init__(self, settings: Settings, catalog: Catalog | None = None) -> None:
        self.settings = settings
        self.catalog = catalog or load_catalog("lakehouse", **settings.catalog_properties)
        for ns in (settings.bronze_namespace, settings.silver_namespace):
            self.catalog.create_namespace_if_not_exists(ns)
        self.bronze = self._ensure_bronze()
        self._silver: dict[str, Table] = {}
        self._offsets: dict[str, Offsets] = {self._bronze_id: committed_offsets(self.bronze)}

    # ------------------------------------------------------------------ tables

    @property
    def _bronze_id(self) -> str:
        return f"{self.settings.bronze_namespace}.{self.settings.bronze_table}"

    def _silver_id(self, table: str) -> str:
        return f"{self.settings.silver_namespace}.{table}"

    def _ensure_bronze(self) -> Table:
        return self.catalog.create_table_if_not_exists(
            self._bronze_id, schema=BRONZE_SCHEMA, partition_spec=BRONZE_SPEC, properties=TABLE_PROPERTIES
        )

    def _load_silver(self, table: str) -> Table | None:
        if table not in self._silver:
            try:
                self._silver[table] = self.catalog.load_table(self._silver_id(table))
            except NoSuchTableError:
                return None
            self._offsets[self._silver_id(table)] = committed_offsets(self._silver[table])
        return self._silver[table]

    def _silver_arrow_schema(self, columns: Sequence[Column], pk: Sequence[str]) -> pa.Schema:
        # Only the PK is required: new/dropped source columns must never break the append.
        fields = [pa.field(c.name, c.arrow_type, nullable=c.name not in pk) for c in columns]
        return pa.schema([*fields, *META_COLUMNS])

    def _ensure_silver(self, table: str, columns: Sequence[Column], pk: Sequence[str]) -> Table:
        """Create the table on first sight, then evolve it (ADD COLUMN, type widening) as the source does."""
        wanted = self._silver_arrow_schema(columns, pk)
        tbl = self._load_silver(table)
        if tbl is None:
            log.info("creating silver table", table=self._silver_id(table), pk=list(pk))
            tbl = self.catalog.create_table_if_not_exists(
                self._silver_id(table), schema=wanted, properties=TABLE_PROPERTIES
            )
            self._offsets.setdefault(self._silver_id(table), {})

        current = tbl.schema().as_arrow()
        drift = [
            f.name
            for f in wanted
            if current.get_field_index(f.name) < 0 or not _same_type(current.field(f.name).type, f.type)
        ]
        if drift:
            log.info("evolving silver schema", table=self._silver_id(table), columns=drift)
            with tbl.update_schema() as update:
                update.union_by_name(wanted)
            tbl.refresh()

        if {tbl.schema().find_column_name(i) for i in tbl.schema().identifier_field_ids} != set(pk):
            with tbl.update_schema() as update:
                update.set_identifier_fields(*pk)
            tbl.refresh()
        self._silver[table] = tbl
        return tbl

    # ------------------------------------------------------------------ offsets

    def resume_positions(self, partitions: Iterable[TopicPartition]) -> dict[TopicPartition, int]:
        """Where the consumer must start: the slowest table that consumes a partition decides."""
        positions: dict[TopicPartition, int] = {}
        bronze = self._offsets[self._bronze_id]
        for topic, partition in partitions:
            tp = (topic, partition)
            silver_table = topic.rsplit(".", 1)[-1]
            silver = self._offsets.get(self._silver_id(silver_table)) if self._load_silver(silver_table) else {}
            committed = [bronze.get(tp, -1), (silver or {}).get(tp, -1)]
            positions[tp] = min(committed) + 1
        return positions

    def _props(self, offsets: Offsets, events: Sequence[ChangeEvent], previous: Table) -> dict[str, str]:
        props = {OFFSETS_PROP: _encode_offsets(offsets)}
        last_ts = max((e.source_ts_ms for e in events), default=None)
        if last_ts is None and previous.current_snapshot() is not None:
            last_ts = int(previous.current_snapshot().summary.additional_properties.get(SOURCE_TS_PROP, 0))
        props[SOURCE_TS_PROP] = str(last_ts or 0)
        return props

    # ------------------------------------------------------------------ writes

    def apply(self, events: Sequence[ChangeEvent]) -> dict[str, int]:
        """Apply one micro-batch. Returns rows written per table (for logging/metrics)."""
        stats = {self._bronze_id: self._apply_bronze(events)}
        by_table: dict[str, list[ChangeEvent]] = defaultdict(list)
        for e in events:
            by_table[e.table].append(e)
        for table, table_events in by_table.items():
            stats[self._silver_id(table)] = self._apply_silver(table, table_events)
        return stats

    def _apply_bronze(self, events: Sequence[ChangeEvent]) -> int:
        committed = self._offsets[self._bronze_id]
        todo = not_yet_applied(events, committed)
        if not todo:
            return 0
        now = datetime.now(UTC)
        rows = [
            {
                "event_topic": e.topic,
                "event_partition": e.partition,
                "event_offset": e.offset,
                "source_table": e.table,
                "op": e.op,
                "pk": e.raw_key,
                "before": _json(e.before),
                "after": _json(e.row),
                "lsn": e.lsn,
                "tx_id": e.tx_id,
                "source_ts": datetime.fromtimestamp(e.source_ts_ms / 1000, UTC),
                "ingested_at": now,
            }
            for e in todo
        ]
        offsets = advance(committed, todo)
        self.bronze.append(
            pa.Table.from_pylist(rows, schema=self.bronze.schema().as_arrow()),
            snapshot_properties=self._props(offsets, todo, self.bronze),
        )
        self._offsets[self._bronze_id] = offsets
        return len(rows)

    def _apply_silver(self, table: str, events: Sequence[ChangeEvent]) -> int:
        identifier = self._silver_id(table)
        if self._load_silver(table) is None:
            self._offsets.setdefault(identifier, {})
        committed = self._offsets.get(identifier, {})
        fresh = not_yet_applied(events, committed)
        if not fresh:
            return 0

        latest = latest_per_key(fresh)
        pk = sorted(latest[0].key)
        newest_columns = max(latest, key=lambda e: (e.lsn, e.offset)).columns
        tbl = self._ensure_silver(table, newest_columns, pk)

        existing = tbl.scan(
            row_filter=key_filter(pk, (e.key_tuple for e in latest)), selected_fields=(*pk, "_lsn")
        ).to_arrow()
        existing_lsn = {tuple(row[c] for c in pk): row["_lsn"] for row in existing.to_pylist()}
        todo = newer_than_target(latest, existing_lsn)

        now = datetime.now(UTC)
        upserts = [
            {
                **e.row,
                "_op": e.op,
                "_lsn": e.lsn,
                "_source_ts": datetime.fromtimestamp(e.source_ts_ms / 1000, UTC),
                "_ingested_at": now,
            }
            for e in todo
            if not e.is_delete and e.row is not None
        ]
        to_remove = [e.key_tuple for e in todo if e.key_tuple in existing_lsn]

        offsets = advance(committed, fresh)
        props = self._props(offsets, fresh, tbl)
        if to_remove or upserts:
            with tbl.transaction() as tx:
                if to_remove:
                    tx.delete(key_filter(pk, to_remove), snapshot_properties=props)
                if upserts:
                    arrow_schema = tx.table_metadata.schema().as_arrow()
                    tx.append(pa.Table.from_pylist(upserts, schema=arrow_schema), snapshot_properties=props)
            tbl.refresh()
        # If everything was guarded out nothing is committed; replaying those events later is a no-op.
        self._offsets[identifier] = offsets
        deletes = sum(e.is_delete for e in todo)
        log.debug("silver applied", table=identifier, upserts=len(upserts), deletes=deletes)
        return len(upserts) + deletes

    # ------------------------------------------------------------------ maintenance

    def maintain(self) -> None:
        """Expire old snapshots and compact small files. Runs between batches (single writer, no races)."""
        tables = {self._bronze_id: self.bronze, **{self._silver_id(n): t for n, t in self._silver.items()}}
        for identifier, tbl in tables.items():
            started = time.monotonic()
            tbl.refresh()
            files = tbl.inspect.files()
            rows = sum(files.column("record_count").to_pylist())
            if files.num_rows >= self.settings.compaction_min_files and rows <= self.settings.compaction_max_rows:
                # Small tables only: a full rewrite is the simplest correct compaction PyIceberg offers.
                props = {OFFSETS_PROP: _encode_offsets(self._offsets.get(identifier, {}))}
                snapshot = tbl.current_snapshot()
                if snapshot is not None and SOURCE_TS_PROP in snapshot.summary.additional_properties:
                    props[SOURCE_TS_PROP] = snapshot.summary.additional_properties[SOURCE_TS_PROP]
                tbl.overwrite(tbl.scan().to_arrow(), overwrite_filter=AlwaysTrue(), snapshot_properties=props)
                tbl.refresh()
                log.info("compacted", table=identifier, files_before=files.num_rows, rows=rows)
            cutoff_ms = (datetime.now(UTC) - timedelta(seconds=self.settings.snapshot_retention_s)).timestamp() * 1000
            expired = [s.snapshot_id for s in tbl.snapshots() if s.timestamp_ms < cutoff_ms]
            current = tbl.current_snapshot()
            expired = [s for s in expired if current is None or s != current.snapshot_id]
            if expired:
                tbl.maintenance.expire_snapshots().by_ids(expired).commit()
                tbl.refresh()
            log.info(
                "maintenance done",
                table=identifier,
                expired_snapshots=len(expired),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
