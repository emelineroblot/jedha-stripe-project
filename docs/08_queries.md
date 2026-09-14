# 08 — Requêtes SQL et NoSQL

> Livrable 8 : *SQL and NoSQL Queries* — comment les questions métier clés (revenue, fraude, segmentation, conformité, produit) se répondent avec les modèles proposés.

| Fichier | Système | Requêtes | Exécution |
|---|---|---|---|
| [`queries/oltp_queries.sql`](../queries/oltp_queries.sql) | PostgreSQL (OLTP) | 10 | `psql -d stripe_oltp -f` |
| [`queries/olap_queries.sql`](../queries/olap_queries.sql) | Redshift (démo : PostgreSQL) | 10 | `psql -d stripe_olap -f` |
| [`queries/nosql_queries.js`](../queries/nosql_queries.js) | MongoDB 7 | 10 | `mongosh --file` |

**Toutes les requêtes ont été exécutées** sur les données synthétiques via `bash scripts/demo.sh` ; les sorties complètes sont dans [`docs/results/`](results/). Les extraits ci-dessous en sont tirés. Les fenêtres temporelles sont élargies pour le jeu de démo (≈ 3 transactions/jour) et annotées avec leur valeur de production.

Couverture des questions métier de l'énoncé :

| Question | OLTP | OLAP | NoSQL |
|---|---|---|---|
| Revenue analysis | Q3, Q7 (MRR) | Q1, Q6, Q7, Q10 | — |
| Fraud detection | Q1, Q4, Q5, Q6 | Q3, Q4 | Q2, Q3, Q6, Q9 |
| Customer segmentation | — | Q2, Q5 | Q4, Q7 |
| Compliance reporting | Q8 | Q8 | — |
| Product performance | — | Q7 | Q8 |
| Ops / SLA | Q2, Q10 | Q10 | Q1, Q5, Q10 |

---

## A. OLTP — PostgreSQL

### Q1 — Transactions à risque élevé récentes
Dashboard risk ops. Jointure `transactions` ⨝ `fraud_indicators` ⨝ `merchants` ⨝ `customers`, filtre temporel dynamique `NOW() - INTERVAL`, tri par score. Le pays de la transaction est comparé au pays du client (signal de géo-mismatch visible directement). **Aucune PII** : `customer_id` plutôt que l'email.

```
   transaction_id    |       created_at       | amount | currency | status    | anomaly_score | risk_level | action | merchant_name               | geo | customer_country
 ch_d6a314c2408449a1 | 2026-09-11 09:13:43+00 | 199.09 | EUR      | succeeded |        0.7844 | high       | review | Hoffman, Baker and Richards | ES  | DE
 ch_28a7b1a179344497 | 2026-08-13 11:22:32+00 |  63.28 | GBP      | succeeded |        0.7111 | high       | review | Mcclure, Ward and Lee       | JP  | SG
 ch_0e5d942bc32a4606 | 2026-09-06 00:10:00+00 |  47.15 | EUR      | succeeded |        0.6367 | high       | review | Blake and Sons              | BR  | GB
```

### Q2 — Taux d'échec par merchant (30 j)
Détecte les intégrations défaillantes. `COUNT(*) FILTER (WHERE …)` (agrégation conditionnelle), `NULLIF` contre la division par zéro, `MODE() WITHIN GROUP` pour la raison d'échec dominante, `HAVING` pour écarter les merchants sans volume.

```
 merchant_name             | category | total | failed | failure_rate_pct | top_failure_reason
 Harrell LLC               | gaming   |     5 |      1 |            20.00 | card_declined
 Flowers, Martin and Kelly | saas     |     6 |      1 |            16.67 | card_declined
 Blake and Sons            | travel   |     7 |      1 |            14.29 | insufficient_funds
```

### Q3 — Top merchants par volume USD (90 j)
Conversion **au taux du jour de la transaction** via `exchange_rates` — sommer des montants en devises différentes n'aurait aucun sens. `STRING_AGG(DISTINCT …)` liste les devises encaissées.

```
 merchant_name  | category | country   | transaction_count | total_volume_usd | avg_ticket_usd | currencies
 Abbott-Munoz   | gaming   | Australia |                11 |          1280.09 |         116.37 | AUD
 Anderson Group | retail   | Australia |                10 |          1176.40 |         117.64 | AUD
```

### Q4 — Rafales suspectes (vélocité)
**Window function à cadre temporel** plutôt qu'un self-join O(n²) : `COUNT(*) OVER (PARTITION BY customer, merchant ORDER BY created_at RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND CURRENT ROW)`. Une seule passe, servie par l'index composite `(customer_id, merchant_id, amount, created_at)`.

