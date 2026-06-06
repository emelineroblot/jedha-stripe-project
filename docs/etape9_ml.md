# Étape 9 — Intégration Machine Learning

---

## Cas d'usage ML

| Modèle | Type | Latence requise | Source de données |
|--------|------|-----------------|-------------------|
| **Fraud detection** | Classification binaire | < 200 ms | OLTP + MongoDB `ml_features` |
| **Customer personalization** | Recommandation | Batch (nuit) | OLAP + MongoDB `user_sessions` |
| **Merchant churn prediction** | Classification binaire | Batch (hebdo) | OLAP `fact_transactions` |

---

## Architecture globale ML

```
┌──────────────────────────────────────────────────────────────┐
│                      FEATURE STORE                           │
│         MongoDB — collection ml_features                     │
│  Features précalculées par transaction, versionnées          │
└───────────────┬──────────────────────────────────────────────┘
                │
    ┌───────────┴────────────────────────────┐
    │                                        │
    ▼                                        ▼
┌───────────────────┐              ┌──────────────────────┐
│  SCORING TEMPS    │              │  ENTRAÎNEMENT        │
│  RÉEL             │              │  OFFLINE             │
│                   │              │                      │
│  Kafka consumer   │              │  OLAP → dbt →        │
│  → API scoring    │              │  dataset d'entraîn.  │
│  → MongoDB write  │              │  → scikit-learn /    │
│  → Kafka produce  │              │    XGBoost           │
└───────────────────┘              └──────────────────────┘
                │                                │
                └──────────────┬─────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │  MODEL REGISTRY      │
                    │  MLflow              │
                    │  Versions, métriques │
                    │  artefacts           │
                    └──────────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │  MONITORING          │
                    │  Drift detection     │
                    │  Performance logs    │
                    │  Alertes Airflow     │
                    └──────────────────────┘
```

---

## Modèle 1 — Fraud Detection (temps réel)

### Pipeline de scoring

```
Transaction créée dans PostgreSQL (OLTP)
        │
        ▼ CDC Debezium
Kafka topic : stripe.transactions
        │
        ▼ Consumer (service Python)
Feature extraction
  • transactions_last_1h    (requête MongoDB ml_features ou Redis cache)
  • transactions_last_24h
  • avg_amount_30d
  • distinct_countries_30d
  • ip_reputation_score     (API externe)
  • device_seen_before
  • velocity_score
  • amount_zscore
        │
        ▼
API de scoring (FastAPI)
  POST /score  { transaction_id, features }
        │
        ▼
Réponse { fraud_probability, risk_level, model_version }
        │
        ├──► MongoDB : INSERT dans ml_features
        ├──► PostgreSQL : INSERT dans fraud_indicators (si risk >= high)
        └──► Kafka topic : stripe.fraud_alerts (si risk = critical)
```

### Contrainte de latence

Le scoring doit s'effectuer en **< 200 ms** pour ne pas bloquer l'autorisation de paiement.

Optimisations :
- Features à haute fréquence (transactions_last_1h) précalculées dans **Redis** (TTL 1h) — pas de requête MongoDB à chaque scoring
- Modèle chargé en mémoire au démarrage de l'API — pas de rechargement à chaque requête
- API scalée horizontalement derrière un load balancer

### Algorithme

