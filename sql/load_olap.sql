-- ============================================================
-- Chargement du star schema (data/olap/*.csv produits par build_olap.py)
-- \copy côté client, à exécuter depuis le dossier data/. Cible de conception : S3 → COPY Redshift, puis dbt.
-- ============================================================

\set ON_ERROR_STOP on

\copy dim_date FROM 'olap/dim_date.csv' CSV HEADER
\copy dim_geography FROM 'olap/dim_geography.csv' CSV HEADER
\copy dim_currency FROM 'olap/dim_currency.csv' CSV HEADER
\copy dim_payment_method FROM 'olap/dim_payment_method.csv' CSV HEADER
\copy dim_product FROM 'olap/dim_product.csv' CSV HEADER
\copy dim_merchant FROM 'olap/dim_merchant.csv' CSV HEADER
\copy dim_customer FROM 'olap/dim_customer.csv' CSV HEADER
\copy exchange_rates FROM 'olap/exchange_rates.csv' CSV HEADER
\copy fact_transactions FROM 'olap/fact_transactions.csv' CSV HEADER
\copy fact_audit_events FROM 'olap/fact_audit_events.csv' CSV HEADER
\copy agg_daily_revenue FROM 'olap/agg_daily_revenue.csv' CSV HEADER
\copy agg_monthly_fraud FROM 'olap/agg_monthly_fraud.csv' CSV HEADER

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
