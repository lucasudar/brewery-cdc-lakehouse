from datetime import date

from .conftest import BEERS, beer


def test_tables_not_created_yet_return_503(client):
    response = client.get("/v1/beers")
    assert response.status_code == 503
    assert "does not exist" in response.json()["detail"]


def test_list_beers_paginates_filters_and_hides_cdc_columns(client, lake):
    lake.seed()
    body = client.get("/v1/beers", params={"limit": 2}).json()
    assert [b["beer_id"] for b in body["data"]] == [1, 2]
    assert body["meta"]["total"] == 3 and body["meta"]["limit"] == 2
    assert body["meta"]["table"] == "silver.beer_factory" and body["meta"]["snapshot_id"]
    assert not any(k.startswith("_") for k in body["data"][0])
    assert body["data"][0]["abv"] == 5.5  # decimal -> JSON number

    stouts = client.get("/v1/beers", params={"style": "Stout"}).json()
    assert [b["beer_id"] for b in stouts["data"]] == [1]


def test_new_source_columns_are_passed_through(client, lake):
    lake.seed()
    assert client.get("/v1/beers/2").json()["data"]["ibu"] == 40


def test_get_beer_and_404(client, lake):
    lake.seed()
    assert client.get("/v1/beers/3").json()["data"]["name"] == "Beer #3"
    missing = client.get("/v1/beers/999")
    assert missing.status_code == 404 and missing.json() == {"detail": "beer 999 not found"}


def test_consumption_filters(client, lake):
    lake.seed()
    by_beer = client.get("/v1/consumption", params={"beer_id": 2}).json()
    assert [c["consumption_id"] for c in by_beer["data"]] == [2, 3]
    window = client.get(
        "/v1/consumption", params={"consumed_from": "2026-09-03T00:00:00Z", "consumed_to": "2026-09-05T00:00:00Z"}
    ).json()
    assert [c["consumption_id"] for c in window["data"]] == [2]
    assert client.get("/v1/consumption", params={"office": "London"}).json()["meta"]["total"] == 1
    bad = client.get("/v1/consumption", params={"consumed_from": "2026-09-05", "consumed_to": "2026-09-01"})
    assert bad.status_code == 422


def test_daily_metrics_newest_first_with_date_range(client, lake):
    lake.seed()
    body = client.get("/v1/metrics/daily-style-consumption").json()
    assert [r["consumption_date"] for r in body["data"]] == ["2026-09-05", "2026-09-03", "2026-09-02"]
    ranged = client.get(
        "/v1/metrics/daily-style-consumption",
        params={"date_from": "2026-09-03", "date_to": "2026-09-05", "style": "IPA"},
    ).json()
    assert ranged["meta"]["total"] == 2
    assert (
        client.get(
            "/v1/metrics/daily-style-consumption", params={"date_from": "2026-09-05", "date_to": "2026-09-01"}
        ).status_code
        == 422
    )


def test_reads_follow_new_snapshots(client, lake):
    lake.seed()
    first = client.get("/v1/beers").json()["meta"]["snapshot_id"]
    lake.write("silver.beer_factory", BEERS, [beer(1), beer(2)])  # e.g. beer 3 deleted upstream
    body = client.get("/v1/beers").json()
    assert body["meta"]["snapshot_id"] != first and body["meta"]["total"] == 2
    assert client.get("/v1/beers/3").status_code == 404


def test_freshness_reports_latency_and_gold_lineage(client, lake):
    lake.seed()
    tables = {t["table"]: t for t in client.get("/freshness").json()["tables"]}
    assert tables["gold.daily_style_consumption"]["up_to_date"] is True
    assert tables["silver.beer_factory"]["source_to_table_latency_s"] >= 0
    lake.write("silver.beer_factory", BEERS, [beer(1)])
    tables = {t["table"]: t for t in client.get("/freshness").json()["tables"]}
    assert tables["gold.daily_style_consumption"]["up_to_date"] is False


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "catalog": "reachable"}


def test_openapi_documents_examples_and_errors(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert {
        "/v1/beers",
        "/v1/beers/{beer_id}",
        "/v1/consumption",
        "/v1/consumption/{consumption_id}",
        "/v1/metrics/daily-style-consumption",
        "/freshness",
        "/health",
    } <= set(paths)
    assert "404" in paths["/v1/beers/{beer_id}"]["get"]["responses"]
    assert spec["components"]["schemas"]["Beer"]["examples"][0]["beer_id"] == 1
    assert date.fromisoformat(spec["components"]["schemas"]["DailyStyleConsumption"]["examples"][0]["consumption_date"])