```
     customer_id      |      burst_start       |       burst_end        | transactions_in_burst | total_amount
 cus_e075d16f596f4fdb | 2026-07-10 06:45:30+00 | 2026-07-10 07:22:30+00 |                     4 |       600.12
 cus_62c85ad1290e4388 | 2026-06-30 04:41:11+00 | 2026-06-30 04:59:11+00 |                     4 |       733.73
```

### Q5 — Comptes à risque multi-moyens de paiement
`COUNT(DISTINCT)` sur plusieurs axes, `LEFT JOIN disputes` pour croiser signaux modèle et vérité terrain, `HAVING` sur un agrégat.

### Q6 — Même carte sur plusieurs comptes (fingerprint)
Le `fingerprint` est un hash irréversible de la carte : on détecte la réutilisation **sans jamais manipuler le PAN** (PCI-DSS). Index partiel `WHERE fingerprint IS NOT NULL`.

```
     fingerprint      | brand | distinct_customers | customer_ids                               | transactions
 0c26116a50d749b2be6c | amex  |                  2 | cus_caa02bc58d8240ca, cus_d22f11f68e0d43e5 |            4
```

### Q7 — MRR des abonnements en USD
*Subscription management* : `subscriptions` ⨝ `products` ⨝ dernier taux de change ; les abonnements annuels sont ramenés au mois (`/ 12`).

```
 merchant_name          | active_subscriptions | past_due | canceled | mrr_usd
 Harrell LLC            |                    2 |        0 |        1 |  202.64
 Wilkerson-Day          |                    6 |        0 |        0 |  176.07
 Baker, Mason and White |                    2 |        1 |        1 |  165.99
```

### Q8 — Conformité : accès sensibles (30 j)
PCI-DSS Req 10 / RGPD : qui a accédé à `payment_methods` et `customers`, exports, effacements, échecs de connexion — par rôle. Alimente le rapport mensuel.

```
   user_role   |  event_type  |   table_name    | events | distinct_users
 analyst       | EXPORT       | transactions    |      1 |              1
 data_engineer | SELECT       | payment_methods |      1 |              1
 service       | ERASURE      | payment_methods |      1 |              1
```

### Q9 — Transaction ACID : remboursement partiel avec garde-fou
`BEGIN ISOLATION LEVEL REPEATABLE READ` … `FOR UPDATE` sur la transaction cible, vérification que le cumul des remboursements ne dépasse pas le montant, `INSERT refunds` + `INSERT audit_logs`, puis `ROLLBACK` (démo). Si le garde-fou échoue, **rien** n'est écrit — ni le refund, ni l'audit.

```
      refund_id       |   transaction_id    | amount
 re_demo_de1f5ad8d61e | ch_e59bec3746194fe4 |  22.97
ROLLBACK
```

### Q10 — Plan d'exécution : partition pruning
`EXPLAIN` sur les échecs des 30 derniers jours : *Subplans Removed: 13* — PostgreSQL n'ouvre que les 2-3 partitions mensuelles concernées sur 16. Sur le jeu de démo (quelques dizaines de lignes par partition) le planificateur choisit un scan séquentiel, moins cher qu'un index ; en production l'index partiel `idx_tx_failed` prend le relais.

---

## B. OLAP — Redshift (exécuté sur PostgreSQL)

Écrites dans le sous-ensemble SQL commun : pas de `FILTER`, pas de `boolean::int` (refusé par Redshift), pas de `AGE()` ; `SUM(CASE WHEN …)` et arithmétique entière sur `year * 100 + month`.

### Q1 — Revenue mensuel par pays et type de paiement, cumul annuel
**Window function sur agrégat** : `SUM(SUM(f.amount_usd)) OVER (PARTITION BY year, country, payment_type ORDER BY month)`. Piège évité : `SUM(f.amount_usd) OVER (…)` dans une requête `GROUP BY` est une erreur SQL (la window s'évalue après l'agrégation), et la partition doit inclure toutes les colonnes du `GROUP BY` sauf celle du cumul, sinon les séries se mélangent.

```
 year | month | country_name | payment_type  | transaction_count | revenue_usd | net_revenue_usd | cumulative_revenue_usd
 2026 |     3 | France       | bank_transfer |                 2 |       87.22 |           84.09 |                 103.99
 2026 |     4 | France       | bank_transfer |                 2 |      318.88 |          309.03 |                 422.87
 2026 |     5 | France       | bank_transfer |                 2 |      192.80 |          186.61 |                 615.67
```

### Q2 — Segmentation clients par décile + segment persisté
CTE → `NTILE(10)` → part du revenu par décile (`SUM(SUM()) OVER ()`). Le second `SELECT` lit `dim_customer.segment` calculé par dbt sur ces déciles (SCD2) : 20 % des clients (`high_value`) font 42 % du revenu ; le décile 10 seul pèse 25 %.

