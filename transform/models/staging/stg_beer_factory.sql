-- Explicit column list = contract: new source columns don't leak into downstream models unannounced.
select
    beer_id,
    name                          as beer_name,
    style,
    abv                           as abv_pct,
    batch_size_l,
    produced_at,
    factory_site,
    updated_at,
    _source_ts                    as cdc_source_ts
from {{ source('silver', 'beer_factory') }} {{ at_snapshot('beer_factory') }}
