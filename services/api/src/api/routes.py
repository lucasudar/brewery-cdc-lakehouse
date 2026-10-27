from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Annotated, Any

import pyarrow as pa
import pyarrow.compute as pc
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.config import Settings
from api.lakehouse import Lakehouse, TableNotReady, TableSnapshot
from api.schemas import (
    Beer,
    Consumption,
    DailyStyleConsumption,
    ErrorResponse,
    Freshness,
    Health,
    Item,
    Meta,
    Page,
    PageMeta,
    TableFreshness,
)

SOURCE_TS_PROP = "ingestor.max-source-ts-ms"
LINEAGE_PROP = "transform.source-snapshots"

NOT_READY = {503: {"model": ErrorResponse, "description": "Table not created yet (pipeline still starting)."}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "No row with this primary key in the current snapshot."}}

Limit = Annotated[int, Query(ge=1, le=1000, description="Page size.")]
Offset = Annotated[int, Query(ge=0, description="Rows to skip.")]

router = APIRouter()


def lakehouse(request: Request) -> Lakehouse:
    return request.app.state.lakehouse


def settings(request: Request) -> Settings:
    return request.app.state.settings


def _snapshot(lh: Lakehouse, name: str) -> TableSnapshot:
    try:
        return lh.snapshot(name)
    except TableNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Hide CDC bookkeeping columns (_op, _lsn, ...) from API consumers."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _meta(snap: TableSnapshot) -> Meta:
    return Meta(table=snap.name, snapshot_id=snap.snapshot_id, as_of=snap.committed_at)


def _page(snap: TableSnapshot, rows: list[dict[str, Any]], total: int, limit: int, offset: int) -> dict[str, Any]:
    meta = PageMeta(**_meta(snap).model_dump(), total=total, limit=limit, offset=offset)
    return {"data": [_public(r) for r in rows], "meta": meta}


def _eq(column: str, value: Any) -> pc.Expression:
    return pc.field(column) == value


def _ts(value: datetime) -> pa.Scalar:
    return pa.scalar(value if value.tzinfo else value.replace(tzinfo=UTC), type=pa.timestamp("us", tz="UTC"))


# --------------------------------------------------------------------------- current state


