-- ============================================================
-- Chargement des CSV synthétiques dans l'OLTP (PostgreSQL)
-- \copy (côté client) : fonctionne en local (docker exec -w /data) comme vers RDS.
-- À exécuter depuis le dossier data/ (chemins relatifs). Ordre = dépendances de clés étrangères
-- ============================================================

\set ON_ERROR_STOP on

\copy countries FROM 'countries.csv' CSV HEADER
\copy currencies FROM 'currencies.csv' CSV HEADER
\copy exchange_rates FROM 'exchange_rates.csv' CSV HEADER
\copy merchants FROM 'merchants.csv' CSV HEADER
\copy customers FROM 'customers.csv' CSV HEADER
\copy payment_methods FROM 'payment_methods.csv' CSV HEADER
\copy products FROM 'products.csv' CSV HEADER
\copy subscriptions FROM 'subscriptions.csv' CSV HEADER
\copy transactions FROM 'transactions.csv' CSV HEADER
\copy refunds FROM 'refunds.csv' CSV HEADER
\copy disputes FROM 'disputes.csv' CSV HEADER
\copy fraud_indicators FROM 'fraud_indicators.csv' CSV HEADER
\copy audit_logs FROM 'audit_logs.csv' CSV HEADER

ANALYZE;

SELECT 'countries' AS tbl, count(*) FROM countries
UNION ALL SELECT 'merchants',        count(*) FROM merchants
UNION ALL SELECT 'customers',        count(*) FROM customers
UNION ALL SELECT 'payment_methods',  count(*) FROM payment_methods
UNION ALL SELECT 'products',         count(*) FROM products
UNION ALL SELECT 'subscriptions',    count(*) FROM subscriptions
UNION ALL SELECT 'transactions',     count(*) FROM transactions
UNION ALL SELECT 'refunds',          count(*) FROM refunds
UNION ALL SELECT 'disputes',         count(*) FROM disputes
UNION ALL SELECT 'fraud_indicators', count(*) FROM fraud_indicators
UNION ALL SELECT 'audit_logs',       count(*) FROM audit_logs
UNION ALL SELECT 'exchange_rates',   count(*) FROM exchange_rates;
