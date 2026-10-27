-- Run against the source while your pipeline is running:
--   docker compose exec -T postgres psql -U novisto -d brewery < scripts/simulate_changes.sql
-- Every change below must be reflected through your API.

BEGIN;
-- INSERT: a new beer and some consumption of it
INSERT INTO beer_factory (name, style, abv, batch_size_l, produced_at, factory_site)
VALUES ('CDC Test Saison', 'Saison', 6.2, 800, now(), 'Montreal');

INSERT INTO novisto_consumption (beer_id, employee_team, quantity_l, consumed_at, office)
SELECT beer_id, 'Data', 0.5, now(), 'Montreal' FROM beer_factory WHERE name = 'CDC Test Saison';
COMMIT;

-- UPDATE: correct an ABV and a quantity
UPDATE beer_factory        SET abv = 9.9,        updated_at = now() WHERE beer_id = 1;
UPDATE novisto_consumption SET quantity_l = 2.0, updated_at = now() WHERE consumption_id = 10;

-- DELETE: remove a consumption event (hard delete)
DELETE FROM novisto_consumption WHERE consumption_id = 20;
