# Architecture Globale — Intégration OLTP + OLAP + NoSQL

---

## Diagramme d'architecture complet

```
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                            SOURCES ET INGESTION                                      ║
║                                                                                      ║
║   Clients / Merchants                 Services internes          Frontend / Mobile   ║
║   (paiements, remboursements)         (fraud scoring, auth)      (sessions, feedback)║
╚══════════════╤═══════════════════════════════╤════════════════════════╤══════════════╝
               │                               │                        │
               ▼                               │                        ▼
╔══════════════════════════════╗               │          ╔═════════════════════════════╗
║   OLTP — PostgreSQL          ║               │          ║   NoSQL — MongoDB           ║
║   (transactions opérat.)     ║               │          ║   (données non structurées) ║
║                              ║               │          ║                             ║
║  ┌─────────────────────┐     ║               │          ║  ┌──────────────────────┐   ║
║  │ transactions        │     ║               │          ║  │ logs                 │   ║
║  │ merchants           │     ║               │          ║  │  - type, severity    │   ║
║  │ customers           │     ║               │          ║  │  - metadata, trace   │   ║
║  │ payment_methods     │     ║               │          ║  │  - TTL 30-90 jours   │   ║
║  │ fraud_indicators    │     ║               │          ║  ├──────────────────────┤   ║
║  │ refunds             │     ║               │          ║  │ user_sessions        │   ║
║  │ countries           │     ║               │          ║  │  - events (embedded) │   ║
║  │ currencies          │     ║               │          ║  │  - device, geo       │   ║
║  └─────────────────────┘     ║               │          ║  ├──────────────────────┤   ║
║                              ║               │          ║  │ ml_features          │   ║
║  ACID · 3NF · réplication    ║               │          ║  │  - features{}        │   ║
║  logique PostgreSQL          ║               │          ║  │  - prediction{}      │   ║
╚══════════╤═══════════════════╝               │          ║  ├──────────────────────┤   ║
           │                                   │          ║  │ customer_feedback    │   ║
           │  CDC (Debezium)                   │          ║  │  - scores, NPS       │   ║
           │  WAL → events                     │          ║  │  - sentiment{}       │   ║
           │                                   │          ║  └──────────────────────┘   ║
           ▼                                   │          ║                             ║
╔══════════════════════════════╗               │          ║  Schéma flexible · TTL ·    ║
║   Apache Kafka               ║               │          ║  embedding + referencing    ║
║   (bus de messages central)  ║               │          ╚════════════╤════════════════╝
║                              ║               │                       │
║  Topics :                    ║               │                       │ API write
║  • stripe.transactions       ║◄──────────────┘                       │ (services)
║  • stripe.merchants          ║  fraud_indicators                     │
║  • stripe.customers          ║  (scoring ML)                         │
║  • stripe.fraud_indicators   ║                                       │
║  • stripe.logs               ║──────────────────────────────────────►│
║  • stripe.sessions           ║  logs, sessions → MongoDB             │
╚══════════╤═══════════════════╝                                       │
           │                                                           │
           │  Kafka consumer                                           │
           ▼                                                           │
╔══════════════════════════════╗                                       │
║   Staging Area (OLAP)        ║                                       │
║   Tables brutes — raw_*      ║                                       │
╚══════════╤═══════════════════╝                                       │
           │                                                           │
           │  dbt (transformation + SCD Type 2)                       │
           ▼                                                           │
╔══════════════════════════════════════════════════════════╗           │
║   OLAP — Redshift / BigQuery  (star schema)              ║           │
║   (analytics & reporting)                                ║           │
║                                                          ║           │
║   ┌─────────────────────────────────────────────┐       ║           │
║   │              fact_transactions              │       ║           │
║   │  amount · amount_usd · is_fraud · is_refund │       ║           │
║   └──────┬──────────┬───────────┬───────────────┘       ║           │
║          │          │           │                        ║           │
║   ┌──────▼──┐  ┌────▼────┐  ┌──▼──────────┐            ║           │
║   │dim_date │  │dim_merch│  │dim_customer │            ║           │
║   │dim_geo  │  │(SCD T2) │  │(SCD T2)     │            ║           │
║   └─────────┘  └─────────┘  └─────────────┘            ║           │
║                                                          ║           │
║   ┌──────────────────────┐  ┌──────────────────────┐    ║           │
║   │ agg_daily_revenue    │  │ agg_monthly_fraud    │    ║           │
║   │ (refresh quotidien)  │  │ (refresh mensuel)    │    ║           │
║   └──────────────────────┘  └──────────────────────┘    ║           │
║                                                          ║           │
║   Stockage colonne · partitionnement par date            ║           │
║   Indexation FK · matérialized views                     ║           │
╚══════════════════════════════╤═══════════════════════════╝           │
                               │                                       │
                               │  Apache Airflow (orchestration batch) │
                               │  DAG quotidien (02:00 UTC)            │
                               │  DAG mensuel (J+1)                    │
                               │                                       │
                               ▼                                       ▼
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                         COUCHE ML & CONSOMMATION                                     ║
║                                                                                      ║
║   ┌─────────────────────────┐   ┌─────────────────────────┐   ┌──────────────────┐  ║
║   │  Fraud Detection        │   │  Customer Personalis.   │   │  Churn Predict.  │  ║
║   │  (temps réel, <200ms)   │   │  (batch nocturne)       │   │  (hebdomadaire)  │  ║
║   │  XGBoost + SHAP         │   │  Collaborative Filter.  │   │  XGBoost         │  ║
║   │  Features ← MongoDB     │   │  Features ← OLAP+Mongo  │   │  Features ← OLAP │  ║
║   │  Output → MongoDB +     │   │  Output → MongoDB       │   │  Output → OLAP   │  ║
║   │          PostgreSQL     │   │  recommendations{}      │   │  ml_churn_pred.  │  ║
║   └─────────────────────────┘   └─────────────────────────┘   └──────────────────┘  ║
║                                                                                      ║
║   MLflow (model registry) · Evidently AI (drift detection) · PagerDuty (alerting)   ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
```

