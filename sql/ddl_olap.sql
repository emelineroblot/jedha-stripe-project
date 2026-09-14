-- ============================================================
-- STRIPE — DDL OLAP (star schema)
-- Cible de production : Amazon Redshift.
-- Ce fichier est exécutable sur PostgreSQL (démo locale) ; les
-- clauses spécifiques Redshift sont indiquées en commentaire
-- « -- Redshift: ... » à côté de chaque table.
-- Miroir exécutable de schemas/olap_dbdiagram.txt
-- ============================================================

BEGIN;

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

-- ───────────────────────── Dimensions (Redshift: DISTSTYLE ALL) ─────────────────────────
-- Petites tables répliquées sur chaque nœud → jointures locales, aucun shuffle réseau.

CREATE TABLE dim_date (
    date_sk        int      PRIMARY KEY,          -- YYYYMMDD
    full_date      date     NOT NULL UNIQUE,
    year           int      NOT NULL,
    quarter        int      NOT NULL,
    month          int      NOT NULL,
    month_name     varchar  NOT NULL,
    week_of_year   int      NOT NULL,
    day            int      NOT NULL,
    day_of_week    int      NOT NULL,             -- 1 = lundi
    day_name       varchar  NOT NULL,
    is_weekend     boolean  NOT NULL,
    is_holiday     boolean  NOT NULL DEFAULT false,
    fiscal_year    int,
    fiscal_quarter int
);  -- Redshift: DISTSTYLE ALL SORTKEY (date_sk)

CREATE TABLE dim_merchant (
    merchant_sk   int        PRIMARY KEY,
    merchant_id   varchar    NOT NULL,
    name          varchar,
    category      varchar,
    mcc           char(4),
    country_code  varchar(2),
    region        varchar,
    status        varchar,
    scd_start     date       NOT NULL,
    scd_end       date,                          -- NULL = version courante
    is_current    boolean    NOT NULL DEFAULT true
);  -- Redshift: DISTSTYLE ALL
CREATE INDEX idx_dim_merchant_nk ON dim_merchant (merchant_id, is_current);

CREATE TABLE dim_customer (
    customer_sk        int        PRIMARY KEY,
    customer_id        varchar    NOT NULL,
    email_hash         varchar,                  -- SHA-256 salé, jamais l'email
    country_code       varchar(2),
    region             varchar,
    data_region        varchar(4),
    segment            varchar,                  -- low_value / mid_value / high_value
    acquisition_month  date,
    scd_start          date       NOT NULL,
    scd_end            date,
    is_current         boolean    NOT NULL DEFAULT true
);  -- Redshift: DISTSTYLE ALL
CREATE INDEX idx_dim_customer_nk ON dim_customer (customer_id, is_current);

CREATE TABLE dim_geography (
    geography_sk int        PRIMARY KEY,
    country_code varchar(2) NOT NULL UNIQUE,
    country_name varchar,
    region       varchar,
    data_region  varchar(4),
    gdpr_applies boolean
);  -- Redshift: DISTSTYLE ALL

CREATE TABLE dim_payment_method (
    payment_method_sk int     PRIMARY KEY,
    type              varchar NOT NULL,
    brand             varchar,
    is_digital        boolean NOT NULL
);  -- Redshift: DISTSTYLE ALL

CREATE TABLE dim_product (
    product_sk       int     PRIMARY KEY,
    product_id       varchar NOT NULL UNIQUE,
    merchant_id      varchar,
    name             varchar,
    billing_interval varchar,
    unit_price       numeric(14,2),
    currency_code    varchar(3)
);  -- Redshift: DISTSTYLE ALL

CREATE TABLE dim_currency (
    currency_sk    int        PRIMARY KEY,
    currency_code  varchar(3) NOT NULL UNIQUE,
    name           varchar,
    decimal_places smallint
);  -- Redshift: DISTSTYLE ALL

CREATE TABLE exchange_rates (
    rate_date      date          NOT NULL,
    currency_code  varchar(3)    NOT NULL,
    rate_to_usd    numeric(18,8) NOT NULL,
    PRIMARY KEY (rate_date, currency_code)
);  -- Redshift: DISTSTYLE ALL SORTKEY (rate_date)

-- ───────────────────────── Table de faits ─────────────────────────
-- Redshift: DISTKEY (merchant_sk) → toutes les transactions d'un merchant sur le même nœud
--           SORTKEY (date_sk)     → zone maps : un filtre sur une période ne lit que les blocs utiles
-- Grain : 1 ligne = 1 transaction OLTP.

