# Brewery lakehouse: near-real-time CDC

Postgres changes (inserts, updates, hard deletes) stream through **Debezium → Redpanda** into
**Apache Iceberg** (bronze changelog + silver current state). **dbt** builds a gold mart from them, and
**FastAPI** serves all of it with auto-generated OpenAPI docs. Measured end to end: **≈ 5–7 s** from a
Postgres commit to the API, against a 60 s target.

![Architecture](docs/diagrams/1-architecture.png)

- Design and trade-offs: [docs/DESIGN.md](docs/DESIGN.md)
- How a change is applied exactly once and recovered after a crash: [diagram](docs/diagrams/2-exactly-once.png)
- AI usage: [docs/AI_USAGE.md](docs/AI_USAGE.md)
- The original assignment: [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md)

## Run it

Requirements: **Docker Desktop and `make`**. Nothing else on the host: tests, lint and diagrams also run
in containers. Images are multi-arch (arm64 + amd64) and pinned. The stack uses about **2.1 GB RAM**.

From a clean state, one line:

```bash
make reset && make up && make demo
```

- `make reset` wipes any previous state (containers and volumes), so the demo starts from the seed data.
- `make up` builds and starts everything and returns once all services are healthy (about 40 s; the
  first run also pulls and builds images).
- `make demo` applies `scripts/simulate_changes.sql` and watches every change reach the API.

Expected output:

```
==> Polling the API until every change is visible
  ✔ INSERT beer 'CDC Test Saison'        (silver.beer_factory)  visible after   3.8s
  ✔ UPDATE beer 1 abv -> 9.9             (silver.beer_factory)  visible after   3.8s
  ✔ INSERT its consumption, 0.5 l        (silver.novisto_consumption)  visible after   4.3s
  ✔ UPDATE consumption 10 -> 2.0 l       (silver.novisto_consumption)  visible after   4.3s
  ✔ DELETE consumption 20 -> 404         (silver.novisto_consumption)  visible after   4.3s
  ✔ Saison litres up in today's mart     (gold.daily_style_consumption)  visible after   7.0s

End to end: every change visible through the API after 7.0s (target: 60s)
```

The demo exits non-zero if any change is not visible within 60 s. Running `make demo` again without
`make reset` also passes, but the UPDATE/DELETE checks then show `0.0s`: those rows already have the new
values from the previous run. Always measure latency from a fresh state.

| Target | What it does |
|---|---|
| `make up` / `make down` | start / stop; `down` keeps all state (Postgres, topics, offsets, Iceberg) |
| `make reset` | stop and wipe every volume; Postgres re-seeds from `data/` on the next `up` |
| `make demo` | end-to-end demo with latency measurement |
| `make test` | 37 unit + integration tests (ingestor, API) against a real local Iceberg catalog |
| `make lint` | ruff lint + format check for all Python projects |
| `make dbt-build` | run the dbt models + 23 data tests in the transformer container |
| `make diagrams` | regenerate `docs/diagrams/*.png` from `gen.py` (draw.io, with a layout check) |
| `make ps` / `make logs` / `make topics` | status, logs, Kafka topics |

## API

Swagger UI: **http://localhost:8000/docs** · ReDoc: http://localhost:8000/redoc · spec: `/openapi.json`

| Endpoint | Returns |
|---|---|
| `GET /v1/beers` (`style`, `factory_site`, `limit`, `offset`) | current state of `beer_factory` |
| `GET /v1/beers/{beer_id}` | one beer, 404 if it does not exist |
| `GET /v1/consumption` (`beer_id`, `office`, `employee_team`, `consumed_from`, `consumed_to`, paging) | current state of `novisto_consumption` (deleted rows are gone) |
| `GET /v1/consumption/{consumption_id}` | one event, 404 if deleted or unknown |
| `GET /v1/metrics/daily-style-consumption` (`date_from`, `date_to`, `style`, `factory_site`, paging) | gold mart: litres per UTC day × style × factory site |
| `GET /freshness` | per table: snapshot, commit time, source→table latency; gold lineage and `up_to_date` |
| `GET /health` | liveness + catalog reachability |