@router.get(
    "/v1/beers",
    response_model=Page[Beer],
    tags=["Current state"],
    summary="List beers (production batches)",
    responses=NOT_READY,
)
def list_beers(
    lh: Annotated[Lakehouse, Depends(lakehouse)],
    cfg: Annotated[Settings, Depends(settings)],
    style: Annotated[str | None, Query(examples=["Stout"])] = None,
    factory_site: Annotated[str | None, Query(examples=["Montreal"])] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    snap = _snapshot(lh, cfg.beers_table)
    filters = [_eq(c, v) for c, v in (("style", style), ("factory_site", factory_site)) if v is not None]
    rows, total = snap.select(filters, [("beer_id", "ascending")], limit, offset)
    return _page(snap, rows, total, limit, offset)


@router.get(
    "/v1/beers/{beer_id}",
    response_model=Item[Beer],
    tags=["Current state"],
    summary="Get one beer",
    responses={**NOT_FOUND, **NOT_READY},
)
def get_beer(
    beer_id: int, lh: Annotated[Lakehouse, Depends(lakehouse)], cfg: Annotated[Settings, Depends(settings)]
) -> dict[str, Any]:
    snap = _snapshot(lh, cfg.beers_table)
    rows, _ = snap.select([_eq("beer_id", beer_id)])
    if not rows:
        raise HTTPException(status_code=404, detail=f"beer {beer_id} not found")
    return {"data": _public(rows[0]), "meta": _meta(snap)}


@router.get(
    "/v1/consumption",
    response_model=Page[Consumption],
    tags=["Current state"],
    summary="List consumption events",
    responses={**NOT_READY, 422: {"model": ErrorResponse, "description": "Invalid filter combination."}},
)
def list_consumption(
    lh: Annotated[Lakehouse, Depends(lakehouse)],
    cfg: Annotated[Settings, Depends(settings)],
    beer_id: int | None = None,
    office: Annotated[str | None, Query(examples=["Montreal"])] = None,
    employee_team: Annotated[str | None, Query(examples=["Data"])] = None,
    consumed_from: Annotated[datetime | None, Query(description="Inclusive, ISO 8601.")] = None,
    consumed_to: Annotated[datetime | None, Query(description="Exclusive, ISO 8601.")] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    if consumed_from and consumed_to and consumed_from >= consumed_to:
        raise HTTPException(status_code=422, detail="consumed_from must be before consumed_to")
    snap = _snapshot(lh, cfg.consumption_table)
    filters = [_eq(c, v) for c, v in (("beer_id", beer_id), ("office", office), ("employee_team", employee_team)) if v]
    if consumed_from:
        filters.append(pc.field("consumed_at") >= _ts(consumed_from))
    if consumed_to:
        filters.append(pc.field("consumed_at") < _ts(consumed_to))
    rows, total = snap.select(filters, [("consumption_id", "ascending")], limit, offset)
    return _page(snap, rows, total, limit, offset)


@router.get(
    "/v1/consumption/{consumption_id}",
    response_model=Item[Consumption],
    tags=["Current state"],
    summary="Get one consumption event",
    responses={**NOT_FOUND, **NOT_READY},
)
def get_consumption(
    consumption_id: int, lh: Annotated[Lakehouse, Depends(lakehouse)], cfg: Annotated[Settings, Depends(settings)]
) -> dict[str, Any]:
    snap = _snapshot(lh, cfg.consumption_table)
    rows, _ = snap.select([_eq("consumption_id", consumption_id)])
    if not rows:
        raise HTTPException(status_code=404, detail=f"consumption {consumption_id} not found")
    return {"data": _public(rows[0]), "meta": _meta(snap)}


# --------------------------------------------------------------------------- metrics


@router.get(
    "/v1/metrics/daily-style-consumption",
    response_model=Page[DailyStyleConsumption],
    tags=["Metrics"],
    summary="Litres consumed per day, beer style and factory site",
    description="Gold mart built by dbt from the silver tables; newest days first.",
    responses={**NOT_READY, 422: {"model": ErrorResponse, "description": "Invalid date range."}},
)
def daily_style_consumption(
    lh: Annotated[Lakehouse, Depends(lakehouse)],
    cfg: Annotated[Settings, Depends(settings)],
    date_from: Annotated[date | None, Query(description="Inclusive.", examples=["2026-09-01"])] = None,
    date_to: Annotated[date | None, Query(description="Inclusive.", examples=["2026-10-31"])] = None,
    style: Annotated[str | None, Query(examples=["IPA"])] = None,
    factory_site: Annotated[str | None, Query(examples=["Sherbrooke"])] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    snap = _snapshot(lh, cfg.daily_style_table)
    filters = [_eq(c, v) for c, v in (("style", style), ("factory_site", factory_site)) if v is not None]
    if date_from:
        filters.append(pc.field("consumption_date") >= pa.scalar(date_from))
    if date_to:
        filters.append(pc.field("consumption_date") <= pa.scalar(date_to))
    order = [("consumption_date", "descending"), ("style", "ascending"), ("factory_site", "ascending")]
    rows, total = snap.select(filters, order, limit, offset)
    return _page(snap, rows, total, limit, offset)


# --------------------------------------------------------------------------- operations


def _freshness(lh: Lakehouse, name: str) -> TableFreshness:
    try:
        snap = lh.snapshot(name)
    except TableNotReady:
        return TableFreshness(
            table=name, snapshot_id=None, committed_at=None, last_source_change_at=None, source_to_table_latency_s=None
        )
    source_ms = int(snap.summary.get(SOURCE_TS_PROP, 0) or 0)
    source_at = datetime.fromtimestamp(source_ms / 1000, UTC) if source_ms else None
    lineage = json.loads(snap.summary[LINEAGE_PROP]) if LINEAGE_PROP in snap.summary else None
    return TableFreshness(
        table=name,
        snapshot_id=snap.snapshot_id,
        committed_at=snap.committed_at,
        last_source_change_at=source_at,
        source_to_table_latency_s=round((snap.committed_at - source_at).total_seconds(), 3) if source_at else None,
        source_snapshots=lineage,
    )


@router.get(
    "/freshness",
    response_model=Freshness,
    tags=["Operations"],
    summary="Pipeline freshness and lineage per table",
)
def freshness(lh: Annotated[Lakehouse, Depends(lakehouse)], cfg: Annotated[Settings, Depends(settings)]) -> Freshness:
    silver = {name: _freshness(lh, name) for name in (cfg.beers_table, cfg.consumption_table)}
    gold = _freshness(lh, cfg.daily_style_table)
    if gold.source_snapshots is not None:
        current = {name.split(".", 1)[1]: f.snapshot_id for name, f in silver.items()}
        gold.up_to_date = gold.source_snapshots == current
    return Freshness(checked_at=datetime.now(UTC), tables=[*silver.values(), gold])


@router.get(
    "/health",
    response_model=Health,
    tags=["Operations"],
    summary="Liveness + catalog reachability",
    responses={503: {"model": ErrorResponse, "description": "Iceberg catalog unreachable."}},
)
def health(lh: Annotated[Lakehouse, Depends(lakehouse)]) -> Health:
    try:
        lh.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"catalog unreachable: {exc}") from exc
    return Health(status="ok", catalog="reachable")
