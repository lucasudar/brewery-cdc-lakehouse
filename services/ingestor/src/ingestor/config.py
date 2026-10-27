from __future__ import annotations

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_group_id: str = "iceberg-ingestor"
    # Debezium topics are <prefix>.<schema>.<table>; heartbeats/internal topics don't match.
    kafka_topic_pattern: str = r"^cdc\.public\..+"
    dlq_topic: str = "cdc.dlq"

    catalog_uri: str = "http://localhost:8181"
    s3_endpoint: str = "http://localhost:8333"
    aws_access_key_id: str = "lakehouse"
    aws_secret_access_key: str = "lakehouse-secret"
    aws_region: str = "us-east-1"

    bronze_namespace: str = "bronze"
    silver_namespace: str = "silver"
    bronze_table: str = "cdc_events"

    # Micro-batch: commit every `flush_interval_s` or `max_batch_events`, whichever comes first.
    # One Iceberg commit per table per batch -> this is the freshness vs. small-files knob.
    flush_interval_s: float = 5.0
    max_batch_events: int = 5_000

    maintenance_interval_s: float = 300.0
    snapshot_retention_s: float = 3_600.0
    # Rewrite a table into fresh files once it has this many data files (only below `compaction_max_rows`).
    compaction_min_files: int = 20
    compaction_max_rows: int = 1_000_000

    health_file: str = "/tmp/ingestor.healthy"
    log_level: str = "INFO"

    @cached_property
    def catalog_properties(self) -> dict[str, str]:
        return {
            "type": "rest",
            "uri": self.catalog_uri,
            "s3.endpoint": self.s3_endpoint,
            "s3.access-key-id": self.aws_access_key_id,
            "s3.secret-access-key": self.aws_secret_access_key,
            "s3.region": self.aws_region,
            "s3.path-style-access": "true",
        }
