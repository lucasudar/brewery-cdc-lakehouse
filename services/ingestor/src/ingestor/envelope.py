"""Parse Debezium change events (JsonConverter with schemas.enable=true) into typed records.

The Connect schema embedded in every message is the contract: it gives us column types,
the source NUMERIC precision/scale (``column.propagate.source.type``) and new columns as
soon as they appear in Postgres, so no schema registry is needed for this scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa

# Debezium ops: r = snapshot read, c = insert, u = update, d = delete, t = truncate, m = message
UPSERT_OPS = frozenset({"r", "c", "u"})
DELETE_OP = "d"

_EPOCH = date(1970, 1, 1)
_MAX_DECIMAL_PRECISION = 38


class EnvelopeError(ValueError):
    """The message is not a Debezium change event we can apply (routed to the DLQ)."""


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    arrow_type: pa.DataType
    connect_type: str
    logical_name: str | None


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    table: str
    op: str
    key: dict[str, Any]
    row: dict[str, Any] | None  # after-image for upserts, None for deletes
    before: dict[str, Any] | None
    lsn: int
    tx_id: int | None
    source_ts_ms: int
    columns: tuple[Column, ...]
    topic: str
    partition: int
    offset: int
    raw_key: str = field(repr=False, default="")

    @property
    def is_delete(self) -> bool:
        return self.op == DELETE_OP

    @property
    def key_tuple(self) -> tuple[Any, ...]:
        return tuple(self.key[k] for k in sorted(self.key))


def arrow_type_for(connect_field: dict[str, Any]) -> pa.DataType:
    """Map a Kafka Connect field schema (plus Debezium source-type hints) to an Arrow type."""
    ctype = connect_field.get("type")
    logical = connect_field.get("name")
    params = connect_field.get("parameters") or {}
    source_type = str(params.get("__debezium.source.column.type", "")).upper()

    if logical in ("io.debezium.time.ZonedTimestamp",):
        return pa.timestamp("us", tz="UTC")
    if logical in ("io.debezium.time.Timestamp", "io.debezium.time.MicroTimestamp", "io.debezium.time.NanoTimestamp"):
        return pa.timestamp("us")
    if logical == "io.debezium.time.Date":
        return pa.date32()
    if source_type in ("NUMERIC", "DECIMAL") and ctype == "string":
        precision = int(params.get("__debezium.source.column.length", 0) or 0)
        scale = int(params.get("__debezium.source.column.scale", 0) or 0)
        if 0 < precision <= _MAX_DECIMAL_PRECISION:
            return pa.decimal128(precision, scale)
        return pa.string()  # unconstrained NUMERIC: keep the exact textual value
    return {
        "int8": pa.int32(),
        "int16": pa.int32(),
        "int32": pa.int32(),
        "int64": pa.int64(),
        "float32": pa.float32(),
        "float64": pa.float64(),
        "boolean": pa.bool_(),
        "bytes": pa.binary(),
    }.get(ctype, pa.string())


def convert_value(value: Any, column: Column) -> Any:
    """Convert a JSON value into the Python value Arrow expects for ``column.arrow_type``."""
    if value is None:
        return None
    t = column.arrow_type
    if pa.types.is_timestamp(t):
        if column.logical_name == "io.debezium.time.ZonedTimestamp":
            return datetime.fromisoformat(value).astimezone(UTC)
        if column.logical_name == "io.debezium.time.Timestamp":
            return datetime.fromtimestamp(value / 1_000, UTC).replace(tzinfo=None)
        if column.logical_name == "io.debezium.time.NanoTimestamp":
            value //= 1_000
        return datetime(1970, 1, 1) + timedelta(microseconds=value)
    if pa.types.is_date(t):
        return _EPOCH + timedelta(days=value)
    if pa.types.is_decimal(t):
        return Decimal(value)
    if pa.types.is_string(t) and not isinstance(value, str):
        return json.dumps(value)
    return value


def _struct_fields(schema: dict[str, Any], name: str) -> list[dict[str, Any]]:
    for f in schema.get("fields", []):
        if f.get("field") == name:
            return list(f.get("fields", []))
    return []


def parse(*, topic: str, partition: int, offset: int, key: bytes | None, value: bytes | None) -> ChangeEvent | None:
    """Return a ChangeEvent, ``None`` for messages that carry no row change, or raise EnvelopeError."""
    if value is None:
        return None  # tombstone (disabled in the connector, ignored defensively)
    try:
        message = json.loads(value)
        key_message = json.loads(key) if key else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnvelopeError(f"invalid json: {exc}") from exc

    payload = message.get("payload")
    schema = message.get("schema")
    if not isinstance(payload, dict) or not isinstance(schema, dict):
        raise EnvelopeError("expected JsonConverter envelope with 'schema' and 'payload'")

    op = payload.get("op")
    if op not in UPSERT_OPS and op != DELETE_OP:
        return None  # truncate / logical decoding messages are out of scope

    source = payload.get("source") or {}
    image_name = "before" if op == DELETE_OP else "after"
    image = payload.get(image_name)
    if not isinstance(image, dict):
        raise EnvelopeError(f"op={op} without '{image_name}' image")

    connect_fields = _struct_fields(schema, image_name)
    columns = tuple(
        Column(
            name=f["field"],
            arrow_type=arrow_type_for(f),
            connect_type=f.get("type", "string"),
            logical_name=f.get("name"),
        )
        for f in connect_fields
    )
    if not columns:
        raise EnvelopeError(f"no field schema for '{image_name}'")
    by_name = {c.name: c for c in columns}

    def typed(img: dict[str, Any] | None) -> dict[str, Any] | None:
        if img is None:
            return None
        return {k: convert_value(v, by_name[k]) if k in by_name else v for k, v in img.items()}

    key_payload = key_message.get("payload") if isinstance(key_message, dict) else None
    if not isinstance(key_payload, dict) or not key_payload:
        raise EnvelopeError("missing primary key (table without PK or REPLICA IDENTITY?)")

    lsn = source.get("lsn")
    ts_ms = source.get("ts_ms") or payload.get("ts_ms")
    if lsn is None or ts_ms is None:
        raise EnvelopeError("missing source.lsn / source.ts_ms")

    typed_key = {k: convert_value(v, by_name[k]) if k in by_name else v for k, v in key_payload.items()}
    return ChangeEvent(
        table=str(source.get("table") or topic.rsplit(".", 1)[-1]),
        op=op,
        key=typed_key,
        row=None if op == DELETE_OP else typed(image),
        before=typed(payload.get("before")),
        lsn=int(lsn),
        tx_id=source.get("txId"),
        source_ts_ms=int(ts_ms),
        columns=columns,
        topic=topic,
        partition=partition,
        offset=offset,
        raw_key=json.dumps(key_payload, sort_keys=True, default=str),
    )