```
 spending_decile | customer_count | avg_spent_usd | decile_revenue_usd | pct_total_revenue
               1 |             20 |         72.25 |            1444.92 |              1.90
               9 |             19 |        638.87 |           12138.47 |             15.92
              10 |             19 |       1009.19 |           19174.66 |             25.15

  segment   | customers | revenue_usd | pct_revenue | avg_ticket_usd
 high_value |        39 |    31881.36 |        41.8 |          91.09
 mid_value  |        78 |    31418.04 |        41.2 |          77.77
 low_value  |        79 |    12935.16 |        17.0 |          60.44
```

### Q3 — Taux de fraude trimestriel par région, variation vs trimestre précédent
`LAG() OVER (PARTITION BY region ORDER BY year, quarter)` ; croise le taux modèle (`is_fraud_flagged`) et la vérité terrain (`is_disputed`).

### Q4 — Performance des méthodes de paiement par région
Quatre taux (échec, remboursement, fraude, litige) en une passe avec `SUM(CASE WHEN …)`.

```
 region | payment_type  | is_digital | transaction_count | volume_usd | failure_rate_pct | refund_rate_pct | fraud_rate_pct | dispute_rate_pct
 Asia   | card          | f          |               155 |   10620.84 |             9.68 |            4.52 |           1.29 |             3.23
 Asia   | wallet        | t          |                15 |    1008.53 |             0.00 |           13.33 |           0.00 |             6.67
```

### Q5 — Cohortes de rétention merchants
Trois CTE (cohorte, activité, rétention) ; écart en mois **portable** `(y2 − y1) × 12 + (m2 − m1)` ; `EXTRACT(MONTH FROM AGE())` serait faux au-delà de 12 mois et absent de Redshift. `FIRST_VALUE` récupère la taille initiale de la cohorte.

```
 cohort_month | months_since_acquisition | active_merchants | cohort_size | retention_rate_pct
       202509 |                        0 |               27 |          27 |              100.0
       202509 |                        1 |               18 |          27 |               66.7
       202509 |                        3 |               18 |          27 |               66.7
```

### Q6 — Série temporelle sur la table pré-agrégée
Lit `agg_daily_revenue` (1 ligne / merchant / jour) et non la fact : moyenne mobile 7 jours (`AVG() OVER (ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)`) et variation semaine sur semaine (`LAG(x, 7)`).

```
 full_date  | day_name | revenue_usd | revenue_ma7_usd | wow_change_pct | succeeded | failed
 2026-09-14 | Monday   |      614.26 |          289.41 |           39.5 |         5 |      0
 2026-09-11 | Friday   |      494.76 |          274.81 |           90.5 |         6 |      0
```

### Q7 — Performance produit
`dim_product` ⨝ fact : brut, net, taux de remboursement et de litige par produit ; distingue abonnement et achat unique.

```
 merchant_name          | product_name | billing | sales | gross_usd | net_usd | refund_rate_pct | dispute_rate_pct
 Harrell LLC            | Season pass  | month   |    21 |   2176.11 | 2106.69 |             0.0 |              0.0
 Baker, Mason and White | Season pass  | month   |    27 |   2197.34 | 1965.02 |            11.1 |              0.0
```

### Q8 — Reporting de conformité mensuel
`fact_audit_events` : événements sensibles, exports, effacements RGPD, échecs de connexion par rôle et par mois. C'est la requête du DAG `monthly_compliance`.

### Q9 — SCD Type 2 : catégorie « telle qu'elle était » vs « actuelle »
La SK de la fact pointe sur la version historique ; la version courante se retrouve par la clé naturelle + `is_current`. Les 3 merchants ayant changé de catégorie font diverger les deux lectures — c'est exactement ce que le SCD2 permet de mesurer.

```
 category_at_transaction_time | category_current | transactions | revenue_usd
 e-commerce                   | education        |            3 |      547.33
 e-commerce                   | gaming           |            7 |      238.51
 retail                       | e-commerce       |            1 |      172.47
```

### Q10 — Pré-agrégation vs fact, et vue matérialisée
Deux `EXPLAIN` pour la même question (revenu mensuel) sur `agg_daily_revenue` puis sur `fact_transactions`, et la même réponse lue dans `mv_monthly_revenue_by_country`. Sur des milliards de lignes, l'écart de coût est de 10² à 10³.

```
 year | month | revenue_usd
 2025 |     9 |     2945.35
 2025 |    10 |     5030.89
 2025 |    11 |     5825.18
```

---

## C. NoSQL — MongoDB

