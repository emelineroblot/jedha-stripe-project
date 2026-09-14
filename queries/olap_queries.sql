-- ============================================================
-- STRIPE — REQUÊTES OLAP (star schema)
-- Cible de production : Amazon Redshift. Écrites dans le sous-ensemble
-- SQL commun Redshift / PostgreSQL pour être exécutables sur la démo
-- locale : pas de FILTER (WHERE), pas de cast boolean::int, pas de AGE().
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- Q1. Revenue mensuel par pays et type de paiement, avec cumul annuel
--     Cas d'usage : reporting financier, suivi de croissance
--     Window function sur agrégat : SUM(SUM(...)) OVER (...)
-- ────────────────────────────────────────────────────────────
SELECT
    d.year,
    d.month,
    g.country_name,
    pm.type                                        AS payment_type,
    COUNT(*)                                       AS transaction_count,
    ROUND(SUM(f.amount_usd), 2)                    AS revenue_usd,
    ROUND(SUM(f.net_amount_usd), 2)                AS net_revenue_usd,
    ROUND(AVG(f.amount_usd), 2)                    AS avg_ticket_usd,
    ROUND(SUM(SUM(f.amount_usd)) OVER (
        PARTITION BY d.year, g.country_name, pm.type
        ORDER BY d.month
        ROWS UNBOUNDED PRECEDING
    ), 2)                                          AS cumulative_revenue_usd
FROM fact_transactions f
JOIN dim_date           d  ON d.date_sk            = f.date_sk
JOIN dim_geography      g  ON g.geography_sk       = f.geography_sk
JOIN dim_payment_method pm ON pm.payment_method_sk = f.payment_method_sk
WHERE f.status = 'succeeded'
  AND f.is_fraud_flagged = false
  AND g.country_code IN ('US', 'FR', 'GB')
GROUP BY d.year, d.month, g.country_name, pm.type
ORDER BY g.country_name, pm.type, d.year, d.month;


-- ────────────────────────────────────────────────────────────
-- Q2. Segmentation clients par décile de dépenses (12 derniers mois)
--     Cas d'usage : marketing, pricing, upsell (loi de Pareto)
-- ────────────────────────────────────────────────────────────
WITH customer_spending AS (
    SELECT
        f.customer_sk,
        c.region,
        c.segment,
        SUM(f.amount_usd)  AS total_spent_usd,
        COUNT(*)           AS transaction_count
    FROM fact_transactions f
    JOIN dim_customer c ON c.customer_sk = f.customer_sk AND c.is_current = true
    WHERE f.status = 'succeeded'
      AND f.is_fraud_flagged = false
    GROUP BY f.customer_sk, c.region, c.segment
),
deciles AS (
    SELECT *, NTILE(10) OVER (ORDER BY total_spent_usd) AS spending_decile
    FROM customer_spending
)
SELECT
    spending_decile,
    COUNT(*)                                   AS customer_count,
    ROUND(AVG(total_spent_usd), 2)             AS avg_spent_usd,
    ROUND(MIN(total_spent_usd), 2)             AS min_spent_usd,
    ROUND(MAX(total_spent_usd), 2)             AS max_spent_usd,
    ROUND(SUM(total_spent_usd), 2)             AS decile_revenue_usd,
    ROUND(SUM(total_spent_usd) * 100.0 / SUM(SUM(total_spent_usd)) OVER (), 2) AS pct_total_revenue,
    MIN(segment)                               AS segment_label
FROM deciles
GROUP BY spending_decile
ORDER BY spending_decile;


-- ────────────────────────────────────────────────────────────
-- Q3. Évolution du taux de fraude par trimestre et par région
--     avec variation vs trimestre précédent (LAG)
--     Cas d'usage : rapport risk management / conformité
-- ────────────────────────────────────────────────────────────
WITH quarterly AS (
    SELECT
        d.year,
        d.quarter,
        g.region,
        COUNT(*)                                                    AS total_transactions,
        SUM(CASE WHEN f.is_fraud_flagged THEN 1 ELSE 0 END)         AS fraud_count,
        SUM(CASE WHEN f.is_disputed THEN 1 ELSE 0 END)              AS dispute_count,
        AVG(f.anomaly_score)                                        AS avg_anomaly_score
    FROM fact_transactions f
    JOIN dim_date      d ON d.date_sk      = f.date_sk
    JOIN dim_geography g ON g.geography_sk = f.geography_sk
    GROUP BY d.year, d.quarter, g.region
)
SELECT
    year,
    quarter,
    region,
    total_transactions,
    fraud_count,
    dispute_count,
    ROUND(fraud_count * 100.0 / NULLIF(total_transactions, 0), 2)                    AS fraud_rate_pct,
    ROUND(LAG(fraud_count * 100.0 / NULLIF(total_transactions, 0))
          OVER (PARTITION BY region ORDER BY year, quarter), 2)                      AS prev_quarter_fraud_rate,
    ROUND(fraud_count * 100.0 / NULLIF(total_transactions, 0)
          - LAG(fraud_count * 100.0 / NULLIF(total_transactions, 0))
            OVER (PARTITION BY region ORDER BY year, quarter), 2)                    AS fraud_rate_delta,
    ROUND(avg_anomaly_score, 4)                                                      AS avg_anomaly_score
