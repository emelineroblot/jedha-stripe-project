-- Staging : événements CDC bruts (Kafka -> S3 -> COPY) -> lignes typées et dédoublonnées.
-- Un même transaction_id peut arriver plusieurs fois (rejeu Kafka, UPDATE) :
-- on garde la version au LSN le plus élevé (dernier état connu).
with raw as (
    select
        payload:after:transaction_id::varchar      as transaction_id,
        payload:after:merchant_id::varchar         as merchant_id,
        payload:after:customer_id::varchar         as customer_id,
        payload:after:payment_method_id::varchar   as payment_method_id,
        payload:after:product_id::varchar          as product_id,
        payload:after:subscription_id::varchar     as subscription_id,
        payload:after:amount::numeric(14,2)        as amount,
        payload:after:currency_code::varchar(3)    as currency_code,
        payload:after:status::varchar              as status,
        payload:after:geo_country_code::varchar(2) as geo_country_code,
        payload:after:device_type::varchar         as device_type,
        payload:after:created_at::timestamp        as created_at,
        payload:source:lsn::bigint                 as lsn,
        payload:op::varchar                        as op
    from {{ source('cdc', 'raw_transactions') }}
    {% if is_incremental() %}
      where ingested_at > (select max(loaded_at) from {{ this }})
    {% endif %}
)
select *
from raw
where op <> 'd'
qualify row_number() over (partition by transaction_id order by lsn desc) = 1
