"""Pure batch logic: offset-based replay filtering, per-key compaction and the LSN guard."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ingestor.envelope import ChangeEvent

TopicPartition = tuple[str, int]
Offsets = dict[TopicPartition, int]  # last offset *applied* to a table, per topic-partition


def not_yet_applied(events: Iterable[ChangeEvent], committed: Mapping[TopicPartition, int]) -> list[ChangeEvent]:
    """Drop events a table has already committed (replay after restart / rebalance)."""
    return [e for e in events if e.offset > committed.get((e.topic, e.partition), -1)]


def latest_per_key(events: Iterable[ChangeEvent]) -> list[ChangeEvent]:
    """Collapse a batch to the final change per primary key.

    Kafka preserves order per partition and Debezium keys messages by PK, so within a batch the
    highest (lsn, offset) wins. A key that is inserted, updated and deleted in one batch yields
    only the delete, i.e. one write per key per commit.
    """
    latest: dict[tuple[object, ...], ChangeEvent] = {}
    for e in events:
        current = latest.get(e.key_tuple)
        if current is None or (e.lsn, e.offset) >= (current.lsn, current.offset):
            latest[e.key_tuple] = e
    return list(latest.values())


def newer_than_target(
    events: Iterable[ChangeEvent], target_lsn: Mapping[tuple[object, ...], int | None]
) -> list[ChangeEvent]:
    """LSN guard: never let an older change overwrite a newer row already in the table."""
    out = []
    for e in events:
        existing = target_lsn.get(e.key_tuple)
        if existing is None or e.lsn > existing:
            out.append(e)
    return out


def advance(offsets: Offsets, events: Iterable[ChangeEvent]) -> Offsets:
    merged = dict(offsets)
    for e in events:
        tp = (e.topic, e.partition)
        merged[tp] = max(merged.get(tp, -1), e.offset)
    return merged