FROM quarterly
ORDER BY region, year, quarter;


-- ────────────────────────────────────────────────────────────
-- Q4. Performance des méthodes de paiement par région
--     Cas d'usage : optimisation produit, partenariats bancaires
-- ────────────────────────────────────────────────────────────
SELECT
    g.region,
    pm.type                                                          AS payment_type,
    pm.is_digital,
    COUNT(*)                                                         AS transaction_count,
    ROUND(SUM(CASE WHEN f.status = 'succeeded' THEN f.amount_usd ELSE 0 END), 2) AS volume_usd,
    ROUND(SUM(CASE WHEN f.status = 'failed'    THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)       AS failure_rate_pct,
    ROUND(SUM(CASE WHEN f.is_refunded          THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)       AS refund_rate_pct,
    ROUND(SUM(CASE WHEN f.is_fraud_flagged     THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)       AS fraud_rate_pct,
    ROUND(SUM(CASE WHEN f.is_disputed          THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)       AS dispute_rate_pct
FROM fact_transactions f
JOIN dim_geography      g  ON g.geography_sk       = f.geography_sk
JOIN dim_payment_method pm ON pm.payment_method_sk = f.payment_method_sk
GROUP BY g.region, pm.type, pm.is_digital
ORDER BY g.region, volume_usd DESC;


-- ────────────────────────────────────────────────────────────
-- Q5. Cohortes de rétention merchants (mois d'acquisition × mois d'activité)
--     Calcul d'écart en mois portable : (y2 - y1) * 12 + (m2 - m1)
-- ────────────────────────────────────────────────────────────
WITH merchant_cohorts AS (
    SELECT f.merchant_sk, MIN(d.year * 100 + d.month) AS cohort_ym
    FROM fact_transactions f
    JOIN dim_date d ON d.date_sk = f.date_sk
    WHERE f.status = 'succeeded'
    GROUP BY f.merchant_sk
),
monthly_activity AS (
    SELECT DISTINCT f.merchant_sk, d.year * 100 + d.month AS activity_ym
    FROM fact_transactions f
    JOIN dim_date d ON d.date_sk = f.date_sk
    WHERE f.status = 'succeeded'
),
cohort_retention AS (
    SELECT
        mc.cohort_ym,
        (ma.activity_ym / 100 - mc.cohort_ym / 100) * 12
          + (ma.activity_ym % 100 - mc.cohort_ym % 100)        AS months_since_acquisition,
        COUNT(DISTINCT ma.merchant_sk)                          AS active_merchants
    FROM merchant_cohorts mc
    JOIN monthly_activity ma ON ma.merchant_sk = mc.merchant_sk
    GROUP BY mc.cohort_ym,
        (ma.activity_ym / 100 - mc.cohort_ym / 100) * 12 + (ma.activity_ym % 100 - mc.cohort_ym % 100)
)
SELECT
    cohort_ym                                                              AS cohort_month,
    months_since_acquisition,
    active_merchants,
    FIRST_VALUE(active_merchants) OVER (PARTITION BY cohort_ym ORDER BY months_since_acquisition
                                        ROWS UNBOUNDED PRECEDING)          AS cohort_size,
    ROUND(active_merchants * 100.0
          / FIRST_VALUE(active_merchants) OVER (PARTITION BY cohort_ym ORDER BY months_since_acquisition
                                                ROWS UNBOUNDED PRECEDING), 1) AS retention_rate_pct
FROM cohort_retention
WHERE months_since_acquisition <= 6
ORDER BY cohort_ym, months_since_acquisition;


-- ────────────────────────────────────────────────────────────
-- Q6. Série temporelle : revenue quotidien, moyenne mobile 7 j, variation
--     Lit la table pré-agrégée agg_daily_revenue (pas la fact) :
--     1 ligne par merchant et par jour au lieu d'1 par transaction
--     (millions/jour chez Stripe) → réponse instantanée
-- ────────────────────────────────────────────────────────────
WITH daily AS (
    SELECT
        d.full_date,
        d.day_name,
        d.is_weekend,
        SUM(a.total_amount_usd)   AS revenue_usd,
        SUM(a.succeeded_count)    AS succeeded,
        SUM(a.failed_count)       AS failed
    FROM agg_daily_revenue a
    JOIN dim_date d ON d.date_sk = a.date_sk
    WHERE d.full_date >= CURRENT_DATE - 60
    GROUP BY d.full_date, d.day_name, d.is_weekend
)
SELECT
    full_date,
    day_name,
    is_weekend,
    revenue_usd,
    ROUND(AVG(revenue_usd) OVER (ORDER BY full_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2)  AS revenue_ma7_usd,
    ROUND((revenue_usd - LAG(revenue_usd, 7) OVER (ORDER BY full_date)) * 100.0
          / NULLIF(LAG(revenue_usd, 7) OVER (ORDER BY full_date), 0), 1)                             AS wow_change_pct,
    succeeded,
    failed
FROM daily
ORDER BY full_date DESC
LIMIT 21;


-- ────────────────────────────────────────────────────────────
-- Q7. Performance produit : revenu net, taux de remboursement, part abonnement
--     Cas d'usage : product performance metrics (énoncé)
-- ────────────────────────────────────────────────────────────
SELECT
    m.name                                                              AS merchant_name,
    p.name                                                              AS product_name,
    COALESCE(p.billing_interval, 'one-off')                             AS billing,
    COUNT(*)                                                            AS sales,
    ROUND(SUM(f.amount_usd), 2)                                         AS gross_usd,
    ROUND(SUM(f.net_amount_usd), 2)                                     AS net_usd,
    ROUND(SUM(CASE WHEN f.is_refunded THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS refund_rate_pct,
    ROUND(SUM(CASE WHEN f.is_disputed THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS dispute_rate_pct
FROM fact_transactions f
JOIN dim_product  p ON p.product_sk  = f.product_sk
JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
WHERE f.status = 'succeeded'
GROUP BY m.name, p.name, p.billing_interval
ORDER BY net_usd DESC
LIMIT 15;


-- ────────────────────────────────────────────────────────────
-- Q8. Reporting de conformité mensuel (PCI-DSS Req 10, RGPD)
--     Accès sensibles par rôle, exports, effacements, échecs de connexion
-- ────────────────────────────────────────────────────────────
SELECT
    d.year,
    d.month,
    a.user_role,
    COUNT(*)                                                                    AS total_events,
    SUM(CASE WHEN a.is_sensitive THEN 1 ELSE 0 END)                             AS sensitive_events,
    SUM(CASE WHEN a.event_type = 'EXPORT'       THEN 1 ELSE 0 END)              AS exports,
    SUM(CASE WHEN a.event_type = 'ERASURE'      THEN 1 ELSE 0 END)              AS gdpr_erasures,
    SUM(CASE WHEN a.event_type = 'LOGIN_FAILED' THEN 1 ELSE 0 END)              AS failed_logins,
    COUNT(DISTINCT a.user_id_hash)                                              AS distinct_users
FROM fact_audit_events a
JOIN dim_date d ON d.date_sk = a.date_sk
WHERE d.full_date >= CURRENT_DATE - 90
GROUP BY d.year, d.month, a.user_role
ORDER BY d.year DESC, d.month DESC, sensitive_events DESC;


-- ────────────────────────────────────────────────────────────
-- Q9. SCD Type 2 : revenu par catégorie « telle qu'elle était » vs « actuelle »
--     La SK stockée dans la fact pointe sur la version valide au moment
--     de la transaction. Pour la vue « as-is », on repasse par la clé
--     naturelle et is_current = true. Les 3 merchants ayant changé de
--     catégorie font diverger les deux colonnes.
-- ────────────────────────────────────────────────────────────
SELECT
    hist.category                                    AS category_at_transaction_time,
    curr.category                                    AS category_current,
    COUNT(*)                                         AS transactions,
    ROUND(SUM(f.amount_usd), 2)                      AS revenue_usd
FROM fact_transactions f
JOIN dim_merchant hist ON hist.merchant_sk = f.merchant_sk                       -- version historique
JOIN dim_merchant curr ON curr.merchant_id = hist.merchant_id AND curr.is_current = true  -- version courante
WHERE f.status = 'succeeded'
  AND hist.category <> curr.category
GROUP BY hist.category, curr.category
ORDER BY revenue_usd DESC;


-- ────────────────────────────────────────────────────────────
-- Q10. Pré-agrégation vs fact : même question, deux coûts
--      (a) sur agg_daily_revenue   (b) sur fact_transactions
--      Comparer les plans EXPLAIN — en production l'écart est de 10² à 10³.
-- ────────────────────────────────────────────────────────────
EXPLAIN (COSTS OFF)
SELECT d.year, d.month, SUM(a.total_amount_usd)
FROM agg_daily_revenue a JOIN dim_date d ON d.date_sk = a.date_sk
GROUP BY d.year, d.month;

EXPLAIN (COSTS OFF)
SELECT d.year, d.month, SUM(f.amount_usd)
FROM fact_transactions f JOIN dim_date d ON d.date_sk = f.date_sk
WHERE f.status = 'succeeded'
GROUP BY d.year, d.month;

-- Résultat identique via la vue matérialisée native (niveau 2 de pré-agrégation)
SELECT year, month, SUM(revenue_usd) AS revenue_usd
FROM mv_monthly_revenue_by_country
GROUP BY year, month
ORDER BY year, month;
