"""Event-driven dbt runner: rebuild the gold mart whenever a silver table gets a new Iceberg snapshot.

Write-audit-publish:
  1. resolve the current snapshot id of every silver source (the "input version"),
  2. ``dbt build`` pinned to exactly those snapshots (models + data tests, in DuckDB),
  3. only if every blocking test passes, atomically ``overwrite`` the Iceberg gold table and record
     the input snapshot ids in the gold snapshot summary.

Step 3 doubles as the checkpoint: after a restart the runner compares the recorded input version
with the current one, so it never republishes the same input twice and never misses a change.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

import pyarrow.parquet as pq
import structlog
from dbt.cli.main import dbtRunner
from pydantic_settings import BaseSettings
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table

log = structlog.get_logger()

SOURCES = ("beer_factory", "novisto_consumption")
LINEAGE_PROP = "transform.source-snapshots"
SOURCE_TS_PROP = "ingestor.max-source-ts-ms"  # written by the ingestor on every silver commit
MART = "fct_daily_style_consumption"

TABLE_PROPERTIES = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    "write.metadata.delete-after-commit.enabled": "true",
    "write.metadata.previous-versions-max": "20",
}


class Settings(BaseSettings):
    catalog_uri: str = "http://localhost:8181"
    s3_endpoint: str = "http://localhost:8333"
    aws_access_key_id: str = "lakehouse"
    aws_secret_access_key: str = "lakehouse-secret"
    aws_region: str = "us-east-1"

    silver_namespace: str = "silver"
    gold_namespace: str = "gold"
    gold_table: str = "daily_style_consumption"

    poll_interval_s: float = 5.0
    retry_failed_after_s: float = 60.0
    snapshot_retention_s: float = 3_600.0

    dbt_project_dir: str = "."
    mart_output_dir: str = "target/marts"
    health_file: str = "/tmp/transformer.healthy"
    log_level: str = "INFO"

    def catalog(self) -> Catalog:
        return load_catalog(
            "lakehouse",
            type="rest",
            uri=self.catalog_uri,
            **{
                "s3.endpoint": self.s3_endpoint,
                "s3.access-key-id": self.aws_access_key_id,
                "s3.secret-access-key": self.aws_secret_access_key,
                "s3.region": self.aws_region,
                "s3.path-style-access": "true",
            },
        )


def configure_logging(level: str) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
    )


class Runner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = settings.catalog()
        self.catalog.create_namespace_if_not_exists(settings.gold_namespace)
        self.dbt = dbtRunner()
        self.running = True
        self._failed: tuple[dict[str, int], float] | None = None

    @property
    def gold_id(self) -> str:
        return f"{self.settings.gold_namespace}.{self.settings.gold_table}"

    def source_snapshots(self) -> dict[str, int] | None:
        """Current snapshot id per silver source, or None until every source has data."""
        snapshots = {}
        for name in SOURCES:
            try:
                snapshot = self.catalog.load_table(f"{self.settings.silver_namespace}.{name}").current_snapshot()
            except NoSuchTableError:
                return None
            if snapshot is None:
                return None
            snapshots[name] = snapshot.snapshot_id
        return snapshots

    def _gold(self) -> Table | None:
        try:
            return self.catalog.load_table(self.gold_id)
        except NoSuchTableError:
            return None

    def published_snapshots(self) -> dict[str, int]:
        gold = self._gold()
        snapshot = gold.current_snapshot() if gold else None
        raw = snapshot.summary.additional_properties.get(LINEAGE_PROP) if snapshot else None
        return json.loads(raw) if raw else {}

    def _source_freshness_ms(self, snapshots: dict[str, int]) -> int:
        latest = 0
        for name, snapshot_id in snapshots.items():
            table = self.catalog.load_table(f"{self.settings.silver_namespace}.{name}")
            snapshot = table.snapshot_by_id(snapshot_id)
            if snapshot and snapshot.summary:
                latest = max(latest, int(snapshot.summary.additional_properties.get(SOURCE_TS_PROP, 0)))
        return latest

    def build(self, snapshots: dict[str, int]) -> bool:
        Path(self.settings.mart_output_dir).mkdir(parents=True, exist_ok=True)
        dbt_vars = {f"{name}_snapshot_id": snapshot_id for name, snapshot_id in snapshots.items()}
        result = self.dbt.invoke(
            [
                "build",
                "--project-dir",
                self.settings.dbt_project_dir,
                "--profiles-dir",
                self.settings.dbt_project_dir,
                "--vars",
                json.dumps(dbt_vars),
                "--no-use-colors",
                "--log-level",
                "warn",
            ]
        )
        statuses: dict[str, int] = {}
        for node in getattr(result.result, "results", None) or []:
            statuses[str(node.status)] = statuses.get(str(node.status), 0) + 1
        if not result.success:
            log.error("dbt build failed, gold not published", statuses=statuses, error=str(result.exception or ""))
            return False
        log.info("dbt build passed", statuses=statuses)
        return True

    def publish(self, snapshots: dict[str, int]) -> int:
        mart = pq.read_table(Path(self.settings.mart_output_dir) / f"{MART}.parquet")
        gold = self._gold()
        if gold is None:
            gold = self.catalog.create_table_if_not_exists(
                self.gold_id, schema=mart.schema, properties=TABLE_PROPERTIES
            )
        else:
            current = gold.schema().as_arrow()
            if any(current.get_field_index(f.name) < 0 for f in mart.schema):
                with gold.update_schema() as update:
                    update.union_by_name(mart.schema)
                gold.refresh()
        props = {
            LINEAGE_PROP: json.dumps(snapshots, sort_keys=True),
            SOURCE_TS_PROP: str(self._source_freshness_ms(snapshots)),
            "transform.published-at": datetime.now(UTC).isoformat(),
        }
        # One atomic snapshot swap: readers see either the previous or the new mart, never a partial one.
        data = mart.cast(gold.schema().as_arrow())
        if gold.current_snapshot() is None:
            gold.append(data, snapshot_properties=props)
        else:
            gold.overwrite(data, snapshot_properties=props)
        self._expire(gold)
        return mart.num_rows

    def _expire(self, gold: Table) -> None:
        gold.refresh()
        cutoff_ms = (datetime.now(UTC) - timedelta(seconds=self.settings.snapshot_retention_s)).timestamp() * 1000
        current = gold.current_snapshot()
        old = [
            s.snapshot_id
            for s in gold.snapshots()
            if s.timestamp_ms < cutoff_ms and (current is None or s.snapshot_id != current.snapshot_id)
        ]
        if old:
            gold.maintenance.expire_snapshots().by_ids(old).commit()

    def tick(self) -> None:
        snapshots = self.source_snapshots()
        if snapshots is None:
            log.info("waiting for silver tables")
            return
        if snapshots == self.published_snapshots():
            return
        recently_failed = self._failed is not None and self._failed[0] == snapshots
        if recently_failed and time.monotonic() - self._failed[1] < self.settings.retry_failed_after_s:
            return  # same input already failed its audit; wait for new data or the retry window

        started = time.monotonic()
        if not self.build(snapshots):
            self._failed = (snapshots, time.monotonic())
            return
        self._failed = None
        rows = self.publish(snapshots)
        log.info(
            "gold published",
            table=self.gold_id,
            rows=rows,
            source_snapshots=snapshots,
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    def run(self) -> None:
        log.info("transformer started", poll_interval_s=self.settings.poll_interval_s)
        health = Path(self.settings.health_file)
        while self.running:
            self.tick()
            health.touch()
            deadline = time.monotonic() + self.settings.poll_interval_s
            while self.running and time.monotonic() < deadline:
                time.sleep(0.2)
        log.info("transformer stopped")

    def stop(self, signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown requested", signal=signum)
        self.running = False


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    runner = Runner(settings)
    signal.signal(signal.SIGTERM, runner.stop)
    signal.signal(signal.SIGINT, runner.stop)
    runner.run()


if __name__ == "__main__":
    main()
