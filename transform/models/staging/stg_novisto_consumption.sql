select
    consumption_id,
    beer_id,
    employee_team,
    quantity_l,
    consumed_at,
    cast(timezone('UTC', consumed_at) as date) as consumption_date,
    office,
    updated_at,
    _source_ts                                 as cdc_source_ts
from {{ source('silver', 'novisto_consumption') }} {{ at_snapshot('novisto_consumption') }}