CREATE TABLE fact_transactions (
    transaction_sk    bigint        PRIMARY KEY,
    transaction_id    varchar       NOT NULL UNIQUE,      -- idempotence des chargements (MERGE)
    date_sk           int           NOT NULL REFERENCES dim_date (date_sk),
    hour_of_day       smallint      NOT NULL,
    merchant_sk       int           NOT NULL REFERENCES dim_merchant (merchant_sk),
    customer_sk       int           NOT NULL REFERENCES dim_customer (customer_sk),
    geography_sk      int           NOT NULL REFERENCES dim_geography (geography_sk),
    payment_method_sk int           NOT NULL REFERENCES dim_payment_method (payment_method_sk),
    product_sk        int           REFERENCES dim_product (product_sk),
    currency_sk       int           NOT NULL REFERENCES dim_currency (currency_sk),
    status            varchar       NOT NULL,
    amount            numeric(14,2) NOT NULL,
    amount_usd        numeric(14,2) NOT NULL,
    fee_usd           numeric(14,2) NOT NULL,
    net_amount_usd    numeric(14,2) NOT NULL,
    refund_amount_usd numeric(14,2) NOT NULL DEFAULT 0,
    is_refunded       boolean       NOT NULL DEFAULT false,
    is_disputed       boolean       NOT NULL DEFAULT false,
    is_fraud_flagged  boolean       NOT NULL DEFAULT false,
    is_subscription   boolean       NOT NULL DEFAULT false,
    anomaly_score     numeric(5,4),
    risk_level        varchar,
    device_type       varchar,
    loaded_at         timestamp     NOT NULL
);  -- Redshift: DISTKEY (merchant_sk) SORTKEY (date_sk)
-- Sur PostgreSQL, l'équivalent des zone maps / sort keys est un index B-tree :
CREATE INDEX idx_fact_date      ON fact_transactions (date_sk);
CREATE INDEX idx_fact_merchant  ON fact_transactions (merchant_sk, date_sk);
CREATE INDEX idx_fact_customer  ON fact_transactions (customer_sk);
CREATE INDEX idx_fact_geography ON fact_transactions (geography_sk);

CREATE TABLE fact_audit_events (
    audit_sk     bigint  PRIMARY KEY,
    audit_id     varchar NOT NULL UNIQUE,
    date_sk      int     NOT NULL REFERENCES dim_date (date_sk),
    event_type   varchar NOT NULL,
    table_name   varchar,
    user_role    varchar NOT NULL,
    user_id_hash varchar NOT NULL,
    is_sensitive boolean NOT NULL
);  -- Redshift: DISTSTYLE EVEN SORTKEY (date_sk)

CREATE TABLE ml_churn_predictions (
    merchant_sk       int  REFERENCES dim_merchant (merchant_sk),
    prediction_date   date,
    churn_probability numeric(5,4),
    risk_category     varchar,
    model_version     varchar,
    PRIMARY KEY (merchant_sk, prediction_date)
);

-- ───────────────────────── Pré-agrégations (dbt, refresh nocturne) ─────────────────────────

CREATE TABLE agg_daily_revenue (
    date_sk            int           REFERENCES dim_date (date_sk),
    merchant_sk        int           REFERENCES dim_merchant (merchant_sk),
    currency_code      varchar(3),
    transaction_count  int,
    succeeded_count    int,
    failed_count       int,
    total_amount       numeric(16,2),
    total_amount_usd   numeric(16,2),
    net_amount_usd     numeric(16,2),
    refund_count       int,
    dispute_count      int,
    fraud_count        int,
    PRIMARY KEY (date_sk, merchant_sk, currency_code)
);  -- Redshift: DISTKEY (merchant_sk) SORTKEY (date_sk)

CREATE TABLE agg_monthly_fraud (
    year_month         char(7),
    geography_sk       int REFERENCES dim_geography (geography_sk),
    total_transactions int,
    total_flagged      int,
    total_critical     int,
    total_disputed     int,
    avg_anomaly_score  numeric(5,4),
    fraud_rate_pct     numeric(6,3),
    PRIMARY KEY (year_month, geography_sk)
);  -- Redshift: DISTSTYLE ALL SORTKEY (year_month)

COMMIT;

-- ───────────────────────── Vue matérialisée native ─────────────────────────
-- Niveau 2 de la stratégie de pré-agrégation : rafraîchie par la base elle-même.
-- Redshift: CREATE MATERIALIZED VIEW ... AUTO REFRESH YES (rafraîchissement incrémental)
CREATE MATERIALIZED VIEW mv_monthly_revenue_by_country AS
SELECT
    d.year,
    d.month,
    g.country_code,
    g.region,
    COUNT(*)                                                   AS transaction_count,
    SUM(CASE WHEN f.status = 'succeeded' THEN f.amount_usd ELSE 0 END) AS revenue_usd,
    SUM(f.net_amount_usd)                                      AS net_revenue_usd,
    SUM(CASE WHEN f.is_fraud_flagged THEN 1 ELSE 0 END)        AS fraud_count
FROM fact_transactions f
JOIN dim_date      d ON d.date_sk      = f.date_sk
JOIN dim_geography g ON g.geography_sk = f.geography_sk
GROUP BY d.year, d.month, g.country_code, g.region
WITH NO DATA;
