# Sample data

Synthetic, deterministic data loaded into Postgres by `db/init.sql` on first start.

## `beer_factory.csv` (30 rows): one row per production batch

| Column | Type | Notes |
|---|---|---|
| beer_id | int | Primary key |
| name | text | |
| style | text | IPA, Stout, Lager, ... |
| abv | numeric | Alcohol by volume, % |
| batch_size_l | int | Litres produced |
| produced_at | timestamptz | |
| factory_site | text | Montreal, Quebec City, Sherbrooke |
| updated_at | timestamptz | Last modification |

## `novisto_consumption.csv` (200 rows): one row per consumption event

| Column | Type | Notes |
|---|---|---|
| consumption_id | int | Primary key |
| beer_id | int | FK → `beer_factory.beer_id` |
| employee_team | text | |
| quantity_l | numeric | Litres consumed |
| consumed_at | timestamptz | |
| office | text | Montreal, Toronto, Paris, London |
| updated_at | timestamptz | Last modification |

Relationship: `novisto_consumption.beer_id` → `beer_factory.beer_id` (many-to-one).
