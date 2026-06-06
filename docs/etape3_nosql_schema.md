# Étape 3 — Modèle NoSQL (MongoDB)

## Schéma disponible dans `schemas/nosql_schema.json`

---

## Vue d'ensemble des collections

| Collection | Rôle | Volume estimé |
|------------|------|---------------|
| `logs` | Logs techniques (erreurs, accès, audit) | Très élevé — TTL 90 jours |
| `user_sessions` | Clickstream et comportement utilisateur | Élevé |
| `ml_features` | Features et prédictions des modèles ML | 1 document par transaction |
| `customer_feedback` | Avis et enquêtes de satisfaction | Modéré |

---

## Pourquoi MongoDB pour ces données

Ces 4 collections partagent des caractéristiques qui rendent MongoDB plus adapté que PostgreSQL :

- **Schéma variable** : les `features` ML évoluent à chaque version de modèle. Ajouter un champ ne demande pas de migration.
- **Données imbriquées** : un `user_session` contient une liste d'events de longueur variable — difficile à modéliser proprement en SQL sans table jointe.
- **Volume élevé en écriture** : les logs et les sessions sont écrits en continu. MongoDB est optimisé pour les insertions massives.
- **Requêtes flexibles** : on peut filtrer sur `features.velocity_score > 0.5` sans connaître à l'avance tous les champs.

---

## Stratégies de modélisation : embedding vs referencing

### Embedding (données imbriquées dans le même document)

Utilisé quand les données sont **toujours accédées ensemble** et que la sous-liste est **bornée en taille**.

**Exemples :**
- `user_sessions.events` : les events d'une session sont toujours lus avec la session, jamais séparément.
- `ml_features.features` : les features sont indissociables de la prédiction.
- `logs.metadata` et `logs.trace` : contexte technique lu avec le log.

**Règle appliquée :** embedding si la liste contient moins de quelques centaines d'éléments.

### Referencing (lien vers l'OLTP via un champ ID)

Utilisé pour les **relations vers les systèmes OLTP** où la source de vérité doit rester dans PostgreSQL.

**Champs de référence présents dans toutes les collections :**
- `transaction_id` → lien vers `transactions` (OLTP)
- `customer_id` → lien vers `customers` (OLTP)
- `merchant_id` → lien vers `merchants` (OLTP)

**Pourquoi ne pas dupliquer les données merchant/customer dans MongoDB ?**
Les données de référence changent (nom du merchant, segmentation client). Dupliquer créerait des incohérences. On garde l'ID et on joint si besoin.

---

## Détail par collection

### `logs`

**Index TTL** sur le champ `ttl` : MongoDB supprime automatiquement les documents expirés.
Stratégie : logs d'erreur conservés 90 jours, logs d'accès 30 jours.

**Champ `severity`** : `debug`, `info`, `warning`, `error`, `critical` — permet de filtrer rapidement les alertes sans scanner tous les logs.

---

### `user_sessions`

**Events embarqués** : liste d'objets `{event, page/element, timestamp}`. Un document = une session complète.

**Cas d'usage analytique clé :** détecter les sessions avec abandon au checkout (dernière page = `/checkout` sans event `click:pay_button`).

**Lien OLTP :** `transaction_id` présent uniquement si la session a abouti à une transaction.

---

### `ml_features`

**Un document par transaction.** Structure en deux blocs :
- `features` : valeurs d'entrée du modèle (scores de vélocité, comportements passés, réputation IP…)
- `prediction` : sortie du modèle avec `fraud_probability`, `risk_level`, `model_version`

**Versioning du modèle** via `model_version` : permet de comparer les prédictions entre différentes versions sans écraser l'historique.

**Index unique sur `transaction_id`** : garantit qu'on n'a qu'un seul document de features par transaction.

---

### `customer_feedback`

**Analyse de sentiment** embarquée dans le document : le score est calculé à l'ingestion par un modèle NLP et stocké directement — pas besoin de le recalculer à la requête.

**Tags** : tableau de mots-clés extrait automatiquement du texte libre. Index `multikey` sur ce tableau pour des requêtes comme "tous les feedbacks mentionnant 'checkout'".

**NPS score** (0-10) séparé du score `overall` (1-5) : deux métriques distinctes, souvent collectées séparément.

---

## Intégration avec OLTP et OLAP

```
PostgreSQL (OLTP)
      │
      │  Kafka (CDC) — transaction_id comme clé de message
      ▼
MongoDB
  ├── logs          ← écrits directement par les services (pas via OLTP)
  ├── user_sessions ← écrits par le frontend via API
  ├── ml_features   ← écrits par le service de scoring en temps réel
  └── customer_feedback ← écrits après la transaction

      │
      │  ETL batch (Airflow) — features ML → OLAP pour reporting
      ▼
OLAP (Redshift/BigQuery)
  └── agg_monthly_fraud ← alimenté depuis ml_features
```

---

## Index TTL — politique de rétention

| Collection | Durée de rétention | Justification |
|------------|-------------------|---------------|
| `logs` (error/critical) | 90 jours | Audit et post-mortem |
| `logs` (access/debug) | 30 jours | Volume élevé, utilité limitée |
| `user_sessions` | 180 jours | Analyse comportementale |
| `ml_features` | Illimité | Nécessaire pour réentraîner les modèles |
| `customer_feedback` | Illimité | Données CRM à valeur long terme |
