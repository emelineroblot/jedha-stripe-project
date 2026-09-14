{{ config(
    materialized = 'incremental',
    unique_key   = 'transaction_id',          -- MERGE : un rejeu ne crée jamais de doublon
    incremental_strategy = 'merge',
    dist = 'merchant_sk',
    sort = 'date_sk'
) }}

-- Résolution des surrogate keys : la version SCD2 valide À LA DATE DE LA TRANSACTION
with e as (select * from {{ ref('int_transactions_enriched') }})
select
    {{ dbt_utils.generate_surrogate_key(['e.transaction_id']) }} as transaction_sk,
    e.transaction_id,
    to_char(e.created_at, 'YYYYMMDD')::int                      as date_sk,
    extract(hour from e.created_at)::smallint                   as hour_of_day,
    m.merchant_sk,
    c.customer_sk,
    g.geography_sk,
    pm.payment_method_sk,
    p.product_sk,
    cur.currency_sk,
    e.status, e.amount, e.amount_usd, e.fee_usd,
    e.amount_usd - e.fee_usd - e.refund_amount_usd              as net_amount_usd,
    e.refund_amount_usd, e.is_refunded, e.is_disputed, e.is_fraud_flagged, e.is_subscription,
    e.anomaly_score, e.risk_level, e.device_type,
    current_timestamp                                           as loaded_at
from e
join {{ ref('dim_merchant') }} m
  on m.merchant_id = e.merchant_id
 and e.created_at::date >= m.scd_start and e.created_at::date < coalesce(m.scd_end, '9999-12-31')
join {{ ref('dim_customer') }} c
  on c.customer_id = e.customer_id
 and e.created_at::date >= c.scd_start and e.created_at::date < coalesce(c.scd_end, '9999-12-31')
join {{ ref('dim_geography') }}      g   on g.country_code = e.geo_country_code
join {{ ref('dim_payment_method') }} pm  on pm.payment_method_id = e.payment_method_id
left join {{ ref('dim_product') }}   p   on p.product_id = e.product_id
join {{ ref('dim_currency') }}       cur on cur.currency_code = e.currency_code
