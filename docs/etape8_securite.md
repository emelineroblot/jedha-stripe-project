# Étape 8 — Sécurité et conformité

---

## Réglementations applicables

| Réglementation | Périmètre | Obligation principale |
|----------------|-----------|----------------------|
| **PCI-DSS v4** | Données de paiement (numéros de carte) | Tokenisation, chiffrement, audit |
| **RGPD** | Données personnelles des résidents UE | Pseudonymisation, droit à l'oubli, consentement |
| **CCPA** | Résidents californiens | Droit d'accès, suppression, opt-out |

---

## 1. Chiffrement

### At-rest (données stockées)

| Système | Méthode | Clé |
|---------|---------|-----|
| PostgreSQL (OLTP) | AES-256 via pgcrypto ou chiffrement disque | AWS KMS / GCP KMS |
| Redshift / BigQuery (OLAP) | Chiffrement natif AES-256 | Clé gérée par le cloud provider |
| MongoDB | Encrypted Storage Engine (AES-256) | AWS KMS |
| S3 (rapports, archives) | SSE-S3 ou SSE-KMS | AWS KMS |

### In-transit (données en mouvement)

- Toutes les connexions applicatives : **TLS 1.3** minimum (TLS 1.2 refusé)
- Kafka brokers : chiffrement TLS entre producteurs, brokers et consommateurs
- Debezium → Kafka : connexion chiffrée avec certificat mutuel (mTLS)
- API internes : HTTPS obligatoire, certificats renouvelés automatiquement (Let's Encrypt / ACM)

---

## 2. Tokenisation PCI-DSS

Les numéros de carte (PAN — Primary Account Number) ne sont **jamais stockés en clair** dans aucun système Stripe.

### Flux de tokenisation

```
Client saisit sa carte
        │
        ▼
Stripe.js (frontend)          ← le PAN ne transite jamais par les serveurs Stripe
        │
        ▼
Vault de tokenisation          ← PAN → token (ex : tok_visa_xxxx)
        │
        ▼
Base de données                ← seul le token est stocké dans payment_methods.last4
                                  (les 4 derniers chiffres, non sensibles)
```

**Ce que contient `payment_methods` :** `last4` (4 chiffres affichables), `brand` (Visa, Mastercard), token opaque. Jamais le PAN complet, jamais le CVV.

### Segmentation réseau (PCI-DSS Requirement 1)

Les systèmes qui traitent les données de carte (vault) sont isolés dans un **CDE (Cardholder Data Environment)** — sous-réseau séparé, accès restreint, monitoring renforcé.

---

## 3. Pseudonymisation RGPD

Les données personnelles identifiantes sont pseudonymisées avant leur chargement dans l'OLAP.

### Champs concernés

| Champ original | Traitement | Ce qui est stocké dans l'OLAP |
|----------------|-----------|-------------------------------|
| `email` | Hash SHA-256 avec sel | `customer_email_hash` |
| `ip_address` | Troncature du dernier octet | `185.12.44.0` au lieu de `185.12.44.21` |
| `customer_id` | Surrogate key opaque | `customer_sk` (entier sans lien direct) |

**Pourquoi la troncature d'IP et pas le hash :**
Une IP complète est une donnée personnelle (CJUE, 2016). La troncature conserve l'information géographique (pays, ville) utile pour l'analyse de fraude, tout en rendant impossible l'identification de l'individu.

### Droit à l'oubli (RGPD Art. 17)

Procédure en 3 étapes déclenchée par une demande client :

```
1. OLTP        → DELETE sur customers + anonymisation des transactions liées
2. OLAP        → Suppression de la dimension dim_customer (surrogate key)
                 Les faits restent (agrégats anonymes conservés)
3. MongoDB     → Suppression de tous les documents référençant customer_id
                 (user_sessions, ml_features, customer_feedback)
```

**DAG Airflow dédié** : `dag_right_to_erasure` — s'exécute sous 72h après réception de la demande (obligation RGPD : 30 jours, bonne pratique : 72h).

---

## 4. Contrôle d'accès (RBAC)

### Niveaux de rôles

| Rôle | OLTP (PostgreSQL) | OLAP (Redshift) | MongoDB |
|------|-------------------|-----------------|---------|
| `admin` | Tous droits | Tous droits | Tous droits |
| `data_engineer` | SELECT, INSERT sur staging | CREATE, INSERT, SELECT | readWrite sur collections techniques |
| `analyst` | SELECT sur vues anonymisées | SELECT sur star schema | read sur ml_features, logs agrégés |
| `compliance` | SELECT sur audit_logs uniquement | SELECT sur agg_monthly_fraud | Aucun accès |
| `readonly` | SELECT sur vues exposées | SELECT sur vues exposées | read sur customer_feedback |

### Vues anonymisées pour les analysts

Les analysts n'accèdent pas directement aux tables — ils passent par des **vues SQL** qui masquent les champs sensibles :

```sql
-- Vue exposée aux analysts (pas d'email, IP tronquée)
CREATE VIEW v_transactions_analyst AS
SELECT
    transaction_id,
    merchant_id,
    amount,
    currency_code,
    status,
    LEFT(ip_address, LENGTH(ip_address) - POSITION('.' IN REVERSE(ip_address))) || '.0'
        AS ip_truncated,
    device_type,
    created_at
FROM transactions;
```

### Principe du moindre privilège

- Aucun compte applicatif n'a les droits `DROP` ou `TRUNCATE`
- Les credentials de production ne sont jamais dans le code — stockés dans **AWS Secrets Manager** ou **HashiCorp Vault**
- Rotation automatique des secrets tous les 90 jours

---

## 5. Audit logging

Toute opération sensible est tracée dans une table d'audit immuable.

### Table `audit_logs` (PostgreSQL)

```sql
CREATE TABLE audit_logs (
    audit_id     varchar     PRIMARY KEY,
    event_type   varchar     NOT NULL,  -- SELECT, INSERT, UPDATE, DELETE, LOGIN, EXPORT
    table_name   varchar,
    record_id    varchar,
    user_id      varchar     NOT NULL,
    user_role    varchar     NOT NULL,
    ip_address   varchar,
    query_hash   varchar,               -- hash de la requête exécutée
    created_at   timestamp   NOT NULL DEFAULT NOW()
);
```

**Immuabilité :** la table `audit_logs` est en **append-only** — aucun rôle n'a les droits `UPDATE` ou `DELETE` sur cette table, y compris les admins. Les logs sont également répliqués sur S3 (immuabilité renforcée via S3 Object Lock).

### Événements audités

- Toute connexion réussie ou échouée
- Tout accès à des données de paiement (SELECT sur `payment_methods`)
- Tout export de données (téléchargement CSV, rapport)
- Toute modification de rôle ou de permission
- Toute suppression de données (droit à l'oubli)

---

## 6. Monitoring et détection d'intrusion

### Alertes automatiques déclenchées sur

| Événement | Seuil | Action |
|-----------|-------|--------|
| Échecs de connexion | > 5 en 1 min depuis la même IP | Blocage IP + alerte Slack |
| Volume export inhabituel | > 10 000 lignes exportées | Alerte équipe sécurité |
| Accès hors horaires | Connexion entre 00h et 06h UTC | Notification manager |
| Requête sans index (`seq_scan`) | Sur table > 1M lignes | Alerte DBA |
| Anomalie sur audit_logs | Tentative de DELETE sur la table | Incident critique immédiat |

### Outils

- **AWS CloudWatch / GCP Cloud Monitoring** — métriques infrastructure
- **Datadog** — APM et logs centralisés
- **PagerDuty** — escalade des incidents critiques

---

## 7. Plan de conformité automatisé

### Rapport PCI-DSS mensuel (DAG Airflow)

Contenu généré automatiquement :
- Nombre de transactions dans le CDE
- Tentatives d'accès refusées
- Rotations de secrets effectuées
- Résultats des scans de vulnérabilités

### Rapport RGPD trimestriel

- Nombre de demandes de droit à l'oubli traitées + délai moyen
- Inventaire des données personnelles par système
- Liste des sous-traitants ayant accès aux données UE (Registre des traitements)
- Incidents de sécurité déclarés à la CNIL

### Data lineage (traçabilité des données)

Chaque donnée de l'OLAP est tracée jusqu'à sa source OLTP via les metadata dbt. On peut répondre à la question "cette donnée vient d'où ?" en moins de 5 minutes — exigence RGPD pour les audits.

---

## Résumé des mesures par réglementation

| Mesure | PCI-DSS | RGPD | CCPA |
|--------|---------|------|------|
| Chiffrement at-rest AES-256 | ✓ | ✓ | ✓ |
| Chiffrement in-transit TLS 1.3 | ✓ | ✓ | ✓ |
| Tokenisation des PAN | ✓ | — | — |
| Pseudonymisation email/IP | — | ✓ | ✓ |
| Droit à l'oubli automatisé | — | ✓ | ✓ |
| RBAC + moindre privilège | ✓ | ✓ | ✓ |
| Audit logging immuable | ✓ | ✓ | — |
| Rapport conformité automatisé | ✓ | ✓ | — |
| Segmentation réseau CDE | ✓ | — | — |
