# Étape 5 — Requêtes SQL (OLTP + OLAP)

## Fichiers disponibles dans `queries/`
- `oltp_queries.sql` — 5 requêtes opérationnelles (PostgreSQL)
- `olap_queries.sql` — 5 requêtes analytiques (Redshift / BigQuery)

---

## Requêtes OLTP

### Q1 — Transactions frauduleuses des dernières 24h

**Objectif métier :** alimenter un dashboard d'alerte en temps réel pour l'équipe risk.

**Techniques utilisées :**
- `JOIN` sur 4 tables (transactions, fraud_indicators, merchants, customers)
- Filtre temporel `NOW() - INTERVAL '24 hours'` — dynamique, pas de date en dur
- Filtre sur `risk_level IN ('high', 'critical')` — on exclut les alertes basses pour réduire le bruit

**Piège évité :** utiliser `DATE(created_at) = CURRENT_DATE` au lieu de `NOW() - INTERVAL` couperait à minuit et manquerait les fraudes du début de nuit.

---

### Q2 — Taux d'échec par merchant

**Objectif métier :** identifier les merchants avec des problèmes d'intégration ou de configuration.

**Techniques utilisées :**
- `COUNT(*) FILTER (WHERE status = 'failed')` — agrégation conditionnelle, plus lisible qu'un `SUM(CASE WHEN ...)`
- `NULLIF(COUNT(*), 0)` dans la division — protège contre la division par zéro
- `HAVING COUNT(*) >= 10` — exclut les merchants avec trop peu de volume (biais statistique)

**Piège évité :** diviser sans `NULLIF` lève une erreur si un merchant n'a aucune transaction sur la période.

---

### Q3 — Top 10 merchants par volume

**Objectif métier :** identifier les merchants stratégiques pour le support et le commercial.

**Techniques utilisées :**
- `DATE_TRUNC('month', NOW())` — tronque à minuit le 1er du mois courant
- Filtre `status = 'successful'` — on ne compte pas le volume des transactions échouées dans le revenue

**Choix discutable :** le classement est fait en devise locale (pas en USD). Pour une comparaison inter-devises, il faudrait utiliser la colonne `amount_usd` de la table OLAP — ce n'est pas disponible en OLTP.

---

### Q4 — Détection de doublons suspects

**Objectif métier :** détecter les double-clics, rejeux de requêtes HTTP et tentatives de fraude par répétition.

**Techniques utilisées :**
- **Self-JOIN** sur `transactions` : on joint la table avec elle-même pour comparer des paires de lignes
- Condition `t2.created_at > t1.created_at` — évite les paires en double (A-B et B-A)
- `t1.transaction_id <> t2.transaction_id` — évite qu'une transaction se compare à elle-même
- `INTERVAL '5 minutes'` — fenêtre temporelle configurable

**Coût de cette requête :** le self-JOIN est coûteux sur de grands volumes. En production, on ajouterait un index composite sur `(customer_id, merchant_id, amount, created_at)`.

---

### Q5 — Comptes à risque multi-paiements

**Objectif métier :** profiler les comptes qui cumulent plusieurs moyens de paiement et des signaux de fraude.

**Techniques utilisées :**
- `COUNT(DISTINCT ...)` sur plusieurs dimensions en même temps
- `HAVING` avec deux conditions agrégées
- Combinaison JOIN OLTP + fraud_indicators

---

## Requêtes OLAP

### Q1 — Revenue mensuel par pays avec cumul

**Objectif métier :** suivi de la croissance du revenue par marché géographique.

**Techniques utilisées :**
- **Window function** `SUM() OVER (PARTITION BY ... ORDER BY ... ROWS UNBOUNDED PRECEDING)` — calcule un cumul glissant du revenue depuis janvier, sans GROUP BY supplémentaire
- Jointure sur 3 dimensions (date, geography, payment_method)
- Filtre `is_fraud = false` — on exclut les transactions frauduleuses du revenue réel

**Pourquoi OLAP et pas OLTP :** ce calcul agrège des milliers de lignes sur 12 mois. En OLTP, ça bloquerait la table. En OLAP (Redshift/BigQuery), le stockage colonne permet de scanner uniquement les colonnes nécessaires.

---

### Q2 — Segmentation clients par décile

**Objectif métier :** identifier les 10% de clients qui génèrent le plus de revenue (loi de Pareto).

**Techniques utilisées :**
- **CTE** (Common Table Expression) en deux étapes : calcul du spending total, puis application du décile
- `NTILE(10)` — window function qui divise les clients en 10 groupes de taille égale selon le montant dépensé
- `SUM(SUM(...)) OVER ()` — somme de sommes : calcule le total global pour le pourcentage du revenue par décile

**Lecture du résultat :** le décile 10 (les 10% les plus dépensiers) génère typiquement 50-70% du revenue total — ce ratio est le résultat clé à mettre en avant.

---

### Q3 — Évolution du taux de fraude avec variation trimestrielle

**Objectif métier :** rapport risk management pour les équipes conformité et les régulateurs.

**Techniques utilisées :**
- `LAG()` — window function qui accède à la valeur du trimestre précédent dans la même partition géographique
- `PARTITION BY g.region ORDER BY d.year, d.quarter` — le LAG est calculé indépendamment pour chaque région
- `is_fraud::int` — cast booléen → entier pour sommer les fraudes

**Valeur analytique :** la colonne `fraud_rate_delta` permet de détecter immédiatement si une région voit son taux de fraude augmenter — signal d'alerte pour l'équipe risk.

---

### Q4 — Performance des méthodes de paiement

**Objectif métier :** guider les décisions produit sur les partenariats bancaires et l'activation des wallets.

**Techniques utilisées :**
- Double agrégation : taux de fraude ET taux de remboursement par méthode
- `is_refunded::int` et `is_fraud::int` — les booléens de la fact table sont directement sommables

**Insight attendu :** les wallets numériques (`is_digital = true`) ont généralement un taux de fraude plus bas que les cartes physiques — ce qui justifie leur mise en avant.

---

### Q5 — Cohorte de rétention merchants

**Objectif métier :** mesurer combien de merchants restent actifs mois après mois depuis leur acquisition.

**Techniques utilisées :**
- **3 CTEs chaînées** : cohort (mois d'acquisition) → activité mensuelle → calcul de rétention
- `FIRST_VALUE() OVER (PARTITION BY cohort_month ORDER BY months_since_acquisition)` — récupère la taille initiale de la cohorte (mois 0) pour calculer le taux de rétention
- `DATE_TRUNC('month', ...)` — normalise toutes les dates au 1er du mois pour comparer des mois complets
- `EXTRACT(MONTH FROM AGE(...))` — calcule l'ancienneté en mois depuis l'acquisition

**Lecture du résultat :** le mois 0 est toujours 100%. La chute entre le mois 1 et le mois 3 est le chiffre le plus important — il mesure l'activation réelle des nouveaux merchants.
