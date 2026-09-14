-- ============================================================
-- STRIPE — DDL OLTP (PostgreSQL 16)
-- Schéma normalisé 3NF, contraintes d'intégrité, partitionnement,
-- index, audit append-only, vue pseudonymisée.
-- Miroir exécutable de schemas/oltp_dbdiagram.txt
-- ============================================================

BEGIN;

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

-- ───────────────────────── Données de référence ─────────────────────────

CREATE TABLE countries (
    country_code  varchar(2)  PRIMARY KEY,
    name          varchar     NOT NULL,
    region        varchar     NOT NULL,
    data_region   varchar(4)  NOT NULL CHECK (data_region IN ('EU', 'US', 'APAC')),
    gdpr_applies  boolean     NOT NULL DEFAULT false
);

CREATE TABLE currencies (
    currency_code   varchar(3) PRIMARY KEY,
    name            varchar    NOT NULL,
    symbol          varchar(5),
    decimal_places  smallint   NOT NULL DEFAULT 2 CHECK (decimal_places BETWEEN 0 AND 3)
);

CREATE TABLE exchange_rates (
    rate_date      date          NOT NULL,
    currency_code  varchar(3)    NOT NULL REFERENCES currencies (currency_code),
    rate_to_usd    numeric(18,8) NOT NULL CHECK (rate_to_usd > 0),
    source         varchar       DEFAULT 'ECB',
    PRIMARY KEY (rate_date, currency_code)
);

-- ───────────────────────── Entités métier ─────────────────────────

CREATE TABLE merchants (
    merchant_id   varchar     PRIMARY KEY,
    name          varchar     NOT NULL,
    country_code  varchar(2)  NOT NULL REFERENCES countries (country_code),
    category      varchar     NOT NULL,
    mcc           char(4),
    status        varchar     NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'restricted', 'closed')),
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL
);
CREATE INDEX idx_merchants_country  ON merchants (country_code);
CREATE INDEX idx_merchants_category ON merchants (category);

CREATE TABLE customers (
    customer_id   varchar     PRIMARY KEY,
    email         varchar     NOT NULL UNIQUE,          -- PII
    country_code  varchar(2)  NOT NULL REFERENCES countries (country_code),
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL,
    deleted_at    timestamptz                           -- soft delete (RGPD art. 17)
);
CREATE INDEX idx_customers_country ON customers (country_code);
COMMENT ON COLUMN customers.email IS 'PII — hashé (SHA-256 salé) avant chargement OLAP';

CREATE TABLE payment_methods (
    payment_method_id varchar     PRIMARY KEY,
    customer_id       varchar     NOT NULL REFERENCES customers (customer_id),
    type              varchar     NOT NULL CHECK (type IN ('card', 'bank_transfer', 'wallet')),
    brand             varchar,
    last4             char(4),
    token             varchar     NOT NULL UNIQUE,      -- référence vault PCI, jamais le PAN
    fingerprint       varchar,                          -- même carte sur plusieurs comptes ?
    exp_month         smallint    CHECK (exp_month BETWEEN 1 AND 12),
    exp_year          smallint,
    is_default        boolean     NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL
);
CREATE INDEX idx_pm_customer    ON payment_methods (customer_id);
CREATE INDEX idx_pm_fingerprint ON payment_methods (fingerprint) WHERE fingerprint IS NOT NULL;
COMMENT ON COLUMN payment_methods.token IS 'PCI-DSS Req 3 : le PAN ne quitte jamais le vault (CDE)';

CREATE TABLE products (
    product_id        varchar       PRIMARY KEY,
    merchant_id       varchar       NOT NULL REFERENCES merchants (merchant_id),
    name              varchar       NOT NULL,
    unit_price        numeric(14,2) NOT NULL CHECK (unit_price >= 0),
    currency_code     varchar(3)    NOT NULL REFERENCES currencies (currency_code),
    billing_interval  varchar       CHECK (billing_interval IN ('month', 'year')),
    active            boolean       NOT NULL DEFAULT true,
    created_at        timestamptz   NOT NULL
);
CREATE INDEX idx_products_merchant ON products (merchant_id);

