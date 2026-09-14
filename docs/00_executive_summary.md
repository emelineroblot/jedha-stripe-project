# 00 — Synthèse exécutive

**Contexte.** Stripe traite des millions de paiements par jour et doit, en même temps, garantir l'intégrité de chaque transaction, analyser des années d'historique, exploiter des données non structurées (logs, clickstream, feedback) pour la fraude et la personnalisation, et rester conforme à PCI-DSS, au RGPD et au CCPA — dans plusieurs juridictions.

**Réponse.** Une architecture à trois systèmes spécialisés reliés par un bus d'événements, où chaque donnée a une seule source de vérité et circule sans jamais être ressaisie :

| | Rôle | Choix | Point clé |
|---|---|---|---|
| **OLTP** | Source de vérité des paiements | PostgreSQL 16, 3NF, partitionné par mois | ACID, `idempotency_key`, standby synchrone (RPO 0 / RTO < 60 s), aucun numéro de carte (tokenisation) |
| **Bus** | Diffuser chaque changement en < 500 ms | Debezium (CDC) + Kafka + Schema Registry | Idempotence à chaque étape → exactly-once effectif ; DLQ, replay |
| **NoSQL** | Contexte : logs, sessions, features et prédictions ML, feedback | MongoDB 7 (time-series, TTL, sharding, transactions) | Feature store + journal de prédictions + **labels retardés** ; validators `$jsonSchema` |
| **OLAP** | Analyse, reporting, entraînement | Amazon Redshift, star schema Kimball, dbt, Airflow | SCD Type 2, `DISTKEY`/`SORTKEY`, 3 niveaux de pré-agrégation, tests bloquants |
| **ML** | Fraude temps réel (< 200 ms), personnalisation, prédictif | Règles + XGBoost + revue humaine ; Redis online ; MLflow ; Evidently | Boucle de feedback chargeback → label → réentraînement ; shadow → canary → prod |
| **Sécurité** | Conformité par construction | KMS, chiffrement colonne / champ, RBAC, audit à 3 couches immuable, résidence des données | Contrôles automatisés dans le pipeline ; rapports = sortie des contrôles |

**Ce que ce dépôt démontre concrètement.**
- 3 modèles de données complets (ERD 13 tables, star schema 12 tables, 6 collections) avec DDL exécutables.
- ~1 000 transactions synthétiques réalistes (fraude corrélée à des signaux, label delay, abonnements, litiges) et le star schema construit depuis l'OLTP.
- **30 requêtes SQL et NoSQL exécutées** sur une stack Docker (PostgreSQL + MongoDB), résultats archivés : revenue, fraude, segmentation, conformité, produit, SLA.
- Code de pipeline : connecteur Debezium, modèles dbt (staging → marts, snapshot SCD2, tests), DAGs Airflow (agrégats quotidiens, droit à l'oubli).
- Un mini-modèle de fraude entraîné sur les données générées (AUC-PR, SHAP, MLflow).

**Trois décisions à défendre.**
1. *PostgreSQL plutôt qu'une base distribuée* : le partitionnement, les read replicas et Citus couvrent l'échelle sans payer la latence d'un consensus distribué sur chaque écriture ; la source de vérité reste simple et auditable.
2. *Le chargeback comme entité, pas comme statut* : la transaction reste `succeeded`, le litige vit sa propre vie 20-90 jours plus tard — c'est ce qui rend la boucle de feedback ML et la comptabilité correctes.
3. *L'idempotence plutôt que l'exactly-once natif* : chaque étape (clé unique, `MERGE`, `upsert`, manifeste `COPY`) tolère le rejeu ; le système est simple à raisonner et à réparer.

**Fil rouge de la soutenance** : suivre une transaction — de l'`INSERT` OLTP au score de fraude en 115 ms, à la ligne du star schema 5 minutes plus tard, au chargeback 45 jours après qui devient un label d'entraînement, jusqu'à l'effacement RGPD qui la détache de l'identité sans casser la comptabilité.
