import json
from datetime import UTC, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from ingestor.envelope import EnvelopeError, arrow_type_for, parse

from .conftest import TOPIC, load_fixture


def _parse(name: str, offset: int = 0):
    fx = load_fixture(name)
    return parse(
        topic=TOPIC,
        partition=0,
        offset=offset,
        key=json.dumps(fx["key"]).encode(),
        value=json.dumps(fx["value"]).encode(),
    )


@pytest.mark.parametrize("name, op", [("snapshot_read", "r"), ("insert", "c"), ("update", "u")])
def test_upserts_are_typed_from_connect_schema(name, op):
    event = _parse(name)
    assert event.op == op and not event.is_delete
    assert event.table == "novisto_consumption"
    assert set(event.key) == {"consumption_id"}
    assert isinstance(event.row["quantity_l"], Decimal)
    assert event.row["consumed_at"].tzinfo == UTC
    assert event.lsn > 0 and event.source_ts_ms > 0
    types = {c.name: c.arrow_type for c in event.columns}
    assert types["quantity_l"] == pa.decimal128(5, 3)  # source NUMERIC(5,3) preserved
    assert types["consumed_at"] == pa.timestamp("us", tz="UTC")
    assert types["consumption_id"] == pa.int32()


def test_delete_uses_before_image_and_has_no_row():
    event = _parse("delete")
    assert event.is_delete and event.row is None
    assert event.before["consumption_id"] == event.key["consumption_id"]


def test_update_keeps_before_image_for_bronze():
    event = _parse("update")
    assert event.before["quantity_l"] == Decimal("0.250")
    assert event.row["quantity_l"] == Decimal("0.750")


def test_tombstone_and_truncate_are_ignored():
    assert parse(topic=TOPIC, partition=0, offset=0, key=b"{}", value=None) is None
    fx = load_fixture("insert")
    fx["value"]["payload"]["op"] = "t"
    assert parse(topic=TOPIC, partition=0, offset=0, key=None, value=json.dumps(fx["value"]).encode()) is None


@pytest.mark.parametrize(
    "value",
    [b"not json", b'{"payload": {"op": "c"}}', json.dumps({"schema": {}, "payload": {"op": "c"}}).encode()],
)
def test_malformed_messages_raise_for_dlq(value):
    with pytest.raises(EnvelopeError):
        parse(topic=TOPIC, partition=0, offset=0, key=b"{}", value=value)


def test_missing_primary_key_raises():
    fx = load_fixture("insert")
    with pytest.raises(EnvelopeError, match="primary key"):
        parse(topic=TOPIC, partition=0, offset=0, key=None, value=json.dumps(fx["value"]).encode())


@pytest.mark.parametrize(
    "field, expected",
    [
        ({"type": "int64"}, pa.int64()),
        ({"type": "boolean"}, pa.bool_()),
        ({"type": "int32", "name": "io.debezium.time.Date"}, pa.date32()),
        ({"type": "int64", "name": "io.debezium.time.MicroTimestamp"}, pa.timestamp("us")),
        ({"type": "string", "parameters": {"__debezium.source.column.type": "NUMERIC"}}, pa.string()),
        ({"type": "string", "name": "io.debezium.data.Json"}, pa.string()),
    ],
)
def test_type_mapping(field, expected):
    assert arrow_type_for(field) == expected


def test_zoned_timestamp_is_normalised_to_utc():
    event = _parse("snapshot_read")
    assert event.row["consumed_at"] == datetime(2026, 9, 2, 15, 30, tzinfo=UTC)
