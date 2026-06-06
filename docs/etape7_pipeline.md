# Étape 7 — Architecture du pipeline de données

---

## Vue d'ensemble

L'architecture repose sur **deux modes de traitement complémentaires** :

| Mode | Latence | Outil | Usage |
|------|---------|-------|-------|
| Streaming | < 1 seconde | Kafka + Debezium | Synchronisation OLTP → OLAP en temps quasi-réel |
| Batch | Quotidien / mensuel | Airflow + dbt | Agrégations, transformations complexes, pré-calculs |

---

## Diagramme d'architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SOURCES DE DONNÉES                           │
│                                                                     │
│  Services Stripe  ──►  PostgreSQL (OLTP)                            │
│  Frontend/Mobile  ──►  API d'ingestion                              │
└────────────────────────┬────────────────────────────────────────────┘
                         │
          ┌──────────────▼──────────────┐
          │   COUCHE DE STREAMING       │
          │                             │
          │  Debezium (CDC)             │
          │  Capture les INSERTs,       │
          │  UPDATEs, DELETEs de        │
          │  PostgreSQL                 │
          │         │                   │
          │         ▼                   │
          │  Apache Kafka               │
          │  Topics :                   │
          │  • stripe.transactions      │
          │  • stripe.merchants         │
          │  • stripe.customers         │
          │  • stripe.fraud_indicators  │
          └──────┬──────────────┬───────┘
                 │              │
    ┌────────────▼───┐    ┌─────▼──────────────────┐
    │  OLAP          │    │  NoSQL (MongoDB)        │
    │  Staging area  │    │                         │
    │  (tables raw)  │    │  • logs                 │
    │       │        │    │  • user_sessions        │
    │       ▼        │    │  • ml_features          │
    │  dbt           │    │  • customer_feedback    │
    │  Transforms    │    └─────────────────────────┘
    │  + SCD Type 2  │
    │       │        │
    │       ▼        │
    │  Star Schema   │
    │  (Redshift /   │
    │   BigQuery)    │
    └────────┬───────┘
             │
    ┌────────▼────────────────────────────┐
    │  COUCHE BATCH (Apache Airflow)      │
    │                                     │
    │  DAG quotidien (02:00 UTC)          │
    │  • Recalcul agg_daily_revenue       │
    │  • Mise à jour taux de change       │
    │                                     │
    │  DAG mensuel (J+1 du mois)          │
    │  • Recalcul agg_monthly_fraud       │
    │  • Rapport conformité PCI-DSS       │
    └─────────────────────────────────────┘
```

---

## Composant 1 — Debezium (Change Data Capture)

### Rôle
Debezium surveille le **WAL (Write-Ahead Log)** de PostgreSQL et publie chaque changement (INSERT, UPDATE, DELETE) sur un topic Kafka correspondant.

### Pourquoi CDC plutôt qu'un batch extract

| Approche | Latence | Charge OLTP | Risque |
|----------|---------|-------------|--------|
| Batch SQL (`SELECT * WHERE updated_at > ...`) | Minutes | Requête lourde toutes les X min | Manque les DELETEs |
| CDC (Debezium) | < 1 seconde | Lecture du WAL, sans requête | Nécessite PostgreSQL `wal_level = logical` |

Le CDC est non-intrusif : Debezium lit les logs de PostgreSQL sans exécuter de requêtes sur les tables métier.

### Configuration clé
```
wal_level = logical          # dans postgresql.conf
max_replication_slots = 4    # un slot par connecteur Debezium
```

---

## Composant 2 — Apache Kafka

### Rôle
Bus de messages central qui découple les producteurs (Debezium, services applicatifs) des consommateurs (OLAP staging, MongoDB, service ML).

### Topics et rétention

| Topic | Producteur | Consommateurs | Rétention |
|-------|-----------|---------------|-----------|
| `stripe.transactions` | Debezium | OLAP staging, ML scoring | 7 jours |
| `stripe.merchants` | Debezium | OLAP staging | 7 jours |
| `stripe.customers` | Debezium | OLAP staging | 7 jours |
| `stripe.fraud_indicators` | Service ML | OLAP staging, alerting | 3 jours |
| `stripe.logs` | Services applicatifs | MongoDB | 1 jour |
| `stripe.sessions` | Frontend | MongoDB | 1 jour |

### Pourquoi Kafka et pas une queue simple (RabbitMQ, SQS)

- **Replay** : Kafka conserve les messages — si le consommateur OLAP tombe, il reprend depuis son dernier offset sans perte
- **Multi-consommateurs** : un même message peut être lu par l'OLAP staging ET le service ML en parallèle
- **Débit** : Kafka gère plusieurs millions de messages/seconde — adapté aux volumes Stripe

---

## Composant 3 — dbt (Data Build Tool)

### Rôle
Transforme les données brutes du staging area en star schema propre. Gère le SCD Type 2 sur les dimensions et garantit la qualité des données.

### Chaîne de transformation

```
stripe.transactions (Kafka)
        │
        ▼
