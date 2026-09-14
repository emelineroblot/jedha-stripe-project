-- Enrichissement : taux de change du jour, fees, refunds, disputes, fraude.
with tx as (select * from {{ ref('stg_transactions') }}),
rates as (select * from {{ ref('stg_exchange_rates') }}),
refunds as (
    select transaction_id, sum(amount) as refund_amount
    from {{ ref('stg_refunds') }} where status = 'succeeded' group by 1
),
disputes as (select distinct transaction_id from {{ ref('stg_disputes') }}),
fraud as (select transaction_id, anomaly_score, risk_level from {{ ref('stg_fraud_indicators') }})
select
    tx.*,
    round(tx.amount * r.rate_to_usd, 2)                                        as amount_usd,
    case when tx.status = 'succeeded'
         then round(tx.amount * r.rate_to_usd * 0.029 + 0.30, 2) else 0 end     as fee_usd,
    coalesce(round(rf.refund_amount * r.rate_to_usd, 2), 0)                    as refund_amount_usd,
    rf.transaction_id is not null                                              as is_refunded,
    d.transaction_id is not null                                               as is_disputed,
    coalesce(f.risk_level in ('high', 'critical'), false)                      as is_fraud_flagged,
    tx.subscription_id is not null                                             as is_subscription,
    f.anomaly_score,
    f.risk_level
from tx
join rates r on r.currency_code = tx.currency_code and r.rate_date = tx.created_at::date
left join refunds  rf on rf.transaction_id = tx.transaction_id
left join disputes d  on d.transaction_id  = tx.transaction_id
left join fraud    f  on f.transaction_id  = tx.transaction_id
