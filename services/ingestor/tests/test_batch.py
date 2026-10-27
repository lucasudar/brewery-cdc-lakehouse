from ingestor.batch import advance, latest_per_key, newer_than_target, not_yet_applied


def test_replayed_offsets_are_skipped(event_factory):
    events = [event_factory("insert", offset=o, pk=o) for o in range(5)]
    fresh = not_yet_applied(events, {(events[0].topic, 0): 2})
    assert [e.offset for e in fresh] == [3, 4]


def test_latest_change_per_key_wins(event_factory):
    events = [
        event_factory("insert", offset=1, pk=1, lsn=100),
        event_factory("update", offset=2, pk=1, lsn=200),
        event_factory("insert", offset=3, pk=2, lsn=300),
        event_factory("delete", offset=4, pk=1, lsn=400),
    ]
    latest = {e.key["consumption_id"]: e for e in latest_per_key(events)}
    assert latest[1].op == "d" and latest[1].offset == 4
    assert latest[2].op == "c"


def test_lsn_guard_rejects_older_or_equal_changes(event_factory):
    old = event_factory("update", offset=1, pk=1, lsn=100)
    same = event_factory("update", offset=2, pk=2, lsn=500)
    new = event_factory("update", offset=3, pk=3, lsn=900)
    unknown = event_factory("insert", offset=4, pk=4, lsn=1)
    target = {(1,): 200, (2,): 500, (3,): 800}
    assert {e.key["consumption_id"] for e in newer_than_target([old, same, new, unknown], target)} == {3, 4}


def test_advance_tracks_max_offset_per_partition(event_factory):
    events = [event_factory("insert", offset=o, pk=o) for o in (7, 3, 9)]
    assert advance({(events[0].topic, 0): 5}, events) == {(events[0].topic, 0): 9}
