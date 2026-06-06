# Étape 2 — Star Schema OLAP

## Schéma disponible dans `schemas/olap_dbdiagram.txt`

À coller sur https://dbdiagram.io pour visualiser le diagramme.

---

## Structure du star schema

```
                    dim_date
                       │
dim_merchant ──── fact_transactions ──── dim_customer
                       │
              dim_geography   dim_payment_method

Tables de pré-agrégation : agg_daily_revenue, agg_monthly_fraud
```

**1 table de faits, 5 dimensions, 2 tables d'agrégation.**

---

## Justifications des choix de modélisation

### Star schema vs Snowflake schema

Le **star schema** a été choisi plutôt que le snowflake pour plusieurs raisons :
- Les requêtes analytiques sont plus simples : une seule jointure entre la fact table et chaque dimension.
- Les performances sont meilleures en lecture (moins de jointures = moins de coût CPU).
- Le snowflake est utile quand les dimensions sont très volumineuses et redondantes — ce n'est pas le cas ici.

### Surrogate keys (clés de substitution)

Chaque dimension utilise une `surrogate key` (entier auto-incrémenté, ex: `merchant_sk`) plutôt que la clé naturelle OLTP (`merchant_id` en varchar).

**Pourquoi :**
- Les entiers sont plus rapides pour les jointures que les varchar.
- Les surrogate keys sont stables : si un merchant change d'ID dans l'OLTP (rare mais possible), l'historique OLAP n'est pas cassé.
- Indispensable pour le SCD Type 2 (voir ci-dessous).

### Natural keys conservées

Les colonnes `merchant_id`, `customer_id`, `transaction_id` sont conservées dans les dimensions comme "natural keys". Elles permettent de faire la jointure retour vers l'OLTP quand on a besoin d'un détail opérationnel.

---

## SCD Type 2 sur merchants et customers

Les dimensions `dim_merchant` et `dim_customer` implémentent le **Slowly Changing Dimension Type 2**.

**Problème résolu :** si un merchant change de catégorie ou de pays, on veut que l'historique des transactions passées reste rattaché à l'ancienne version du merchant, pas à la nouvelle.

**Mécanisme :**
- `scd_start` / `scd_end` : plage de validité de la ligne
- `is_current = true` : identifie la version active
- Quand un merchant change : on ferme la ligne courante (`scd_end = today`, `is_current = false`) et on insère une nouvelle ligne

```sql
-- Exemple : récupérer les transactions avec la version du merchant valide au moment de la transaction
SELECT t.transaction_id, m.name, m.category
FROM fact_transactions t
JOIN dim_merchant m
  ON t.merchant_sk = m.merchant_sk
  AND m.is_current = true
```

---

## `amount_usd` : normalisation des devises

La fact table contient `amount` (montant dans la devise originale) ET `amount_usd` (converti en USD au moment du chargement ETL).

**Pourquoi :**
- Stripe traite des transactions en EUR, GBP, JPY, BRL, etc.
- Comparer des revenus multi-devises sans conversion est impossible.
- La conversion est faite une seule fois à l'ETL, pas à chaque requête analytique.

---

## Tables de pré-agrégation

### `agg_daily_revenue`
Agrégation quotidienne du revenu par merchant et devise.

**Requêtes accélérées :**
- Dashboard revenue mensuel par pays
- Top merchants par volume sur une période
- Taux de fraude et de remboursement par jour

**Stratégie de rafraîchissement :** recalcul quotidien via Airflow (job nocturne).

### `agg_monthly_fraud`
Agrégation mensuelle des indicateurs de fraude par zone géographique.

**Requêtes accélérées :**
- Évolution du taux de fraude par région sur 12 mois
- Comparaison des scores d'anomalie moyens entre pays
- Rapports de conformité (GDPR, PCI-DSS)

**Stratégie de rafraîchissement :** recalcul en fin de mois.

---

## Stratégies de performance

| Technique | Application |
|-----------|-------------|
| Partitionnement par range | `fact_transactions` partitionné par `date_sk` (année/mois) |
| Index sur FK | Toutes les foreign keys de `fact_transactions` sont indexées |
| Materialized views | `agg_daily_revenue` et `agg_monthly_fraud` jouent ce rôle |
| Columnar storage | En production (Redshift/BigQuery), le stockage colonne accélère les agrégations |

---

## Flux ETL OLTP → OLAP

```
PostgreSQL (OLTP)
      │
      │  CDC (Debezium + Kafka) — temps réel
      ▼
Staging area (tables brutes)
      │
      │  dbt — transformation + application SCD
      ▼
Star schema OLAP (Redshift / BigQuery)
      │
      │  Airflow — recalcul agrégations (nuit)
      ▼
Tables agg_daily_revenue, agg_monthly_fraud
```