CREATE TABLE subscriptions (
    subscription_id       varchar     PRIMARY KEY,
    customer_id           varchar     NOT NULL REFERENCES customers (customer_id),
    merchant_id           varchar     NOT NULL REFERENCES merchants (merchant_id),
    product_id            varchar     NOT NULL REFERENCES products (product_id),
    status                varchar     NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    current_period_start  date        NOT NULL,
    current_period_end    date        NOT NULL CHECK (current_period_end > current_period_start),
    canceled_at           timestamptz,
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL
);
CREATE INDEX idx_subscriptions_merchant_status ON subscriptions (merchant_id, status);
CREATE INDEX idx_subscriptions_customer        ON subscriptions (customer_id);

-- ───────────────────────── Table centrale (partitionnée par mois) ─────────────────────────

CREATE TABLE transactions (
    transaction_id     varchar       NOT NULL,
    merchant_id        varchar       NOT NULL REFERENCES merchants (merchant_id),
    customer_id        varchar       NOT NULL REFERENCES customers (customer_id),
    payment_method_id  varchar       NOT NULL REFERENCES payment_methods (payment_method_id),
    product_id         varchar       REFERENCES products (product_id),
    subscription_id    varchar       REFERENCES subscriptions (subscription_id),
    amount             numeric(14,2) NOT NULL CHECK (amount > 0),
    currency_code      varchar(3)    NOT NULL REFERENCES currencies (currency_code),
    status             varchar       NOT NULL CHECK (status IN ('succeeded', 'failed')),
    failure_reason     varchar,
    ip_address         inet,                                        -- PII
    geo_country_code   varchar(2)    REFERENCES countries (country_code),
    geo_city           varchar,
    device_type        varchar       CHECK (device_type IN ('mobile', 'desktop', 'tablet')),
    idempotency_key    varchar,
    created_at         timestamptz   NOT NULL,
    updated_at         timestamptz   NOT NULL,
    PRIMARY KEY (transaction_id, created_at),                        -- la clé de partition doit faire partie de la PK
    UNIQUE (idempotency_key, created_at),
    CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
) PARTITION BY RANGE (created_at);

-- Partitions mensuelles : 15 mois glissants + partition par défaut (filet de sécurité)
DO $$
DECLARE
    m date := date_trunc('month', now() - interval '14 months')::date;
BEGIN
    WHILE m <= date_trunc('month', now() + interval '1 month')::date LOOP
        EXECUTE format(
            'CREATE TABLE transactions_%s PARTITION OF transactions FOR VALUES FROM (%L) TO (%L)',
            to_char(m, 'YYYY_MM'), m, m + interval '1 month'
        );
        m := (m + interval '1 month')::date;
    END LOOP;
END $$;
CREATE TABLE transactions_default PARTITION OF transactions DEFAULT;

-- Index (propagés automatiquement à chaque partition)
CREATE INDEX idx_tx_merchant_created ON transactions (merchant_id, created_at DESC);
CREATE INDEX idx_tx_customer_created ON transactions (customer_id, created_at DESC);
CREATE INDEX idx_tx_dup_detection    ON transactions (customer_id, merchant_id, amount, created_at);
CREATE INDEX idx_tx_subscription     ON transactions (subscription_id) WHERE subscription_id IS NOT NULL;
CREATE INDEX idx_tx_failed           ON transactions (created_at) WHERE status = 'failed';   -- index partiel
CREATE UNIQUE INDEX idx_tx_id        ON transactions (transaction_id, created_at);

COMMENT ON TABLE transactions IS 'Partition RANGE mensuelle sur created_at : archivage par DETACH PARTITION → S3';

-- ───────────────────────── Tables liées ─────────────────────────
-- Note : les FK vers une table partitionnée doivent référencer la PK complète ;
-- pour garder transaction_id seul comme référence, on vérifie l'existence par trigger.