Every response is computed from a single Iceberg snapshot and says which one:

```bash
$ curl -s localhost:8000/v1/beers/1
{"data":{"beer_id":1,"name":"Northern Stout #1","style":"Stout","abv":9.9,"batch_size_l":500,
 "produced_at":"2026-09-01T12:00:00Z","factory_site":"Sherbrooke","updated_at":"2026-10-27T18:44:07.360Z"},
 "meta":{"table":"silver.beer_factory","snapshot_id":6911727836290382096,"as_of":"2026-10-27T18:44:12.283Z"}}
```

## What runs

| Service | Image / code | Port | Role |
|---|---|---|---|
| `postgres` | `postgres:16` | 5432 | source (`wal_level=logical`) |
| `redpanda` | `redpandadata/redpanda:v25.3.18` | 19092 | Kafka API log: CDC topics, DLQ, Connect state |
| `connect` + `connect-init` | `quay.io/debezium/connect:3.3.1.Final` | 8083 | Debezium Postgres connector, registered idempotently (`PUT`) |
| `seaweedfs` | `chrislusf/seaweedfs:4.51` | 8333 | S3 object storage, bucket `warehouse` |
| `catalog-db` + `iceberg-rest` | `postgres:16`, `apache/iceberg-rest-fixture:1.10.1` + pgJDBC 42.7.14 | 8181 | Iceberg REST catalog on a Postgres JDBC backend |
| `ingestor` | `services/ingestor` (Python 3.12, PyIceberg 0.12) | – | Kafka → Iceberg bronze + silver |
| `transformer` | `transform/` (dbt-core 1.12, dbt-duckdb 1.11, DuckDB 2.0) | – | silver → gold on every new snapshot |
| `api` | `services/api` (FastAPI 0.162, Pydantic 2) | 8000 | REST API |

```
services/ingestor/   envelope.py (Debezium → typed rows) · batch.py (replay skip, dedup, LSN guard)
                     iceberg_writer.py (commits, schema evolution, maintenance) · main.py (consumer loop, DLQ)
transform/           dbt project (models/, tests/, macros/) + src/transformer/runner.py (write-audit-publish)
services/api/        lakehouse.py (snapshot-cached reads) · schemas.py (models + examples) · routes.py
infra/               Debezium connector config, SeaweedFS credentials, REST catalog image
scripts/             simulate_changes.sql (given) · demo.sh + demo_check.py
docs/                DESIGN.md · AI_USAGE.md · diagrams/ (gen.py → architecture.drawio → PNGs)
```

## Things to try live

```bash
# Restart anything: progress lives in Iceberg snapshots and Kafka, so nothing is lost or duplicated
docker compose restart connect ingestor transformer api && make demo
docker compose kill -s SIGKILL ingestor && docker compose up -d ingestor

# Schema change: the new column is in Iceberg and in the API a few seconds later
docker compose exec postgres psql -U novisto -d brewery \
  -c "ALTER TABLE beer_factory ADD COLUMN ibu INTEGER" -c "UPDATE beer_factory SET ibu = 40 WHERE beer_id = 2"
curl -s localhost:8000/v1/beers/2

# Data quality gate: a negative quantity fails a dbt test, gold keeps serving the last good version
docker compose exec postgres psql -U novisto -d brewery -c "UPDATE novisto_consumption SET quantity_l = -1 WHERE consumption_id = 11"
docker compose logs transformer | grep 'not published'

# Look inside
curl -s localhost:8000/freshness
docker compose exec redpanda rpk topic consume cdc.public.beer_factory -o -1 -n 1
docker compose logs ingestor | grep 'batch committed'
```

## Troubleshooting

- **Port already in use** (5432, 8000, 8083, 8181, 8333, 19092): stop the other process or change the
  host port in `docker-compose.yml`.
- **`make up` times out on the first run**: images are still downloading or building. Run it again;
  it is idempotent.
- **Start from scratch**: `make reset && make up`.
- **Something is unhealthy**: `make ps`, then `docker compose logs <service>`. The ingestor and
  transformer log one JSON line per batch or publish.
