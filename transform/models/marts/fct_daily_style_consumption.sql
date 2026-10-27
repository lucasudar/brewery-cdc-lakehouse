{{ config(location=env_var('MART_OUTPUT_DIR', 'target/marts') ~ '/fct_daily_style_consumption.parquet') }}

-- Grain: one row per UTC day x beer style x producing factory site.
with consumption as (
    select * from {{ ref('stg_novisto_consumption') }}
),

beers as (
    select * from {{ ref('stg_beer_factory') }}
)

select
    c.consumption_date,
    coalesce(b.style, 'Unknown')                                    as style,
    coalesce(b.factory_site, 'Unknown')                             as factory_site,
    cast(sum(c.quantity_l) as decimal(18, 3))                       as litres_consumed,
    cast(sum(c.quantity_l * b.abv_pct / 100) as decimal(18, 4))     as pure_alcohol_litres,
    count(*)                                                        as consumption_events,
    count(distinct c.beer_id)                                       as distinct_beers,
    count(distinct c.employee_team)                                 as distinct_teams,
    greatest(max(c.cdc_source_ts), max(b.cdc_source_ts))            as last_source_change_at
from consumption as c
left join beers as b using (beer_id)
group by all
