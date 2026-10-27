from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pyiceberg.catalog import load_catalog

from api.config import Settings
from api.lakehouse import Lakehouse
from api.routes import router

DESCRIPTION = """
Read API over the brewery lakehouse:
**Postgres → Debezium → Redpanda → Iceberg (silver/gold) → this API**.

* **Current state**: `beer_factory` and `novisto_consumption` as they are in Postgres right now
  (inserts, updates and hard deletes applied), typically 2-10 s behind the source.
* **Metrics**: the dbt-built gold mart `daily_style_consumption`.
* **Operations**: health, per-table freshness and lineage.

Every response is computed from a single Iceberg snapshot; `meta.snapshot_id` / `meta.as_of` say which one.
"""


def create_app(settings: Settings | None = None, lakehouse: Lakehouse | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.lakehouse = lakehouse or Lakehouse(
            lambda: load_catalog("lakehouse", **settings.catalog_properties()),
            refresh_interval_s=settings.refresh_interval_s,
        )
        yield

    app = FastAPI(
        title="Brewery Lakehouse API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "Current state", "description": "Silver tables: current state of the source tables."},
            {"name": "Metrics", "description": "Gold tables built by dbt."},
            {"name": "Operations", "description": "Health and freshness."},
        ],
    )
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app


app = create_app()
