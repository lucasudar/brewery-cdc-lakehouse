"""End-to-end semantics of the sink against a real (local, file-based) Iceberg catalog."""

from decimal import Decimal

import pytest
from pyiceberg.catalog.sql import SqlCatalog

from ingestor.config import Settings
from ingestor.iceberg_writer import IcebergWriter, committed_offsets

IBU = {"type": "int32", "optional": True, "field": "ibu"}


@pytest.fixture
def catalog(tmp_path):
    return SqlCatalog("test", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}/warehouse")


@pytest.fixture
def writer(catalog):
    return IcebergWriter(Settings(), catalog=catalog)


def silver_rows(catalog):
    table = catalog.load_table("silver.novisto_consumption")
    return {r["consumption_id"]: r for r in table.scan().to_arrow().to_pylist()}


def test_insert_update_delete_land_as_current_state(writer, catalog, event_factory):
    writer.apply([event_factory("snapshot_read", offset=o, pk=o, lsn=10 + o) for o in range(1, 4)])
    writer.apply(
        [
            event_factory("update", offset=4, pk=2, lsn=50, after={"quantity_l": "2.000"}),
            event_factory("delete", offset=5, pk=3, lsn=60),
        ]
    )
    rows = silver_rows(catalog)
    assert set(rows) == {1, 2}
    assert rows[2]["quantity_l"] == Decimal("2.000") and rows[2]["_op"] == "u"
    bronze = catalog.load_table("bronze.cdc_events").scan().to_arrow()
    assert bronze.num_rows == 5 and sorted(bronze.column("op").to_pylist()) == ["d", "r", "r", "r", "u"]


def test_replay_after_restart_is_idempotent(catalog, event_factory):
    batch = [event_factory("insert", offset=o, pk=o, lsn=100 + o) for o in range(10)]
    IcebergWriter(Settings(), catalog=catalog).apply(batch)

    restarted = IcebergWriter(Settings(), catalog=catalog)  # fresh process, state only from Iceberg
    assert restarted.resume_positions([(batch[0].topic, 0)]) == {(batch[0].topic, 0): 10}
    stats = restarted.apply(batch)  # e.g. consumer group offsets lost -> full replay
    assert all(v == 0 for v in stats.values())
    assert len(silver_rows(catalog)) == 10
    assert catalog.load_table("bronze.cdc_events").scan().to_arrow().num_rows == 10
    assert committed_offsets(catalog.load_table("silver.novisto_consumption")) == {(batch[0].topic, 0): 9}


def test_older_change_never_overwrites_newer_row(writer, catalog, event_factory):
    writer.apply([event_factory("update", offset=1, pk=1, lsn=500, after={"quantity_l": "9.000"})])
    writer.apply([event_factory("update", offset=2, pk=1, lsn=400, after={"quantity_l": "1.000"})])
    assert silver_rows(catalog)[1]["quantity_l"] == Decimal("9.000")


def test_insert_then_delete_in_same_batch_leaves_nothing(writer, catalog, event_factory):
    writer.apply([event_factory("insert", offset=1, pk=1, lsn=10), event_factory("delete", offset=2, pk=1, lsn=20)])
    assert silver_rows(catalog) == {}


def test_new_source_column_evolves_silver_schema(writer, catalog, event_factory):
    writer.apply([event_factory("insert", offset=1, pk=1, lsn=10)])
    writer.apply([event_factory("insert", offset=2, pk=2, lsn=20, extra_field=(IBU, 40))])
    rows = silver_rows(catalog)
    assert rows[1]["ibu"] is None and rows[2]["ibu"] == 40
    schema = catalog.load_table("silver.novisto_consumption").schema()
    assert [schema.find_column_name(i) for i in schema.identifier_field_ids] == ["consumption_id"]


def test_maintenance_compacts_and_keeps_offsets(catalog, event_factory):
    settings = Settings(compaction_min_files=3, snapshot_retention_s=0)
    writer = IcebergWriter(settings, catalog=catalog)
    for o in range(1, 6):
        writer.apply([event_factory("insert", offset=o, pk=o, lsn=o)])
    writer.maintain()
    table = catalog.load_table("silver.novisto_consumption")
    assert table.inspect.files().num_rows == 1
    assert len(table.snapshots()) == 1
    assert committed_offsets(table) == {("cdc.public.novisto_consumption", 0): 5}
    assert len(silver_rows(catalog)) == 5
