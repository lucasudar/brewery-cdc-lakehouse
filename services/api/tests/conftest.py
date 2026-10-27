from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from pyiceberg.catalog.sql import SqlCatalog

from api.config import Settings
from api.lakehouse import Lakehouse
from api.main import create_app

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
TS = pa.timestamp("us", tz="UTC")

BEERS = pa.schema(
    [
        ("beer_id", pa.int32()),
        ("name", pa.string()),
        ("style", pa.string()),
        ("abv", pa.decimal128(3, 1)),
        ("batch_size_l", pa.int32()),
        ("produced_at", TS),
        ("factory_site", pa.string()),
        ("updated_at", TS),
        ("_op", pa.string()),
        ("_lsn", pa.int64()),
        ("ibu", pa.int32()),  # a column added in the source after the API was written
    ]
)
CONSUMPTION = pa.schema(
    [
        ("consumption_id", pa.int32()),
        ("beer_id", pa.int32()),
        ("employee_team", pa.string()),
        ("quantity_l", pa.decimal128(5, 3)),
        ("consumed_at", TS),
        ("office", pa.string()),
        ("updated_at", TS),
        ("_op", pa.string()),
        ("_lsn", pa.int64()),
    ]
)
DAILY = pa.schema(
    [
        ("consumption_date", pa.date32()),
        ("style", pa.string()),
        ("factory_site", pa.string()),
        ("litres_consumed", pa.decimal128(18, 3)),
        ("pure_alcohol_litres", pa.decimal128(18, 4)),
        ("consumption_events", pa.int64()),
        ("distinct_beers", pa.int64()),
        ("distinct_teams", pa.int64()),
        ("last_source_change_at", TS),
    ]
)


def beer(beer_id: int, style: str = "IPA", site: str = "Montreal", ibu: int | None = None) -> dict:
    return {
        "beer_id": beer_id,
        "name": f"Beer #{beer_id}",
        "style": style,
        "abv": Decimal("5.5"),
        "batch_size_l": 1000,
        "produced_at": T0,
        "factory_site": site,
        "updated_at": T0,
        "_op": "r",
        "_lsn": 1,
        "ibu": ibu,
    }


def consumption(cid: int, beer_id: int, day: int, office: str = "Paris") -> dict:
    return {
        "consumption_id": cid,
        "beer_id": beer_id,
        "employee_team": "Data",
        "quantity_l": Decimal("0.500"),
        "consumed_at": datetime(2026, 9, day, 15, tzinfo=UTC),
        "office": office,
        "updated_at": T0,
        "_op": "c",
        "_lsn": 2,
    }


def daily(day: date, style: str, litres: str) -> dict:
    return {
        "consumption_date": day,
        "style": style,
        "factory_site": "Montreal",
        "litres_consumed": Decimal(litres),
        "pure_alcohol_litres": Decimal("0.0275"),
        "consumption_events": 1,
        "distinct_beers": 1,
        "distinct_teams": 1,
        "last_source_change_at": T0,
    }


class Lake:
    """A real Iceberg catalog (sqlite + local files) seeded like the pipeline would."""

    def __init__(self, catalog: SqlCatalog) -> None:
        self.catalog = catalog
        for ns in ("silver", "gold"):
            catalog.create_namespace(ns)

    def write(self, name: str, schema: pa.Schema, rows: list[dict], props: dict[str, str] | None = None) -> int:
        table = self.catalog.create_table_if_not_exists(name, schema=schema)
        data = pa.Table.from_pylist(rows, schema=schema)
        if table.current_snapshot() is None:
            table.append(data, snapshot_properties=props or {})
        else:
            table.overwrite(data, snapshot_properties=props or {})
        return self.catalog.load_table(name).current_snapshot().snapshot_id

    def seed(self) -> None:
        source_ms = str(int(T0.timestamp() * 1000))
        beers = self.write(
            "silver.beer_factory",
            BEERS,
            [beer(1, "Stout", "Sherbrooke"), beer(2, ibu=40), beer(3)],
            {"ingestor.max-source-ts-ms": source_ms},
        )
        cons = self.write(
            "silver.novisto_consumption",
            CONSUMPTION,
            [consumption(1, 1, 2), consumption(2, 2, 3, "London"), consumption(3, 2, 5)],
            {"ingestor.max-source-ts-ms": source_ms},
        )
        self.write(
            "gold.daily_style_consumption",
            DAILY,
            [
                daily(date(2026, 9, 2), "Stout", "0.500"),
                daily(date(2026, 9, 3), "IPA", "0.500"),
                daily(date(2026, 9, 5), "IPA", "0.500"),
            ],
            {
                "transform.source-snapshots": json.dumps({"beer_factory": beers, "novisto_consumption": cons}),
                "ingestor.max-source-ts-ms": source_ms,
            },
        )


@pytest.fixture
def lake(tmp_path) -> Lake:
    return Lake(SqlCatalog("test", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh"))


@pytest.fixture
def client(lake):
    lakehouse = Lakehouse(lambda: lake.catalog, refresh_interval_s=0)
    with TestClient(create_app(Settings(), lakehouse)) as c:
        yield c
