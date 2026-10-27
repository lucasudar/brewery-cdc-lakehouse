-- Audit before publish: the mart must account for every litre and every event in staging.
with mart as (
    select coalesce(sum(litres_consumed), 0) as litres, coalesce(sum(consumption_events), 0) as events
    from {{ ref('fct_daily_style_consumption') }}
),
staging as (
    select coalesce(sum(quantity_l), 0) as litres, count(*) as events
    from {{ ref('stg_novisto_consumption') }}
)
select mart.litres as mart_litres, staging.litres as staging_litres, mart.events as mart_events, staging.events as staging_events
from mart, staging
where mart.litres <> staging.litres or mart.events <> staging.events
