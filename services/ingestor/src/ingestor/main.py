"""Consume Debezium topics in micro-batches and commit them to Iceberg.

Failure model: any error while writing to Iceberg crashes the process (docker restarts it).
Progress lives in Iceberg snapshots, not in the consumer group, so a restart simply resumes
from the last committed snapshot. Kafka offsets are committed afterwards for lag monitoring only.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType

import structlog
from confluent_kafka import OFFSET_BEGINNING, Consumer, KafkaError, Message, Producer, TopicPartition

from ingestor.config import Settings
from ingestor.envelope import ChangeEvent, EnvelopeError, parse
from ingestor.iceberg_writer import IcebergWriter

log = structlog.get_logger()


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


class Ingestor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.writer = IcebergWriter(settings)
        self.running = True
        self.consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": settings.kafka_group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "topic.metadata.refresh.interval.ms": 5_000,  # pick up new tables' topics quickly
                "max.poll.interval.ms": 600_000,
            }
        )
        self.dlq = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers, "enable.idempotence": True})
        self._last_maintenance = time.monotonic()

    def _on_assign(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        positions = self.writer.resume_positions((p.topic, p.partition) for p in partitions)
        for p in partitions:
            position = positions[(p.topic, p.partition)]
            p.offset = position if position > 0 else OFFSET_BEGINNING
        log.info("partitions assigned", positions={f"{p.topic}[{p.partition}]": p.offset for p in partitions})
        consumer.assign(partitions)
        # Mirror Iceberg progress into the consumer group so lag dashboards are right after a restart.
        resumed = [p for p in partitions if p.offset >= 0]
        if resumed:
            consumer.commit(offsets=resumed, asynchronous=True)

    def _to_dlq(self, msg: Message, error: Exception) -> None:
        log.warning("event sent to DLQ", topic=msg.topic(), offset=msg.offset(), error=str(error))
        self.dlq.produce(
            self.settings.dlq_topic,
            key=msg.key(),
            value=msg.value(),
            headers={
                "error": str(error),
                "source": json.dumps({"topic": msg.topic(), "partition": msg.partition(), "offset": msg.offset()}),
            },
        )

    def _poll_batch(self) -> tuple[list[ChangeEvent], int]:
        events: list[ChangeEvent] = []
        consumed = 0
        deadline = time.monotonic() + self.settings.flush_interval_s
        while self.running and consumed < self.settings.max_batch_events:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for msg in self.consumer.consume(num_messages=500, timeout=min(remaining, 1.0)):
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error("kafka error", error=str(msg.error()))
                    continue
                consumed += 1
                try:
                    event = parse(
                        topic=msg.topic(),
                        partition=msg.partition(),
                        offset=msg.offset(),
                        key=msg.key(),
                        value=msg.value(),
                    )
                except EnvelopeError as exc:
                    self._to_dlq(msg, exc)
                    continue
                if event is not None:
                    events.append(event)
        return events, consumed

    def run(self) -> None:
        self.consumer.subscribe([self.settings.kafka_topic_pattern], on_assign=self._on_assign)
        log.info("ingestor started", pattern=self.settings.kafka_topic_pattern)
        health = Path(self.settings.health_file)
        while self.running:
            events, consumed = self._poll_batch()
            if consumed:
                started = time.monotonic()
                stats = self.writer.apply(events)
                self.dlq.flush(10)
                self.consumer.commit(asynchronous=False)
                lag_s = time.time() - max(e.source_ts_ms for e in events) / 1000 if events else None
                log.info(
                    "batch committed",
                    consumed=consumed,
                    events=len(events),
                    written=stats,
                    commit_ms=round((time.monotonic() - started) * 1000),
                    source_to_iceberg_s=round(lag_s, 2) if lag_s is not None else None,
                )
            if time.monotonic() - self._last_maintenance >= self.settings.maintenance_interval_s:
                self.writer.maintain()
                self._last_maintenance = time.monotonic()
            health.touch()
        self.consumer.close()
        log.info("ingestor stopped")

    def stop(self, signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown requested, finishing current batch", signal=signum)
        self.running = False


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    ingestor = Ingestor(settings)
    signal.signal(signal.SIGTERM, ingestor.stop)
    signal.signal(signal.SIGINT, ingestor.stop)
    ingestor.run()


if __name__ == "__main__":
    main()
