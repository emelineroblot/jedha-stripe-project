-- ============================================================
-- STRIPE — REQUÊTES OLTP (PostgreSQL 16)
-- Requêtes opérationnelles sur le schéma normalisé 3NF.
--
-- Fenêtres temporelles : le jeu de démo contient ~1 000 transactions
-- sur 12 mois (≈ 3 / jour). Les fenêtres sont donc élargies
-- (30 j au lieu de 24 h) ; en production elles seraient réduites.
-- Aucune requête n'expose de PII (email, IP complète) :
-- les analystes passent par la vue v_transactions_analyst.
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- Q1. Transactions à risque élevé récentes (dashboard risk ops)
--     Prod : INTERVAL '24 hours' (démo : 90 jours)
-- ────────────────────────────────────────────────────────────
SELECT
    t.transaction_id,
    t.created_at,
    t.amount,
    t.currency_code,
    t.status,
    fi.anomaly_score,
    fi.risk_level,
    fi.action_taken,
    m.name                                       AS merchant_name,
    t.customer_id,
    t.geo_country_code,
    c.country_code                               AS customer_country,
    t.device_type
FROM transactions t
JOIN fraud_indicators fi ON fi.transaction_id = t.transaction_id
JOIN merchants        m  ON m.merchant_id      = t.merchant_id
JOIN customers        c  ON c.customer_id      = t.customer_id
WHERE t.created_at >= NOW() - INTERVAL '90 days'
  AND fi.risk_level IN ('high', 'critical')
ORDER BY fi.anomaly_score DESC
LIMIT 20;


-- ────────────────────────────────────────────────────────────
-- Q2. Taux d'échec par merchant sur 30 jours
--     Cas d'usage : détecter les merchants avec problèmes d'intégration
--     Prod : INTERVAL '7 days', HAVING COUNT(*) >= 100
-- ────────────────────────────────────────────────────────────
SELECT
    m.merchant_id,
    m.name                                                       AS merchant_name,
    m.category,
    COUNT(*)                                                     AS total_transactions,
    COUNT(*) FILTER (WHERE t.status = 'failed')                  AS failed_count,
    ROUND(COUNT(*) FILTER (WHERE t.status = 'failed') * 100.0
          / NULLIF(COUNT(*), 0), 2)                              AS failure_rate_pct,
    MODE() WITHIN GROUP (ORDER BY t.failure_reason)              AS top_failure_reason
FROM transactions t
JOIN merchants m ON m.merchant_id = t.merchant_id
WHERE t.created_at >= NOW() - INTERVAL '30 days'
GROUP BY m.merchant_id, m.name, m.category
HAVING COUNT(*) >= 3
ORDER BY failure_rate_pct DESC, total_transactions DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────
-- Q3. Top 10 merchants par volume USD sur 90 jours
--     Conversion au taux du jour de la transaction (exchange_rates)
--     Cas d'usage : reporting commercial, priorité support
-- ────────────────────────────────────────────────────────────
SELECT
    m.merchant_id,
    m.name                                                  AS merchant_name,
    m.category,
    co.name                                                 AS country,
    COUNT(t.transaction_id)                                 AS transaction_count,
    ROUND(SUM(t.amount * er.rate_to_usd), 2)                AS total_volume_usd,
    ROUND(AVG(t.amount * er.rate_to_usd), 2)                AS avg_ticket_usd,
    STRING_AGG(DISTINCT t.currency_code, ', ')              AS currencies
FROM transactions t
JOIN merchants      m  ON m.merchant_id   = t.merchant_id
JOIN countries      co ON co.country_code = m.country_code
JOIN exchange_rates er ON er.currency_code = t.currency_code
                      AND er.rate_date     = t.created_at::date
WHERE t.status      = 'succeeded'
  AND t.created_at >= NOW() - INTERVAL '90 days'
