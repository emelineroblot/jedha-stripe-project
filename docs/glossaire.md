# Glossaire

| Terme | Définition | Où dans le projet |
|---|---|---|
| **3NF** (troisième forme normale) | Chaque attribut dépend de la clé, de toute la clé, rien que de la clé ; pas de redondance | OLTP ([02](02_oltp_erd.md)) |
| **ACID** | Atomicité, Cohérence, Isolation, Durabilité — garanties d'une transaction | PostgreSQL, OLTP Q9 |
| **AUC-PR** | Aire sous la courbe précision-rappel ; métrique adaptée aux classes rares (fraude) | [07](07_ml_integration.md) |
| **Backfill** | Rejouer l'historique pour reconstruire une table après un changement ou un bug | [05](05_pipeline.md) §3.4 |
| **Bucket pattern** | Modélisation MongoDB : découper un tableau trop long en plusieurs documents | [04](04_nosql_model.md) §3 |
| **CDC** (Change Data Capture) | Capture des changements d'une base (via son journal) pour les diffuser | Debezium |
| **CDE** (Cardholder Data Environment) | Périmètre réseau isolé qui traite les données de carte (PCI-DSS) | [06](06_security_compliance.md) §4 |
| **Change stream** | Flux des modifications d'une collection MongoDB | Mongo → Kafka |
| **Chargeback / dispute** | Contestation d'un paiement par le porteur de carte ; arrive 20-90 j après | `disputes`, labels ML |
| **CSFLE** (Client-Side Field Level Encryption) | Chiffrement d'un champ MongoDB côté client, illisible pour le serveur | [06](06_security_compliance.md) §3 |
| **Data drift / concept drift** | Dérive de la distribution des features / de la relation features → cible | [07](07_ml_integration.md) §6 |
| **dbt** | Outil de transformation SQL versionnée, avec tests et lineage | [`pipeline/dbt/`](../pipeline/dbt/) |
| **DISTKEY / SORTKEY** | Redshift : clé de répartition des lignes entre nœuds / ordre physique de tri | [03](03_olap_schema.md) §5 |
| **DLQ** (Dead Letter Queue) | Topic où sont rangés les messages impossibles à traiter, sans bloquer le flux | Kafka |
| **DPIA** | Analyse d'impact relative à la protection des données (RGPD art. 35) | scoring fraude |
| **ESR** (Equality, Sort, Range) | Ordre recommandé des champs dans un index composé MongoDB | [04](04_nosql_model.md) §4 |
| **Exactly-once effectif** | Livraison at-least-once + idempotence à chaque étape = aucun doublon observable | [05](05_pipeline.md) §3.2 |
| **Extended reference** | Copier dans un document quelques attributs stables d'une autre entité pour éviter une jointure | `customer_feedback.country_code` |
| **Feature store** | Référentiel des features ML ; *online* (faible latence) et *offline* (entraînement) | Redis / MongoDB / Redshift |
| **Fingerprint** | Hash irréversible d'une carte permettant de reconnaître la même carte sans stocker le PAN | `payment_methods` |
| **Grain** | Niveau de détail d'une ligne de table de faits | 1 ligne = 1 transaction |
| **GridFS** | Stockage de fichiers binaires dans MongoDB par morceaux | `documents` |
| **Idempotence** | Rejouer une opération ne change pas le résultat | `idempotency_key`, MERGE |
| **Label delay** | Délai entre une prédiction et la connaissance de la vérité terrain | [07](07_ml_integration.md) §4 |
| **Lineage** | Traçabilité d'une donnée jusqu'à sa source | dbt docs |
| **LSN** (Log Sequence Number) | Position dans le WAL PostgreSQL ; ordonne les événements CDC | dédoublonnage dbt |
| **MCC** | Merchant Category Code (ISO 18245), catégorie d'activité d'un commerçant | `merchants.mcc` |
| **MRR** | Monthly Recurring Revenue, revenu récurrent mensuel | OLTP Q7 |
| **PAN** | Primary Account Number, le numéro de carte | jamais stocké |
| **PCI-DSS** | Norme de sécurité des données de cartes de paiement | [06](06_security_compliance.md) §12 |
| **PITR** | Point-In-Time Recovery, restauration à un instant précis | PostgreSQL |
| **Pseudonymisation** | Remplacer les identifiants directs par des valeurs non identifiantes (hash, troncature) | `email_hash`, IP `/24` |
| **RPO / RTO** | Perte de données maximale admise / durée d'interruption maximale admise | [02](02_oltp_erd.md) §7 |
| **SCD Type 2** (Slowly Changing Dimension) | Historiser les versions d'une dimension avec dates de validité | `dim_merchant`, `dim_customer` |
| **Schema Registry** | Registre des schémas Avro des messages Kafka, avec règles de compatibilité | [05](05_pipeline.md) §2.2 |
| **Shadow mode** | Nouveau modèle qui score sans agir, pour comparaison | [07](07_ml_integration.md) §5 |
| **SHAP** | Valeurs expliquant la contribution de chaque feature à une prédiction | `ml_features.prediction.top_shap` |
| **Star schema** | Table de faits centrale entourée de dimensions dénormalisées | [03](03_olap_schema.md) |
| **Surrogate key (SK)** | Clé entière technique d'une dimension, distincte de la clé naturelle | `merchant_sk` |
| **Time-series collection** | Collection MongoDB optimisée pour les mesures horodatées | `logs` |
| **Tokenisation** | Remplacer une donnée sensible par un jeton opaque référençant un vault | `payment_methods.token` |
| **TTL** (Time To Live) | Expiration automatique d'un document après une durée | index TTL MongoDB |
| **WAL** (Write-Ahead Log) | Journal des modifications PostgreSQL ; base de la durabilité, de la réplication et du CDC | Debezium |
| **Zone sharding** | Assigner des plages de clés à des shards situés dans une région donnée | résidence des données |