Toutes en **aggregation pipeline** (`$match` en tête pour utiliser les index), dates en vrais `BSON Date`. Chaque pipeline se termine par `.forEach(printjson)` pour s'afficher en mode script comme en mode interactif.

### Q1 — Logs critiques par service (30 j)
Time-series collection : `$match` sur `meta.severity` + plage de `timestamp`, `$group` par service, `$slice` pour limiter les exemples de messages.

```
{ service: 'auth-service',  severity: 'error',    count: 4, avg_latency_ms: 3788, max_latency_ms: 4708, sample_messages: [ 'Timeout on charge attempt', … ] }
{ service: 'fraud-service', severity: 'critical', count: 2, avg_latency_ms: 2947, max_latency_ms: 4094, sample_messages: [ 'Idempotency key conflict', … ] }
```

### Q2 — Sessions suspectes (bots, clics répétés au checkout)
`$match` sur l'index ESR `{converted: 1, event_count: -1}`, puis `$filter` + `$size` sur le tableau embarqué `events` pour compter les clics sur `pay_button` : l'équivalent d'un `WHERE` à l'intérieur d'un document — ce que SQL ferait avec une table `session_events`, un `GROUP BY` et une jointure.

### Q3 — Transactions à haut risque + facteurs SHAP
Projection de sous-documents (`$prediction.top_shap`) : l'explication de chaque décision est requêtable (RGPD art. 22).

```
{ transaction_id: 'ch_d6a314c2408449a1', fraud_probability: 0.7844, risk_level: 'high', action_taken: 'review',
  top_factors: [ { feature: 'geo_mismatch', value: 0.31 }, { feature: 'ip_reputation_score', value: 0.291 }, { feature: 'amount_zscore', value: 0.179 } ],
  geo_mismatch: true, ip_reputation: 0.728, label: null }
```

### Q4 — Feedbacks négatifs par pays + top tags
`$unwind` sur `tags` puis double `$group` (pays × tag → pays) : vrai comptage de fréquence (`$setUnion` ne ferait que dédupliquer). Possible grâce à `country_code` dénormalisé (extended reference).

### Q5 — Latence P50 / P95 par service (7 j)
`$percentile` (MongoDB 7, t-digest) + taux d'erreur HTTP via `$cond`. Le P95 répond à la question SLA « 95 % des requêtes tiennent en moins de combien ? », que la moyenne ne sait pas donner. Fallback < 7.0 fourni en commentaire.

### Q6 — Qualité du modèle sur les transactions labellisées
Précision par niveau de risque et rappel global, uniquement sur les documents dont `label.is_fraud` est connu (chargeback, dispute gagné, revue analyste, maturité). Montre la **boucle de feedback** en action :

```
{ risk_level: 'critical', labeled: 4,   confirmed_frauds: 3,  precision_pct: 75   }
{ risk_level: 'high',     labeled: 9,   confirmed_frauds: 5,  precision_pct: 55.6 }
{ risk_level: 'medium',   labeled: 44,  confirmed_frauds: 11, precision_pct: 25   }
{ risk_level: 'low',      labeled: 557, confirmed_frauds: 24, precision_pct: 4.3  }
{ confirmed_frauds: 43, caught: 8, recall_pct: 18.6 }
```

La précision décroît proprement avec le niveau de risque (le modèle est calibré) ; le rappel à seuil `high` est faible — c'est le compromis précision/rappel discuté dans [07_ml_integration.md](07_ml_integration.md), et la raison d'être du niveau `medium → challenge_3ds`.

### Q7 — Entonnoir de conversion (`$facet`)
Deux agrégations en une passe : pages atteintes (funnel via `$unwind events`) et conversion par device.

### Q8 — `$lookup` intra-Mongo
Recommandations du mois enrichies du NPS moyen du merchant (`$lookup` avec sous-pipeline). La jointure vers l'OLTP, elle, ne se fait jamais dans MongoDB.

### Q9 — Transaction multi-documents
`session.withTransaction()` : `ml_features` + `fraud_alerts` écrits atomiquement (replica set requis). Nettoyage en fin de requête.

```
  écritures atomiques OK pour ch_demo_…
{ transaction_id: 'ch_demo_…', level: 'critical', acknowledged: false }
```

### Q10 — `explain()`
Le plan gagnant utilise l'index composé `prediction.risk_level_1_computed_at_-1` : `IXSCAN`, 2 clés examinées pour 2 documents retournés — pas de scan de collection, pas de tri en mémoire.

```
{ stage: 'LIMIT', input_stage: 'FETCH', index_used: 'prediction.risk_level_1_computed_at_-1', docs_examined: 2, keys_examined: 2, returned: 2 }
```