GROUP BY m.merchant_id, m.name, m.category, co.name
ORDER BY total_volume_usd DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────
-- Q4. Détection de rafales suspectes (vélocité)
--     Même client, même merchant, >= 3 transactions en < 1 heure
--     Cas d'usage : bots, test de cartes volées, rejeu de requêtes
--     Index utilisé : idx_tx_dup_detection (customer_id, merchant_id, amount, created_at)
-- ────────────────────────────────────────────────────────────
WITH windows AS (
    SELECT
        t.transaction_id,
        t.customer_id,
        t.merchant_id,
        t.amount,
        t.created_at,
        COUNT(*) OVER (
            PARTITION BY t.customer_id, t.merchant_id
            ORDER BY t.created_at
            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND CURRENT ROW
        ) AS tx_in_last_hour
    FROM transactions t
)
SELECT
    customer_id,
    merchant_id,
    MIN(created_at)                         AS burst_start,
    MAX(created_at)                         AS burst_end,
    COUNT(*)                                AS transactions_in_burst,
    ROUND(SUM(amount), 2)                   AS total_amount,
    STRING_AGG(transaction_id, ', ')        AS transaction_ids
FROM windows
WHERE tx_in_last_hour >= 3
GROUP BY customer_id, merchant_id
ORDER BY transactions_in_burst DESC, burst_start DESC
LIMIT 15;


-- ────────────────────────────────────────────────────────────
-- Q5. Comptes à risque : plusieurs moyens de paiement + signaux de fraude
--     Cas d'usage : profilage des comptes, revue manuelle
-- ────────────────────────────────────────────────────────────
SELECT
    c.customer_id,
    c.country_code,
    COUNT(DISTINCT t.payment_method_id)     AS distinct_payment_methods,
    COUNT(DISTINCT t.transaction_id)        AS total_transactions,
    COUNT(DISTINCT fi.fraud_id)             AS fraud_flags,
    MAX(fi.anomaly_score)                   AS max_anomaly_score,
    COUNT(DISTINCT d.dispute_id)            AS disputes
FROM customers c
JOIN transactions      t  ON t.customer_id     = c.customer_id
JOIN fraud_indicators  fi ON fi.transaction_id = t.transaction_id
LEFT JOIN disputes     d  ON d.transaction_id  = t.transaction_id
GROUP BY c.customer_id, c.country_code
HAVING COUNT(DISTINCT t.payment_method_id) >= 2
ORDER BY fraud_flags DESC, max_anomaly_score DESC
LIMIT 15;


-- ────────────────────────────────────────────────────────────
-- Q6. Même carte utilisée par plusieurs comptes (fingerprint)
--     Cas d'usage : détection de comptes multiples / cartes volées
--     Le fingerprint est un hash de la carte : on détecte la réutilisation
--     sans jamais manipuler le PAN (PCI-DSS)
-- ────────────────────────────────────────────────────────────
SELECT
    pm.fingerprint,
    pm.brand,
    COUNT(DISTINCT pm.customer_id)                  AS distinct_customers,
    STRING_AGG(DISTINCT pm.customer_id, ', ')       AS customer_ids,
    COUNT(t.transaction_id)                         AS transactions,
    COUNT(fi.fraud_id)                              AS fraud_flags
FROM payment_methods pm
LEFT JOIN transactions     t  ON t.payment_method_id = pm.payment_method_id
LEFT JOIN fraud_indicators fi ON fi.transaction_id   = t.transaction_id
WHERE pm.fingerprint IS NOT NULL
GROUP BY pm.fingerprint, pm.brand
HAVING COUNT(DISTINCT pm.customer_id) >= 2
ORDER BY distinct_customers DESC, fraud_flags DESC;


