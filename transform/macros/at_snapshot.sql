{#- Pin an Iceberg source to the snapshot the runner resolved (time travel), or read the current one. -#}
{% macro at_snapshot(table_name) -%}
  {%- set snapshot_id = var(table_name ~ '_snapshot_id', '') -%}
  {%- if snapshot_id | string | length > 0 -%} at (version => {{ snapshot_id }}) {%- endif -%}
{%- endmacro %}
