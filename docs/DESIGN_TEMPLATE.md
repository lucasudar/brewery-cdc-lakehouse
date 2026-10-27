# Design: <your name>

> Copy to `docs/DESIGN.md`. Maximum 2 pages. Bullet points are fine.

## 1. Architecture
Diagram (ASCII, Mermaid or image) and one paragraph on the data flow.

## 2. Stack choices
For each component: what you chose, why, and what you considered and rejected.

## 3. CDC semantics
- How inserts, updates and deletes are represented in the table format (merge-on-read, copy-on-write, equality deletes, upserts, ...).
- Delivery guarantees (at-least-once, exactly-once?) and how duplicates or out-of-order events are handled.
- What happens when a component restarts.

## 4. Freshness vs. cost
Commit interval, small-file problem, compaction and snapshot expiry. What latency did you measure?

## 5. Transformation layer
What you modelled and why. How it is refreshed (incremental or full).

## 6. Known limitations
What doesn't work or would break (schema change, large volumes, backfill, ...).

## 7. Path to production
What you would change for a multi-source, multi-tenant production lakehouse: orchestration, observability, data contracts, security.