-- ────────────────────────────────────────────────────────────
-- Q7. Abonnements : MRR (revenu récurrent mensuel) normalisé en USD
--     Cas d'usage : subscription management, reporting SaaS
-- ────────────────────────────────────────────────────────────
SELECT
    m.merchant_id,
    m.name                                                          AS merchant_name,
    COUNT(*) FILTER (WHERE s.status = 'active')                     AS active_subscriptions,
    COUNT(*) FILTER (WHERE s.status = 'past_due')                   AS past_due,
    COUNT(*) FILTER (WHERE s.status = 'canceled')                   AS canceled,
    ROUND(SUM(
        CASE WHEN s.status = 'active' THEN
            p.unit_price * er.rate_to_usd
            / CASE p.billing_interval WHEN 'year' THEN 12 ELSE 1 END
        ELSE 0 END
    ), 2)                                                           AS mrr_usd
FROM subscriptions s
JOIN products  p ON p.product_id  = s.product_id
JOIN merchants m ON m.merchant_id = s.merchant_id
JOIN exchange_rates er ON er.currency_code = p.currency_code
                      AND er.rate_date = (SELECT MAX(rate_date) FROM exchange_rates)
GROUP BY m.merchant_id, m.name
HAVING COUNT(*) FILTER (WHERE s.status = 'active') > 0
ORDER BY mrr_usd DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────
-- Q8. Conformité : accès aux données sensibles sur 30 jours
--     Cas d'usage : rapport PCI-DSS Req 10 / RGPD (qui a accédé à quoi)
-- ────────────────────────────────────────────────────────────
SELECT
    a.user_role,
    a.event_type,
    a.table_name,
    COUNT(*)                                        AS events,
    COUNT(DISTINCT a.user_id)                       AS distinct_users,
    MAX(a.created_at)                               AS last_event
FROM audit_logs a
WHERE a.created_at >= NOW() - INTERVAL '30 days'
  AND (a.table_name IN ('payment_methods', 'customers')
       OR a.event_type IN ('EXPORT', 'ERASURE', 'LOGIN_FAILED'))
GROUP BY a.user_role, a.event_type, a.table_name
ORDER BY events DESC;


-- ────────────────────────────────────────────────────────────
-- Q9. Transaction ACID : remboursement partiel avec garde-fous
--     Tout ou rien : si le cumul des remboursements dépasse le montant
--     de la transaction, la transaction SQL est annulée.
--     (ROLLBACK volontaire en fin de bloc pour ne pas modifier la démo)
-- ────────────────────────────────────────────────────────────
BEGIN ISOLATION LEVEL REPEATABLE READ;

WITH target AS (
    SELECT transaction_id, amount, currency_code
    FROM transactions
    WHERE status = 'succeeded'
    ORDER BY created_at DESC
    LIMIT 1
    FOR UPDATE
),
already_refunded AS (
    SELECT COALESCE(SUM(r.amount), 0) AS refunded
    FROM refunds r JOIN target t ON t.transaction_id = r.transaction_id
    WHERE r.status = 'succeeded'
)
INSERT INTO refunds (refund_id, transaction_id, amount, reason, status, created_at)
SELECT 're_demo_' || substr(md5(random()::text), 1, 12), t.transaction_id,
       ROUND(t.amount * 0.5, 2), 'requested_by_customer', 'succeeded', NOW()
FROM target t, already_refunded a
WHERE a.refunded + ROUND(t.amount * 0.5, 2) <= t.amount      -- garde-fou métier
RETURNING refund_id, transaction_id, amount;

INSERT INTO audit_logs (audit_id, event_type, table_name, user_id, user_role, created_at)
VALUES ('aud_demo_' || substr(md5(random()::text), 1, 12), 'INSERT', 'refunds', 'svc_refund_api', 'service', NOW());

ROLLBACK;   -- en production : COMMIT


-- ────────────────────────────────────────────────────────────
-- Q10. Plan d'exécution : preuve d'utilisation de l'index partiel
--      et du partition pruning sur transactions
-- ────────────────────────────────────────────────────────────
EXPLAIN (COSTS OFF)
SELECT transaction_id, merchant_id, failure_reason
FROM transactions
WHERE status = 'failed'
  AND created_at >= NOW() - INTERVAL '30 days';
