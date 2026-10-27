from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    catalog_uri: str = "http://localhost:8181"
    s3_endpoint: str = "http://localhost:8333"
    aws_access_key_id: str = "lakehouse"
    aws_secret_access_key: str = "lakehouse-secret"
    aws_region: str = "us-east-1"

    beers_table: str = "silver.beer_factory"
    consumption_table: str = "silver.novisto_consumption"
    daily_style_table: str = "gold.daily_style_consumption"

    # How often a request may hit the catalog to look for a newer snapshot (seconds).
    refresh_interval_s: float = 1.0

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