**XGBoost** (Gradient Boosting) — choix justifié :
- Performant sur des données tabulaires déséquilibrées (5% de fraude)
- Gère nativement les valeurs manquantes
- Interprétable via SHAP (valeurs d'importance des features) — exigence réglementaire pour expliquer les décisions de blocage

```python
# Exemple de pipeline d'entraînement (simplifié)
from xgboost import XGBClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

pipeline = Pipeline([
    ("scaler", StandardScaler()),
    ("model", XGBClassifier(
        n_estimators=200,
        max_depth=6,
        scale_pos_weight=19,   # ratio négatifs/positifs : 95/5 = 19
        eval_metric="aucpr",   # AUC-PR plus pertinent que AUC-ROC sur données déséquilibrées
        random_state=42
    ))
])
```

**Métrique d'évaluation principale : AUC-PR** (Precision-Recall)
- L'AUC-ROC est trompeuse sur les datasets déséquilibrés (5% positifs)
- L'AUC-PR mesure la performance sur la classe minoritaire (fraudes) — ce qui compte réellement

---

## Modèle 2 — Customer Personalization (batch)

### Objectif

Recommander aux merchants les méthodes de paiement et les offres les plus susceptibles de convertir leurs clients, en fonction de leur historique comportemental.

### Pipeline batch (DAG Airflow, exécution nocturne)

```
OLAP : fact_transactions (12 derniers mois)
+ MongoDB : user_sessions (comportement navigation)
        │
        ▼ dbt — feature engineering
  • avg_ticket_usd_per_segment
  • preferred_payment_method
  • conversion_rate_by_device
  • session_to_purchase_ratio
        │
        ▼ Entraînement (scikit-learn — Collaborative Filtering ou LightFM)
        │
        ▼ Génération des recommandations (top 3 par merchant/segment)
        │
        ▼ Écriture dans MongoDB : collection recommendations
        │
        ▼ Exposition via API REST : GET /recommendations/{merchant_id}
```

### Stockage des recommandations

```json
{
  "_id": "rec_mer001_2024-03",
  "merchant_id": "mer_001",
  "valid_from": "2024-03-01",
  "valid_until": "2024-04-01",
  "recommendations": [
    { "segment": "high_value", "suggested_method": "wallet", "confidence": 0.87 },
    { "segment": "mid_value",  "suggested_method": "card",   "confidence": 0.73 },
    { "segment": "low_value",  "suggested_method": "card",   "confidence": 0.61 }
  ],
  "model_version": "personalization-v1.3"
}
```

---

## Modèle 3 — Merchant Churn Prediction (hebdomadaire)

### Définition du churn

Un merchant est considéré **churné** s'il n'a aucune transaction `successful` pendant **30 jours consécutifs**.

### Features utilisées (depuis l'OLAP)

| Feature | Source | Logique |
|---------|--------|---------|
| `transactions_last_30d` | fact_transactions | Activité récente |
| `transactions_last_90d` | fact_transactions | Tendance |
| `revenue_trend` | agg_daily_revenue | Pente sur 90 jours (régression linéaire) |
| `failure_rate_7d` | fact_transactions | Signal d'alerte technique |
| `days_since_last_transaction` | fact_transactions | Inactivité |
| `months_since_acquisition` | dim_merchant + dim_date | Ancienneté |

### Output

Probabilité de churn à 30 jours pour chaque merchant actif. Stockée dans l'OLAP pour alimentation du dashboard commercial.

```sql
-- Table de sortie dans l'OLAP
CREATE TABLE ml_churn_predictions (
    merchant_sk        int,
    prediction_date    date,
    churn_probability  decimal(5,4),
    risk_category      varchar,   -- low, medium, high
    model_version      varchar,
    PRIMARY KEY (merchant_sk, prediction_date)
);
```

---

## Feature Store — MongoDB `ml_features`

### Rôle

Centralise toutes les features précalculées pour éviter de les recalculer à chaque scoring.

### Stratégie de mise à jour

| Feature | Fréquence de recalcul | Méthode |
|---------|-----------------------|---------|
| `transactions_last_1h` | Temps réel (Redis) | Incrément à chaque transaction |
| `transactions_last_24h` | Toutes les heures | Job Airflow léger |
| `avg_amount_30d` | Quotidien | DAG Airflow |
| `distinct_countries_30d` | Quotidien | DAG Airflow |
| `ip_reputation_score` | À la demande | API externe (MaxMind) |

---

## Model Registry — MLflow

Toutes les versions de modèles sont enregistrées dans MLflow avec :
- Les métriques d'entraînement (AUC-PR, F1, recall@precision=0.9)
- Les hyperparamètres
- L'artefact du modèle (fichier `.pkl` ou `.json`)
- La date et le dataset d'entraînement utilisé

### Cycle de vie d'un modèle

```
Staging  →  (tests A/B sur 5% du trafic)  →  Production  →  Archived
```

Le passage en production nécessite :
- AUC-PR >= modèle en production actuel
- Recall sur les fraudes critiques >= 0.85
- Validation manuelle par l'équipe risk

---

## Monitoring des modèles

### Data drift detection

Le data drift survient quand les données de production s'écartent des données d'entraînement (ex : nouveau type de fraude non vu à l'entraînement).

**Outil :** Evidently AI — calcule la distribution des features en production vs entraînement.

**Seuil d'alerte :** drift détecté sur > 3 features simultanément → ticket de réentraînement automatique créé dans Jira.

### Performance monitoring

| Métrique | Fréquence de calcul | Seuil d'alerte |
|----------|--------------------|--------------:|
| Recall sur fraudes critiques | Quotidien | < 0.80 |
| Faux positifs (transactions bloquées à tort) | Quotidien | > 2% |
| Latence P95 de l'API scoring | Temps réel | > 200 ms |
| Taux de prédictions manquantes | Temps réel | > 0.1% |

### Réentraînement automatique

DAG Airflow `dag_model_retrain` déclenché :
- Chaque **lundi à 03:00 UTC** (réentraînement hebdomadaire planifié)
- Ou **immédiatement** si une alerte de drift ou de performance est levée

```
Extraction données entraînement (OLAP — 6 derniers mois)
        │
        ▼
Feature engineering (dbt)
        │
        ▼
Entraînement XGBoost
        │
        ▼
Évaluation : AUC-PR >= seuil ?
        │
      Oui ──► MLflow register → Staging
        │
      Non ──► Alerte équipe ML + conservation modèle actuel
```
