# AI usage

- **Tools used:** Claude Code (Anthropic) as a pair programmer in the terminal, with access to this repo
  and Docker.

- **What I delegated to AI:**
  - Most of the implementation code: ingestor, dbt project and runner, FastAPI service, demo script,
    Makefile, compose wiring, tests.
  - First drafts of the README, this document and DESIGN.md; the diagram generator.
  - Checking current library and image versions and their APIs (PyIceberg 0.12, DuckDB 2.0, arm64 image
    availability).

- **What I decided and drove:**
  - The architecture and the stack trade-offs: Debezium + Python/PyIceberg sink over Flink, because
    equality deletes are hard for light readers and schema changes should not need a job redeploy.
  - The quality bar: idempotent and duplicate-free, auto-generated API docs with examples, everything in
    Docker.
  - The order of work: one stage at a time (infra → ingestor → dbt → API → demo → docs), each reviewed,
    tested by me and committed before the next.

- **How I verified the result works:**
  - At every stage I ran the checks myself before committing: topic counts, `simulate_changes.sql`
    through Iceberg and then the API, restarts, the dbt quality gate.
  - Automated: 37 unit + integration tests against a real local Iceberg catalog, 23 dbt data tests, and
    `make demo`, which asserts every change through the API with a 60 s timeout.
  - Failure drills: `kill -9` mid-batch, deleted consumer group (full replay), connector restart,
    ingestor offline during writes, `make down/up`, a 60 s burst of writes. After each, counts and sums
    were reconciled against Postgres.
  - These drills found real bugs that AI-written code had not anticipated, and they were fixed:
    - the catalog's sqlite backend failing under concurrent commits, so it moved to Postgres;
    - an unpersisted source DB that would diverge from the lake after `down/up`;
    - a DuckDB file lock between dbt runs;
    - a wrong expectation in the demo check.
