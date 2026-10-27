"""Snapshot-consistent, cached reads of Iceberg tables.

Each table is materialised as an Arrow table *per snapshot id*: a request checks the catalog at most
every ``refresh_interval_s`` and only re-reads data files when the snapshot actually changed. Every
response is therefore computed from exactly one snapshot, which the API reports in ``meta``.
Fine for this data size; for large tables we'd push filters into the scan instead (``row_filter``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import reduce
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table


class TableNotReady(LookupError):
    """The table does not exist or has no data yet (pipeline still starting)."""


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    name: str
    snapshot_id: int
    committed_at: datetime
    summary: dict[str, str]
    data: pa.Table

    def select(
        self,
        filters: Sequence[pc.Expression] = (),
        sort_by: Sequence[tuple[str, str]] = (),
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filter, sort and paginate. Returns (rows, total rows matching the filters)."""
        table = self.data
        if filters:
            table = table.filter(reduce(lambda a, b: a & b, filters))
        total = table.num_rows
        if sort_by:
            table = table.sort_by(list(sort_by))
        table = table.slice(offset, limit)
        return table.to_pylist(), total


@dataclass
class _Cached:
    checked_at: float
    snapshot: TableSnapshot


class Lakehouse:
    def __init__(self, catalog_factory: Callable[[], Catalog], refresh_interval_s: float = 1.0) -> None:
        self._catalog_factory = catalog_factory
        self._catalog: Catalog | None = None
        self._refresh_interval_s = refresh_interval_s
        self._cache: dict[str, _Cached] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = self._catalog_factory()
        return self._catalog

    def _lock_for(self, name: str) -> threading.Lock:
        with self._global_lock:
            return self._locks.setdefault(name, threading.Lock())

    def _load(self, name: str) -> Table:
        try:
            return self.catalog.load_table(name)
        except NoSuchTableError as exc:
            raise TableNotReady(f"table {name} does not exist yet") from exc

    def snapshot(self, name: str) -> TableSnapshot:
        cached = self._cache.get(name)
        if cached and time.monotonic() - cached.checked_at < self._refresh_interval_s:
            return cached.snapshot
        with self._lock_for(name):
            cached = self._cache.get(name)
            if cached and time.monotonic() - cached.checked_at < self._refresh_interval_s:
                return cached.snapshot
            table = self._load(name)
            current = table.current_snapshot()
            if current is None:
                raise TableNotReady(f"table {name} has no snapshot yet")
            if cached and cached.snapshot.snapshot_id == current.snapshot_id:
                cached.checked_at = time.monotonic()
                return cached.snapshot
            snapshot = TableSnapshot(
                name=name,
                snapshot_id=current.snapshot_id,
                committed_at=datetime.fromtimestamp(current.timestamp_ms / 1000, UTC),
                summary=dict(current.summary.additional_properties) if current.summary else {},
                data=table.scan(snapshot_id=current.snapshot_id).to_arrow(),
            )
            self._cache[name] = _Cached(checked_at=time.monotonic(), snapshot=snapshot)
            return snapshot

    def current_snapshot_id(self, name: str) -> int | None:
        try:
            return self.snapshot(name).snapshot_id
        except TableNotReady:
            return None

    def ping(self) -> None:
        self.catalog.list_namespaces()
