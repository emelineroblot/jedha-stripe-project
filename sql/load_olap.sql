-- ============================================================
-- Chargement du star schema (data/olap/*.csv produits par build_olap.py)
-- En production : Kafka Connect → S3 → COPY Redshift, puis dbt.
-- ============================================================

\set ON_ERROR_STOP on

COPY dim_date           FROM '/data/olap/dim_date.csv'           CSV HEADER;
COPY dim_geography      FROM '/data/olap/dim_geography.csv'      CSV HEADER;
COPY dim_currency       FROM '/data/olap/dim_currency.csv'       CSV HEADER;
COPY dim_payment_method FROM '/data/olap/dim_payment_method.csv' CSV HEADER;
COPY dim_product        FROM '/data/olap/dim_product.csv'        CSV HEADER;
COPY dim_merchant       FROM '/data/olap/dim_merchant.csv'       CSV HEADER;
COPY dim_customer       FROM '/data/olap/dim_customer.csv'       CSV HEADER;
COPY exchange_rates     FROM '/data/olap/exchange_rates.csv'     CSV HEADER;
COPY fact_transactions  FROM '/data/olap/fact_transactions.csv'  CSV HEADER;
COPY fact_audit_events  FROM '/data/olap/fact_audit_events.csv'  CSV HEADER;
COPY agg_daily_revenue  FROM '/data/olap/agg_daily_revenue.csv'  CSV HEADER;
COPY agg_monthly_fraud  FROM '/data/olap/agg_monthly_fraud.csv'  CSV HEADER;

REFRESH MATERIALIZED VIEW mv_monthly_revenue_by_country;
ANALYZE;

SELECT 'fact_transactions' AS tbl, count(*) FROM fact_transactions
UNION ALL SELECT 'dim_merchant (dont SCD2 historiques)', count(*) FROM dim_merchant
UNION ALL SELECT 'dim_customer',       count(*) FROM dim_customer
UNION ALL SELECT 'dim_date',           count(*) FROM dim_date
UNION ALL SELECT 'agg_daily_revenue',  count(*) FROM agg_daily_revenue
UNION ALL SELECT 'agg_monthly_fraud',  count(*) FROM agg_monthly_fraud
UNION ALL SELECT 'fact_audit_events',  count(*) FROM fact_audit_events
UNION ALL SELECT 'mv_monthly_revenue_by_country', count(*) FROM mv_monthly_revenue_by_country;
