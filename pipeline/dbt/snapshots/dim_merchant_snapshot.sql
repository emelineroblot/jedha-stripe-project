-- SCD Type 2 géré par dbt : chaque changement de name / category / country_code / status
-- ferme la version courante (dbt_valid_to) et en ouvre une nouvelle.
-- dim_merchant est une vue sur ce snapshot : scd_start = dbt_valid_from, scd_end = dbt_valid_to,
-- is_current = (dbt_valid_to is null).
{% snapshot dim_merchant_snapshot %}
{{ config(
    target_schema = 'snapshots',
    unique_key    = 'merchant_id',
    strategy      = 'check',
    check_cols    = ['name', 'category', 'country_code', 'status']
) }}
select merchant_id, name, category, mcc, country_code, status, updated_at
from {{ source('oltp', 'merchants') }}
{% endsnapshot %}