stg_transactions        ← nettoyage, cast des types, déduplication
        │
        ▼
int_transactions        ← enrichissement (ajout amount_usd via taux de change)
        │
        ▼
fact_transactions       ← chargement dans le star schema, résolution des SK
```

### Modèles dbt pour le SCD Type 2

```sql
-- models/dim_merchant.sql (simplifié)
-- dbt gère automatiquement scd_start, scd_end, is_current
-- via le snapshot dbt

{% snapshot dim_merchant_snapshot %}
  {{ config(
    target_schema = 'snapshots',
    unique_key    = 'merchant_id',
    strategy      = 'check',
    check_cols    = ['name', 'category', 'country_code']
  ) }}
  SELECT * FROM {{ source('oltp', 'merchants') }}
{% endsnapshot %}
```

### Tests de qualité intégrés
dbt exécute des tests automatiques avant chaque chargement :
- `not_null` sur toutes les clés primaires et foreign keys
- `unique` sur `transaction_id` dans la fact table
- `accepted_values` sur `status` et `risk_level`
- `relationships` : chaque `merchant_sk` de la fact table existe dans `dim_merchant`

---

## Composant 4 — Apache Airflow

### Rôle
Orchestrateur de workflows batch. Planifie et monitore les DAGs (Directed Acyclic Graphs).

### DAG 1 — Agrégations quotidiennes (`02:00 UTC`)

```
[Attendre fin J-1]
      │
      ▼
[Recalcul agg_daily_revenue]    ← dbt run --select agg_daily_revenue
      │
      ▼
[Mise à jour taux de change]    ← API externe (ECB / Fixer.io)
      │
      ▼
[Tests qualité]                 ← dbt test
      │
      ▼
[Notification Slack si échec]
```

### DAG 2 — Rapport mensuel (`J+1 à 06:00 UTC`)

```
[Recalcul agg_monthly_fraud]
      │
      ▼
[Export rapport conformité PDF] ← PCI-DSS, GDPR
      │
      ▼
[Archivage S3]
      │
      ▼
[Envoi équipe Legal & Compliance]
```

---

## Composant 5 — Taux de change (données de référence)

Les taux de change sont récupérés quotidiennement depuis une API externe (BCE ou Fixer.io) et stockés dans une table `exchange_rates` dans l'OLAP.

```
exchange_rates (date, currency_from, currency_to, rate)
```

Le champ `amount_usd` de `fact_transactions` est calculé à l'ingestion avec le taux du **jour de la transaction** — pas le taux du jour de chargement. Cela garantit la fidélité historique des montants.

---

## Gestion des pannes et reprise

| Scénario | Mécanisme |
|----------|-----------|
| Kafka consommateur en panne | Reprise depuis le dernier offset — pas de perte |
| dbt job en échec | Airflow retry x3 avec backoff exponentiel |
| PostgreSQL indisponible | Debezium met en pause, reprend dès reconnexion |
| OLAP indisponible | Messages restent dans Kafka (rétention 7 jours) |

---

## Latences cibles

| Flux | Latence cible |
|------|--------------|
| Transaction → Kafka | < 500 ms |
| Kafka → OLAP staging | < 2 secondes |
| OLAP staging → star schema (dbt) | < 5 min (streaming micro-batch) |
| Star schema → agg_daily_revenue | Quotidien à 02:00 UTC |
| MongoDB ingestion (logs, sessions) | < 1 seconde |
