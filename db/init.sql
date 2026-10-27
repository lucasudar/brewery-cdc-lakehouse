-- Source schema, seeded from ../data/*.csv on first start.
-- wal_level=logical is set in docker-compose.yml.

CREATE TABLE beer_factory (
    beer_id       INTEGER PRIMARY KEY,
    name          TEXT        NOT NULL,
    style         TEXT        NOT NULL,
    abv           NUMERIC(3,1) NOT NULL,
    batch_size_l  INTEGER     NOT NULL,
    produced_at   TIMESTAMPTZ NOT NULL,
    factory_site  TEXT        NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE novisto_consumption (
    consumption_id INTEGER PRIMARY KEY,
    beer_id        INTEGER     NOT NULL REFERENCES beer_factory (beer_id),
    employee_team  TEXT        NOT NULL,
    quantity_l     NUMERIC(5,3) NOT NULL,
    consumed_at    TIMESTAMPTZ NOT NULL,
    office         TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Full before-images on UPDATE/DELETE so CDC consumers receive the whole row.
ALTER TABLE beer_factory        REPLICA IDENTITY FULL;
ALTER TABLE novisto_consumption REPLICA IDENTITY FULL;

COPY beer_factory        FROM '/data/beer_factory.csv'        WITH (FORMAT csv, HEADER true);
COPY novisto_consumption FROM '/data/novisto_consumption.csv' WITH (FORMAT csv, HEADER true);

-- Let new inserts continue after the seeded ids.
CREATE SEQUENCE beer_factory_id_seq        OWNED BY beer_factory.beer_id;
CREATE SEQUENCE novisto_consumption_id_seq OWNED BY novisto_consumption.consumption_id;
SELECT setval('beer_factory_id_seq',        (SELECT max(beer_id) FROM beer_factory));
SELECT setval('novisto_consumption_id_seq', (SELECT max(consumption_id) FROM novisto_consumption));
ALTER TABLE beer_factory        ALTER COLUMN beer_id        SET DEFAULT nextval('beer_factory_id_seq');
ALTER TABLE novisto_consumption ALTER COLUMN consumption_id SET DEFAULT nextval('novisto_consumption_id_seq');
