-- ============================================================
-- STRIPE — REQUÊTES OLAP (Redshift / BigQuery)
-- Requêtes analytiques sur le star schema
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- Q1. Revenue mensuel par pays et par devise
--     Cas d'usage : reporting financier, suivi de croissance
-- ────────────────────────────────────────────────────────────
SELECT
    d.year,
    d.month,
    d.month_name,
    g.country_name,
    g.region,
    pm.type                         AS payment_type,
    COUNT(f.transaction_sk)         AS transaction_count,
    SUM(f.amount_usd)               AS revenue_usd,
    AVG(f.amount_usd)               AS avg_ticket_usd,
    SUM(f.amount_usd)
        OVER (PARTITION BY d.year, g.country_name
              ORDER BY d.month
              ROWS UNBOUNDED PRECEDING)
                                    AS cumulative_revenue_usd
FROM fact_transactions f
JOIN dim_date           d  ON d.date_sk           = f.date_sk
JOIN dim_geography      g  ON g.geography_sk      = f.geography_sk
JOIN dim_payment_method pm ON pm.payment_method_sk = f.payment_method_sk
WHERE f.is_fraud    = false
  AND d.year        = 2024
GROUP BY d.year, d.month, d.month_name, g.country_name, g.region, pm.type
ORDER BY d.year, d.month, revenue_usd DESC;


-- ────────────────────────────────────────────────────────────
-- Q2. Segmentation clients par décile de dépenses (année en cours)
--     Cas d'usage : stratégie marketing, pricing, upsell
-- ────────────────────────────────────────────────────────────
WITH customer_spending AS (
    SELECT
        f.customer_sk,
        c.country_code,
        c.region,
        SUM(f.amount_usd)       AS total_spent_usd,
        COUNT(f.transaction_sk) AS transaction_count
    FROM fact_transactions f
    JOIN dim_customer c ON c.customer_sk = f.customer_sk
    JOIN dim_date     d ON d.date_sk     = f.date_sk
    WHERE f.is_fraud = false
      AND d.year     = 2024
    GROUP BY f.customer_sk, c.country_code, c.region
),
deciles AS (
    SELECT
        *,
        NTILE(10) OVER (ORDER BY total_spent_usd) AS spending_decile
    FROM customer_spending
)
SELECT
    spending_decile,
    COUNT(*)                    AS customer_count,
    ROUND(AVG(total_spent_usd), 2)  AS avg_spent_usd,
    ROUND(MIN(total_spent_usd), 2)  AS min_spent_usd,
    ROUND(MAX(total_spent_usd), 2)  AS max_spent_usd,
    SUM(total_spent_usd)            AS segment_revenue_usd,
    ROUND(
        SUM(total_spent_usd) * 100.0
        / SUM(SUM(total_spent_usd)) OVER (), 2
    )                               AS pct_total_revenue
FROM deciles
GROUP BY spending_decile
ORDER BY spending_decile;


-- ────────────────────────────────────────────────────────────
-- Q3. Évolution du taux de fraude par trimestre et par région
--     Cas d'usage : conformité, rapport risk management
-- ────────────────────────────────────────────────────────────
SELECT
    d.year,
    d.quarter,
    g.region,
    COUNT(f.transaction_sk)                             AS total_transactions,
    SUM(f.is_fraud::int)                                AS fraud_count,
    ROUND(
        SUM(f.is_fraud::int) * 100.0
        / NULLIF(COUNT(f.transaction_sk), 0), 3
    )                                                   AS fraud_rate_pct,
    AVG(f.anomaly_score)                                AS avg_anomaly_score,
    -- Variation vs trimestre précédent
    LAG(ROUND(SUM(f.is_fraud::int) * 100.0
        / NULLIF(COUNT(f.transaction_sk), 0), 3))
        OVER (PARTITION BY g.region ORDER BY d.year, d.quarter)
                                                        AS prev_quarter_fraud_rate,
    ROUND(
        SUM(f.is_fraud::int) * 100.0 / NULLIF(COUNT(f.transaction_sk), 0)
        - LAG(SUM(f.is_fraud::int) * 100.0 / NULLIF(COUNT(f.transaction_sk), 0))
          OVER (PARTITION BY g.region ORDER BY d.year, d.quarter)
    , 3)                                                AS fraud_rate_delta
