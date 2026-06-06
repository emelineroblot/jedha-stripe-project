-- ============================================================
-- STRIPE — REQUÊTES OLTP (PostgreSQL)
-- Requêtes opérationnelles sur le schéma normalisé 3NF
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- Q1. Transactions frauduleuses des dernières 24 heures
--     Cas d'usage : alerte temps réel, dashboard ops
-- ────────────────────────────────────────────────────────────
SELECT
    t.transaction_id,
    t.created_at,
    t.amount,
    t.currency_code,
    t.status,
    fi.anomaly_score,
    fi.risk_level,
    fi.model_version,
    m.name        AS merchant_name,
    c.email       AS customer_email,
    t.ip_address,
    t.device_type
FROM transactions t
JOIN fraud_indicators fi ON fi.transaction_id = t.transaction_id
JOIN merchants        m  ON m.merchant_id      = t.merchant_id
JOIN customers        c  ON c.customer_id      = t.customer_id
WHERE t.created_at >= NOW() - INTERVAL '24 hours'
  AND fi.risk_level IN ('high', 'critical')
ORDER BY fi.anomaly_score DESC;


-- ────────────────────────────────────────────────────────────
-- Q2. Taux d'échec par merchant sur les 7 derniers jours
--     Cas d'usage : détecter les merchants avec problèmes techniques
-- ────────────────────────────────────────────────────────────
SELECT
    m.merchant_id,
    m.name                                                          AS merchant_name,
    m.category,
    COUNT(*)                                                        AS total_transactions,
    COUNT(*) FILTER (WHERE t.status = 'failed')                    AS failed_count,
    ROUND(
        COUNT(*) FILTER (WHERE t.status = 'failed') * 100.0
        / NULLIF(COUNT(*), 0), 2
    )                                                               AS failure_rate_pct
FROM transactions t
JOIN merchants m ON m.merchant_id = t.merchant_id
WHERE t.created_at >= NOW() - INTERVAL '7 days'
GROUP BY m.merchant_id, m.name, m.category
HAVING COUNT(*) >= 10        -- on exclut les merchants avec trop peu de volume
ORDER BY failure_rate_pct DESC
LIMIT 20;


-- ────────────────────────────────────────────────────────────
-- Q3. Top 10 merchants par volume (montant USD) — mois en cours
--     Cas d'usage : reporting commercial, priorité support
-- ────────────────────────────────────────────────────────────
SELECT
    m.merchant_id,
    m.name                          AS merchant_name,
    m.category,
    co.name                         AS country,
    COUNT(t.transaction_id)         AS transaction_count,
    SUM(t.amount)                   AS total_amount,
    t.currency_code,
    AVG(t.amount)                   AS avg_amount
FROM transactions t
JOIN merchants m  ON m.merchant_id  = t.merchant_id
JOIN countries co ON co.country_code = m.country_code
WHERE t.status      = 'successful'
  AND t.created_at >= DATE_TRUNC('month', NOW())
GROUP BY m.merchant_id, m.name, m.category, co.name, t.currency_code
ORDER BY total_amount DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────
-- Q4. Détection de doublons suspects
--     Même client, même montant, même merchant, intervalle < 5 min
--     Cas d'usage : prévention de la fraude par rejeu de requête
-- ────────────────────────────────────────────────────────────
SELECT
    t1.transaction_id               AS tx_first,
    t2.transaction_id               AS tx_duplicate,
    t1.customer_id,
    t1.merchant_id,
    t1.amount,
    t1.currency_code,
    t1.created_at                   AS first_at,
    t2.created_at                   AS duplicate_at,
    EXTRACT(EPOCH FROM (t2.created_at - t1.created_at)) AS gap_seconds
FROM transactions t1
JOIN transactions t2
    ON  t1.customer_id  = t2.customer_id
    AND t1.merchant_id  = t2.merchant_id
    AND t1.amount       = t2.amount
    AND t1.currency_code = t2.currency_code
    AND t2.created_at   > t1.created_at
    AND t2.created_at   < t1.created_at + INTERVAL '5 minutes'
    AND t1.transaction_id <> t2.transaction_id
ORDER BY first_at DESC;


-- ────────────────────────────────────────────────────────────
-- Q5. Clients avec plusieurs moyens de paiement utilisés
--     et au moins une transaction frauduleuse
--     Cas d'usage : profilage des comptes à risque
-- ────────────────────────────────────────────────────────────
SELECT
    c.customer_id,
    c.email,
    c.country_code,
    COUNT(DISTINCT t.payment_method_id)     AS distinct_payment_methods,
    COUNT(DISTINCT t.transaction_id)        AS total_transactions,
    COUNT(DISTINCT fi.fraud_id)             AS fraud_flags,
    MAX(fi.anomaly_score)                   AS max_anomaly_score
FROM customers c
JOIN transactions     t  ON t.customer_id      = c.customer_id
JOIN fraud_indicators fi ON fi.transaction_id  = t.transaction_id
GROUP BY c.customer_id, c.email, c.country_code
HAVING COUNT(DISTINCT t.payment_method_id) >= 2
ORDER BY max_anomaly_score DESC;