---

## Flux de données — résumé

| Flux | Direction | Outil | Mode | Latence |
|------|-----------|-------|------|---------|
| Transactions → Kafka | OLTP → Bus | Debezium (CDC) | Streaming | < 500 ms |
| Kafka → OLAP staging | Bus → OLAP | Kafka consumer | Streaming | < 2 s |
| OLAP staging → star schema | Staging → OLAP | dbt | Micro-batch | < 5 min |
| OLAP → agrégations | OLAP interne | Airflow + dbt | Batch (nuit) | Quotidien |
| OLTP → ML scoring | OLTP → ML | Kafka + API | Streaming | < 200 ms |
| ML scoring → MongoDB | ML → NoSQL | API write | Temps réel | < 200 ms |
| ML scoring → fraud_indicators | ML → OLTP | API write | Temps réel | < 200 ms |
| Sessions/logs → MongoDB | Frontend/Services → NoSQL | API directe | Temps réel | < 1 s |
| MongoDB features → ML batch | NoSQL → ML | Airflow | Batch | Nocturne |
| OLAP → rapports conformité | OLAP → S3 | Airflow | Batch | Mensuel |

---

## Choix technologiques justifiés

| Besoin | Outil choisi | Alternative écartée | Raison |
|--------|-------------|--------------------|----|
| OLTP | PostgreSQL | MySQL | Réplication logique native (WAL) indispensable pour Debezium |
| OLAP | Redshift / BigQuery | Snowflake | Stockage colonne natif, intégration cloud AWS/GCP |
| NoSQL | MongoDB | Cassandra | Modèle document adapté aux données imbriquées (events, features ML) |
| CDC | Debezium | Batch polling | Capte les DELETEs, non-intrusif (lecture WAL) |
| Bus de messages | Apache Kafka | RabbitMQ | Replay depuis offset, multi-consommateurs, débit élevé |
| Transformation | dbt | Spark | SQL natif, tests intégrés, SCD Type 2 via snapshots |
| Orchestration | Apache Airflow | Prefect | Standard industrie, intégration native dbt + Kafka |
| ML registry | MLflow | W&B | Open-source, intégration scikit-learn/XGBoost native |

---

## Propriétés garanties par système

| Propriété | OLTP | OLAP | NoSQL |
|-----------|------|------|-------|
| ACID | ✓ PostgreSQL | Eventual consistency | Eventual consistency |
| Scalabilité | Verticale + réplication | Horizontale (MPP) | Horizontale (sharding) |
| Latence lecture | < 10 ms | < 5 s (agrégats) | < 50 ms |
| Modèle de données | Normalisé 3NF | Star schema | Document flexible |
| Rétention | Illimité | Illimité | TTL configurable |
| Chiffrement | AES-256 + TLS 1.3 | AES-256 + TLS 1.3 | AES-256 + TLS 1.3 |