CREATE OR REPLACE FUNCTION assert_transaction_exists() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM transactions WHERE transaction_id = NEW.transaction_id) THEN
        RAISE EXCEPTION 'transaction % inexistante', NEW.transaction_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TABLE refunds (
    refund_id       varchar       PRIMARY KEY,
    transaction_id  varchar       NOT NULL,
    amount          numeric(14,2) NOT NULL CHECK (amount > 0),
    reason          varchar       CHECK (reason IN ('requested_by_customer', 'duplicate', 'fraudulent')),
    status          varchar       NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    created_at      timestamptz   NOT NULL
);
CREATE INDEX idx_refunds_tx ON refunds (transaction_id);
CREATE TRIGGER trg_refunds_tx BEFORE INSERT ON refunds FOR EACH ROW EXECUTE FUNCTION assert_transaction_exists();

CREATE TABLE disputes (
    dispute_id       varchar       PRIMARY KEY,
    transaction_id   varchar       NOT NULL,
    amount           numeric(14,2) NOT NULL CHECK (amount > 0),
    reason           varchar       NOT NULL,
    status           varchar       NOT NULL CHECK (status IN ('needs_response', 'under_review', 'won', 'lost')),
    evidence_due_by  date,
    opened_at        timestamptz   NOT NULL,
    resolved_at      timestamptz,
    CHECK ((status IN ('won', 'lost')) = (resolved_at IS NOT NULL))
);
CREATE INDEX idx_disputes_tx            ON disputes (transaction_id);
CREATE INDEX idx_disputes_status_opened ON disputes (status, opened_at);
CREATE TRIGGER trg_disputes_tx BEFORE INSERT ON disputes FOR EACH ROW EXECUTE FUNCTION assert_transaction_exists();

CREATE TABLE fraud_indicators (
    fraud_id        varchar      PRIMARY KEY,
    transaction_id  varchar      NOT NULL UNIQUE,
    anomaly_score   numeric(5,4) NOT NULL CHECK (anomaly_score BETWEEN 0 AND 1),
    risk_level      varchar      NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    action_taken    varchar      NOT NULL CHECK (action_taken IN ('allow', 'challenge_3ds', 'review', 'block')),
    model_version   varchar      NOT NULL,
    flagged_at      timestamptz  NOT NULL
);
CREATE INDEX idx_fraud_risk_flagged ON fraud_indicators (risk_level, flagged_at DESC);
CREATE TRIGGER trg_fraud_tx BEFORE INSERT ON fraud_indicators FOR EACH ROW EXECUTE FUNCTION assert_transaction_exists();

-- ───────────────────────── Audit append-only ─────────────────────────

CREATE TABLE audit_logs (
    audit_id     varchar     PRIMARY KEY,
    event_type   varchar     NOT NULL,
    table_name   varchar,
    record_id    varchar,
    user_id      varchar     NOT NULL,
    user_role    varchar     NOT NULL,
    ip_address   inet,
    query_hash   varchar,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_table_created ON audit_logs (table_name, created_at DESC);
CREATE INDEX idx_audit_user_created  ON audit_logs (user_id, created_at DESC);

-- Immuabilité : toute tentative d'UPDATE / DELETE est refusée, même pour le propriétaire
CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs est append-only (%s interdit)', TG_OP;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();

-- ───────────────────────── Rôles & vue pseudonymisée ─────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst') THEN CREATE ROLE analyst NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'compliance') THEN CREATE ROLE compliance NOLOGIN; END IF;
END $$;

-- Les analystes ne voient jamais l'email ni l'IP complète
CREATE VIEW v_transactions_analyst AS
SELECT
    t.transaction_id,
    t.merchant_id,
    t.customer_id,
    t.amount,
    t.currency_code,
    t.status,
    t.failure_reason,
    host(set_masklen(t.ip_address::cidr, 24))  AS ip_truncated,   -- 185.12.44.21 → 185.12.44.0
    t.geo_country_code,
    t.device_type,
    t.created_at
FROM transactions t;

GRANT SELECT ON v_transactions_analyst, merchants, countries, currencies TO analyst;
GRANT SELECT ON audit_logs TO compliance;
-- Aucun rôle applicatif n'a UPDATE/DELETE sur audit_logs (trigger + absence de GRANT)

COMMIT;
