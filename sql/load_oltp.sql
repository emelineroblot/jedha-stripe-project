-- ============================================================
-- Chargement des CSV synthétiques dans l'OLTP (PostgreSQL)
-- /data est monté depuis ./data dans docker/dev/docker-compose.yml
-- Ordre = dépendances de clés étrangères
-- ============================================================

\set ON_ERROR_STOP on

COPY countries        FROM '/data/countries.csv'        CSV HEADER;
COPY currencies       FROM '/data/currencies.csv'       CSV HEADER;
COPY exchange_rates   FROM '/data/exchange_rates.csv'   CSV HEADER;
COPY merchants        FROM '/data/merchants.csv'        CSV HEADER;
COPY customers        FROM '/data/customers.csv'        CSV HEADER;
COPY payment_methods  FROM '/data/payment_methods.csv'  CSV HEADER;
COPY products         FROM '/data/products.csv'         CSV HEADER;
COPY subscriptions    FROM '/data/subscriptions.csv'    CSV HEADER;
COPY transactions     FROM '/data/transactions.csv'     CSV HEADER;
COPY refunds          FROM '/data/refunds.csv'          CSV HEADER;
COPY disputes         FROM '/data/disputes.csv'         CSV HEADER;
COPY fraud_indicators FROM '/data/fraud_indicators.csv' CSV HEADER;
COPY audit_logs       FROM '/data/audit_logs.csv'       CSV HEADER;

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
