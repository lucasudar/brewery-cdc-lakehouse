# Novisto: Senior Data Engineer Take-Home

**Time budget: about 2 hours.** We care about your judgment, not how many lines you typed.

## The problem

Build a small **near-real-time CDC pipeline**:

```
Postgres (source) ──CDC──▶ Open table format (Iceberg) ──▶ Transformation ──▶ REST or GraphQL API
```

We provide a Postgres database with two tables (`beer_factory`, `novisto_consumption`). Changes made in Postgres should flow into a lakehouse table format and be queryable through an API you expose.

## What's provided

```
docker-compose.yml          Postgres 16 with wal_level=logical, seeded on first start
db/init.sql                 Schema + seed (REPLICA IDENTITY FULL already set)
data/                       CSV sample data + data dictionary
scripts/simulate_changes.sql  INSERT / UPDATE / DELETE used to test your pipeline
```

```bash
docker compose up -d
docker compose exec postgres psql -U novisto -d brewery -c "select count(*) from beer_factory;"
```

## Requirements

1. **CDC ingestion.** Capture inserts, **updates and deletes** from both tables.
2. **Open table format.** Land the data in **Apache Iceberg** (Delta or Hudi are acceptable if you justify the choice), on local disk or local object storage (e.g. MinIO).
3. **Transformation.** At least one modelled table, e.g. litres consumed per beer style per day, joined with factory data.
4. **Query API.** REST or GraphQL with endpoints for the current state of each source table and the transformed table.
5. **Freshness.** After running `scripts/simulate_changes.sql`, the changes are visible through the API within **~60 seconds**.
6. **One-command run.** `make up` (or `docker compose up`) starts everything; `make demo` (or a documented script) shows the changes flowing end to end.

### Stack

**Your choice.** Any tool or language is fine (Debezium, Kafka/Redpanda, Flink, Spark, RisingWave, PyIceberg, DuckDB, dbt, FastAPI, ...), under these constraints:

- **Open-source only, runs fully locally.** No SaaS, no cloud accounts, no free tiers.
- It must run on our machine: **macOS on Apple Silicon (arm64), 16 GB RAM, Docker Desktop.** Mind image architectures and memory use.
- Pin your versions.

### Out of scope

Auth, cloud deployment, UI, high test coverage, production hardening. **A small pipeline that works beats an ambitious one that's broken.**

## Deliverables

1. **A git repository** (link to a public or private repo, or a zip) with a README covering how to run and how to demo.
2. **`docs/DESIGN.md`**.
3. **`docs/AI_USAGE.md`**.

## What happens next

We'll run your repo on our laptop, then hold a **live session** where you walk us through it and we change things together, e.g. restart components or alter the schema.
