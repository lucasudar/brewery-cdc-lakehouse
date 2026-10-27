from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from ingestor.envelope import ChangeEvent, parse

FIXTURES = Path(__file__).parent / "fixtures"
TOPIC = "cdc.public.novisto_consumption"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"consumption_{name}.json").read_text())


def make_event(
    name: str,
    *,
    offset: int,
    pk: int | None = None,
    lsn: int | None = None,
    after: dict[str, Any] | None = None,
    extra_field: tuple[dict[str, Any], Any] | None = None,
) -> ChangeEvent:
    """Build an event from a real Debezium fixture, overriding key/lsn/columns as needed."""
    fx = copy.deepcopy(load_fixture(name))
    key, value = fx["key"], fx["value"]
    payload = value["payload"]
    image = "before" if payload["op"] == "d" else "after"
    if pk is not None:
        key["payload"]["consumption_id"] = pk
        payload[image]["consumption_id"] = pk
    if lsn is not None:
        payload["source"]["lsn"] = lsn
    if after:
        payload[image].update(after)
    if extra_field is not None:
        field_schema, field_value = extra_field
        for f in value["schema"]["fields"]:
            if f["field"] == image:
                f["fields"].append(field_schema)
        payload[image][field_schema["field"]] = field_value
    event = parse(
        topic=TOPIC,
        partition=0,
        offset=offset,
        key=json.dumps(key).encode(),
        value=json.dumps(value).encode(),
    )
    assert event is not None
    return event


@pytest.fixture
def event_factory():
    return make_event