FROM fact_transactions f
JOIN dim_date      d ON d.date_sk      = f.date_sk
JOIN dim_geography g ON g.geography_sk = f.geography_sk
GROUP BY d.year, d.quarter, g.region
ORDER BY d.year, d.quarter, fraud_rate_pct DESC;


-- ────────────────────────────────────────────────────────────
-- Q4. Performance des méthodes de paiement par région
--     Cas d'usage : optimisation produit, partenariats bancaires
-- ────────────────────────────────────────────────────────────
SELECT
    g.region,
    pm.type                             AS payment_type,
    pm.brand,
    pm.is_digital,
    COUNT(f.transaction_sk)             AS transaction_count,
    ROUND(SUM(f.amount_usd), 2)         AS total_volume_usd,
    ROUND(AVG(f.amount_usd), 2)         AS avg_ticket_usd,
    SUM(f.is_refunded::int)             AS refund_count,
    ROUND(
        SUM(f.is_refunded::int) * 100.0
        / NULLIF(COUNT(f.transaction_sk), 0), 2
    )                                   AS refund_rate_pct,
    SUM(f.is_fraud::int)                AS fraud_count,
    ROUND(
        SUM(f.is_fraud::int) * 100.0
        / NULLIF(COUNT(f.transaction_sk), 0), 2
    )                                   AS fraud_rate_pct
FROM fact_transactions f
JOIN dim_geography      g  ON g.geography_sk       = f.geography_sk
JOIN dim_payment_method pm ON pm.payment_method_sk = f.payment_method_sk
GROUP BY g.region, pm.type, pm.brand, pm.is_digital
ORDER BY g.region, total_volume_usd DESC;


-- ────────────────────────────────────────────────────────────
-- Q5. Analyse de cohorte merchants — rétention mensuelle
--     Cas d'usage : mesurer l'engagement et le churn des merchants
--     Logique : un merchant est "actif" le mois M s'il a au moins
--               une transaction successful ce mois-là
-- ────────────────────────────────────────────────────────────
WITH merchant_cohorts AS (
    -- Mois d'acquisition de chaque merchant (première transaction)
    SELECT
        f.merchant_sk,
        DATE_TRUNC('month', MIN(d.full_date))   AS cohort_month
    FROM fact_transactions f
    JOIN dim_date d ON d.date_sk = f.date_sk
    GROUP BY f.merchant_sk
),
monthly_activity AS (
    -- Mois d'activité de chaque merchant
    SELECT DISTINCT
        f.merchant_sk,
        DATE_TRUNC('month', d.full_date)        AS activity_month
    FROM fact_transactions f
    JOIN dim_date d ON d.date_sk = f.date_sk
    WHERE f.is_fraud = false
),
cohort_retention AS (
    SELECT
        mc.cohort_month,
        ma.activity_month,
        EXTRACT(MONTH FROM AGE(ma.activity_month, mc.cohort_month)) AS months_since_acquisition,
        COUNT(DISTINCT ma.merchant_sk)          AS active_merchants
    FROM merchant_cohorts mc
    JOIN monthly_activity ma ON ma.merchant_sk = mc.merchant_sk
    GROUP BY mc.cohort_month, ma.activity_month
)
SELECT
    cohort_month,
    months_since_acquisition,
    active_merchants,
    FIRST_VALUE(active_merchants)
        OVER (PARTITION BY cohort_month ORDER BY months_since_acquisition)
                                                AS cohort_size,
    ROUND(
        active_merchants * 100.0
        / FIRST_VALUE(active_merchants)
          OVER (PARTITION BY cohort_month ORDER BY months_since_acquisition)
    , 1)                                        AS retention_rate_pct
FROM cohort_retention
ORDER BY cohort_month, months_since_acquisition;
