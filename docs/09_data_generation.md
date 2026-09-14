# 09 — Données synthétiques

Aucune donnée réelle : tout est généré par [`generate_data.py`](../generate_data.py) (OLTP + MongoDB) puis [`build_olap.py`](../build_olap.py) (star schema). Les fichiers sont versionnés dans [`data/`](../data/) pour que le jury puisse les consulter sans rien exécuter.

```bash
python -m venv venv && venv/Scripts/activate   # ou source venv/bin/activate
pip install -r requirements.txt
python generate_data.py --scale 1 --seed 42    # ~1 000 transactions ; --scale 10 → 10 000
python build_olap.py
```

---

## 1. Ce qui est généré

| Fichier | Lignes / docs | Contenu |
|---|---|---|
| `countries.csv` | 11 | Pays avec région, **résidence des données** (`EU`/`US`/`APAC`) et RGPD |
| `currencies.csv` | 9 | Devises avec `decimal_places` (JPY = 0) |
| `exchange_rates.csv` | ~3 300 | Taux quotidien vers USD par devise (marche aléatoire ± 0,3 %) |
| `merchants.csv` | 50 | Catégorie, MCC, statut |
| `customers.csv` | 200 | Email (PII), pays |
| `payment_methods.csv` | ~310 | 1-3 par client ; `token`, `fingerprint` (3 % de cartes partagées entre comptes) |
| `products.csv` | 150 | 3 par merchant, dans la devise du merchant ; certains à `billing_interval` |
| `subscriptions.csv` | 80 | Statut, période courante, annulation |
| `transactions.csv` | ~1 040 | Prélèvements d'abonnement + paiements ponctuels + rafales de vélocité |
| `refunds.csv` | ~40 | 4 % des succès, partiels ou totaux, 1-14 j après |
| `disputes.csv` | ~50 | Chargebacks 20-90 j après la transaction |
| `fraud_indicators.csv` | ~90 | Risque ≥ `medium` uniquement |
| `audit_logs.csv` | 300 | Événements d'accès par rôle |
| `mongo/logs.json` | 400 | Time-series : `meta.{service,type,severity}` |
| `mongo/user_sessions.json` | 500 | 60 % converties (liées à une transaction), 40 % abandonnées avec clics répétés |
| `mongo/ml_features.json` | ~1 040 | 1 par transaction : features, prédiction, SHAP, **label** |
| `mongo/customer_feedback.json` | 150 | Scores, NPS, texte, sentiment, tags, `country_code` |
| `mongo/recommendations.json` | 50 | 1 par merchant pour le mois courant |
| `olap/*.csv` | — | Dimensions (SK, SCD2), `fact_transactions`, `fact_audit_events`, `agg_*` |
| `_ground_truth_fraud.csv` | ~1 040 | Vérité terrain latente — **jamais chargée en base**, sert à `ml/train_fraud_demo.py` |

---

## 2. Choix de génération (et pourquoi)

| Choix | Pourquoi |
|---|---|
| **Dates relatives à aujourd'hui** (12 derniers mois) | Les requêtes `NOW() - INTERVAL` renvoient des résultats quel que soit le jour de la soutenance. Des dates figées donneraient des résultats vides sur `NOW()`. |
| **Seed fixe** (`--seed 42`) | Reproductibilité : mêmes données à chaque exécution (à la date près). |
| **Fraude corrélée à des signaux** | Un logit pondère géo-mismatch, heure de nuit, montant anormal (z-score), vélocité 1 h, device inconnu, réputation IP, carte partagée ; la vérité terrain et la prédiction du modèle en dérivent avec des bruits différents → le modèle est **imparfait mais calibré**, et un XGBoost peut réellement apprendre. Un score uniforme sans lien avec les features ne permettrait aucune démonstration ML. |
| **Actions du modèle** | `allow` / `challenge_3ds` / `review` / `block` selon la probabilité ; `block` ⇒ transaction `failed` (`fraudulent`). |
| **Label delay** | Les disputes sont ouvertes 20-90 j après ; celles qui « n'ont pas encore eu lieu » n'existent pas → la fraude récente est inconnue, comme en production. Les transactions `review`/`block` reçoivent un label `analyst_review` à 70 % (file de revue). |
| **Montants log-normaux** | Beaucoup de petits tickets, quelques gros — plus réaliste qu'une loi normale ; arrondis selon `decimal_places`. |
| **Devise du merchant** | C'est le merchant qui facture ; un client américain paie un merchant français en EUR. |
| **Heures pondérées** | Peu d'activité 0-5 h ; une transaction nocturne est donc un signal. |
| **Rafales de vélocité** | 8 clients font 4-6 transactions en < 1 h chez un même merchant (OLTP Q4). |
| **Cartes partagées** | 5 empreintes réutilisées sur plusieurs comptes (OLTP Q6). |
| **Extended JSON** (`{"$date": …}`) | `mongoimport` crée de vrais `BSON Date` → index TTL et comparaisons de dates fonctionnent. Avec des chaînes ISO, le TTL serait inopérant. |
| **Entiers nullables en `Int64`** | pandas exporte sinon `7.0` pour `exp_month` → erreur `COPY`. |
| **Taux de fraude ~8 %** | Surpondéré (Stripe réel : < 0,1 %) pour que les requêtes et le modèle de démo aient de la matière. |

---

## 3. `build_olap.py` — la chaîne dbt en pandas

Reproduit exactement les modèles de [`pipeline/dbt/`](../pipeline/dbt/) :

1. **Staging** : lecture et typage des CSV OLTP.
2. **Dimensions** : `dim_date` (calendrier complet), `dim_geography`, `dim_currency`, `dim_payment_method` (type × brand), `dim_product`, `dim_merchant` avec **SCD2 simulé** (3 merchants ont changé de catégorie il y a 180 j → 2 versions chacun), `dim_customer` avec `email_hash` (SHA-256 salé) et `segment` (déciles).
3. **Intermediate** : `amount_usd` au taux du jour, `fee_usd` (2,9 % + 0,30), refunds, disputes, fraude.
4. **Fact** : résolution de la SK merchant **valide à la date de la transaction**.
5. **Agrégats** : `agg_daily_revenue` (jour × merchant × devise), `agg_monthly_fraud` (mois × pays), `fact_audit_events`.

---

## 4. Chargement et démo

`bash scripts/demo.sh` démarre PostgreSQL + MongoDB (docker compose), crée les schémas ([`sql/ddl_oltp.sql`](../sql/ddl_oltp.sql), [`sql/ddl_olap.sql`](../sql/ddl_olap.sql), [`scripts/mongo_init.js`](../scripts/mongo_init.js)), charge les données (`COPY`, `mongoimport`), exécute les 30 requêtes et archive les sorties dans [`docs/results/`](results/).

| Base | Accès |
|---|---|
| OLTP | `docker exec -it stripe-postgres psql -U stripe -d stripe_oltp` (port hôte 5433) |
| OLAP | `docker exec -it stripe-postgres psql -U stripe -d stripe_olap` |
| NoSQL | `docker exec -it stripe-mongo mongosh stripe_nosql` (port hôte 27018) |
