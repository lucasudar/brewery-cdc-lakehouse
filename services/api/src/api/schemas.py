"""Response models. Examples here are what Swagger UI (/docs) and ReDoc (/redoc) render."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Exact decimals in storage, plain JSON numbers on the wire.
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


SNAPSHOT_EXAMPLE = 5208133962335491234
AS_OF_EXAMPLE = "2026-10-27T18:02:25.086000Z"


class ErrorResponse(BaseModel):
    detail: str = Field(examples=["beer 999 not found"])


class Meta(BaseModel):
    table: str = Field(description="Iceberg table the data was read from.", examples=["silver.beer_factory"])
    snapshot_id: int = Field(description="Iceberg snapshot every row of this response comes from.")
    as_of: datetime = Field(description="Commit time of that snapshot (UTC).")


class PageMeta(Meta):
    total: int = Field(description="Rows matching the filters (before pagination).", examples=[31])
    limit: int = Field(examples=[100])
    offset: int = Field(examples=[0])


class Page[T](BaseModel):
    data: list[T]
    meta: PageMeta


class Item[T](BaseModel):
    data: T
    meta: Meta


class Beer(BaseModel):
    """A production batch (current state of `beer_factory`).

    Columns added to the source table later (e.g. `ALTER TABLE ... ADD COLUMN ibu`) are passed
    through automatically as extra properties.
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "beer_id": 1,
                    "name": "Northern Stout #1",
                    "style": "Stout",
                    "abv": 9.9,
                    "batch_size_l": 500,
                    "produced_at": "2026-09-01T12:00:00Z",
                    "factory_site": "Sherbrooke",
                    "updated_at": "2026-10-27T18:16:25.104000Z",
                }
            ]
        },
    )

    beer_id: int
    name: str
    style: str = Field(description="IPA, Stout, Lager, ...")
    abv: Number = Field(description="Alcohol by volume, %.")
    batch_size_l: int = Field(description="Litres produced.")
    produced_at: datetime
    factory_site: str = Field(description="Montreal, Quebec City or Sherbrooke.")
    updated_at: datetime = Field(description="Last modification in the source.")


class Consumption(BaseModel):
    """A consumption event (current state of `novisto_consumption`; hard deletes are gone)."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "consumption_id": 10,
                    "beer_id": 15,
                    "employee_team": "Finance",
                    "quantity_l": 2.0,
                    "consumed_at": "2026-09-06T11:30:00Z",
                    "office": "London",
                    "updated_at": "2026-10-27T18:16:25.110000Z",
                }
            ]
        },
    )

    consumption_id: int
    beer_id: int = Field(description="FK to `beer_factory.beer_id`.")
    employee_team: str
    quantity_l: Number = Field(description="Litres consumed.")
    consumed_at: datetime
    office: str = Field(description="Montreal, Toronto, Paris or London.")
    updated_at: datetime


class DailyStyleConsumption(BaseModel):
    """One row per UTC day x beer style x factory site (gold mart built by dbt)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "consumption_date": "2026-10-27",
                    "style": "Saison",
                    "factory_site": "Montreal",
                    "litres_consumed": 0.5,
                    "pure_alcohol_litres": 0.031,
                    "consumption_events": 1,
                    "distinct_beers": 1,
                    "distinct_teams": 1,
                    "last_source_change_at": "2026-10-27T18:16:25.093000Z",
                }
            ]
        }
    )

    consumption_date: date
    style: str = Field(description="'Unknown' if the beer is not (yet) present in beer_factory.")
    factory_site: str
    litres_consumed: Number
    pure_alcohol_litres: Number = Field(description="sum(quantity_l * abv / 100).")
    consumption_events: int
    distinct_beers: int
    distinct_teams: int
    last_source_change_at: datetime | None = Field(description="Latest CDC change that contributed to this row.")


class TableFreshness(BaseModel):
    table: str = Field(examples=["silver.novisto_consumption"])
    snapshot_id: int | None = Field(description="Current snapshot; null if the table is not created yet.")
    committed_at: datetime | None = Field(description="When the current snapshot was committed.")
    last_source_change_at: datetime | None = Field(
        description="Source (Postgres) commit time of the newest change included in the snapshot."
    )
    source_to_table_latency_s: float | None = Field(
        description="committed_at - last_source_change_at: end-to-end latency of the newest change.",
        examples=[2.6],
    )
    source_snapshots: dict[str, int] | None = Field(
        default=None, description="Gold only: silver snapshot ids the mart was built from (lineage)."
    )
    up_to_date: bool | None = Field(
        default=None, description="Gold only: true if built from the current silver snapshots."
    )


class Freshness(BaseModel):
    checked_at: datetime
    tables: list[TableFreshness]


class Health(BaseModel):
    status: str = Field(examples=["ok"])
    catalog: str = Field(examples=["reachable"])
