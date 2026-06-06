# Étape 6 — Requêtes NoSQL (MongoDB)

## Fichier disponible dans `queries/nosql_queries.js`

---

## Vue d'ensemble

Toutes les requêtes utilisent le framework d'**agrégation MongoDB** (`aggregate()`), qui est le mécanisme standard pour les requêtes analytiques. Il fonctionne comme un pipeline : chaque stage transforme les documents et passe le résultat au stage suivant.

```
Collection → $match → $group → $addFields → $project → $sort → résultat
```

---

## Q1 — Logs critiques des dernières 24h par service

**Objectif métier :** détecter rapidement les services en échec pour le monitoring ops.

**Stages utilisés :**
- `$match` — filtre sur `severity` et `timestamp` avant l'agrégation (économise la mémoire)
- `$group` — regroupe par couple `(service, severity)` et calcule les métriques
- `$addFields` + `$slice` — limite les exemples de messages à 3 par groupe pour ne pas saturer le résultat

**Ordre important :** `$match` en premier est une règle fondamentale en MongoDB. Filtrer tôt réduit le nombre de documents à traiter dans les stages suivants. Inverser l'ordre (`$group` puis `$match`) forcerait MongoDB à agréger tous les logs avant de filtrer — beaucoup plus lent.

**Index TTL** : la collection `logs` a un index TTL sur le champ `ttl`. MongoDB supprime automatiquement les documents expirés sans job de nettoyage manuel.

---

## Q2 — Sessions suspectes sans transaction

**Objectif métier :** détecter les bots et scrapers qui naviguent intensément sans jamais payer.

**Stages utilisés :**
- `$match` sur `transaction_id: { $exists: false }` — sélectionne uniquement les sessions sans transaction associée
- `$addFields` + `$size` — calcule le nombre total d'events et le nombre d'events sur `/checkout`
- `$filter` — filtre le tableau `events` pour ne garder que ceux dont `page = "/checkout"`, puis `$size` compte le résultat

**Pourquoi `$filter` et pas une requête sur le tableau entier :**
MongoDB permet de requêter à l'intérieur des documents embarqués. `$filter` est l'équivalent d'un `WHERE` sur un tableau imbriqué — c'est l'un des avantages majeurs du modèle document par rapport au SQL pour ce type de données.

**Ce qu'on ne pourrait pas faire facilement en SQL :**
En SQL, les events seraient dans une table séparée `session_events` avec une foreign key. La même requête nécessiterait un sous-GROUP BY sur `session_events` puis une jointure — plus de code, moins lisible.

---

## Q3 — Features ML des transactions à haut risque

**Objectif métier :** auditer les décisions du modèle de fraude et identifier les facteurs déclencheurs.

**Stages utilisés :**
- `$match` sur un sous-document imbriqué `"prediction.risk_level"` — MongoDB supporte la notation pointée pour filtrer sur des champs imbriqués
- `$project` avec la notation pointée `"$features.velocity_score"` — extrait et renomme les champs du sous-document pour les remonter à la racine du résultat

**Versioning du modèle :**
Le champ `model_version` permet de filtrer les prédictions d'une version spécifique. En pratique, on peut comparer les taux de fraude entre `v2.3.0` et `v2.4.1` pour valider qu'une nouvelle version du modèle améliore bien les performances.

**Lien avec l'OLTP :**
`transaction_id` est le champ de jointure vers PostgreSQL. Si on veut enrichir le résultat avec les détails de la transaction (montant, merchant), on fait la jointure côté applicatif ou dans le pipeline ETL — pas dans MongoDB.

---

## Q4 — Feedbacks négatifs par merchant

**Objectif métier :** prioriser les actions support et identifier les pain points récurrents.

**Stage complexe : aplatissement des tags avec `$reduce`**

```javascript
all_tags_flat: {
  $reduce: {
    input: "$all_tags",       // tableau de tableaux : [[tag1, tag2], [tag3]]
    initialValue: [],
    in: { $concatArrays: ["$$value", "$$this"] }  // résultat : [tag1, tag2, tag3]
  }
}
```

Le `$group` accumule tous les tableaux de tags dans `all_tags` avec `$push` — ce qui crée un tableau de tableaux. `$reduce` les aplatit en un seul tableau, équivalent d'un `flatten()` en Python.

**Pourquoi `$setUnion` pour les top tags :**
`$setUnion` déduplique les éléments du tableau avant le `$slice`. Sans cela, on afficherait plusieurs fois le même tag si tous les feedbacks le mentionnent.

**Limite de cette approche :**
`$setUnion` ne compte pas les occurrences — il déduplique. Pour un vrai comptage de fréquence des tags (ex : "checkout" apparaît 12 fois), il faudrait un `$unwind` suivi d'un `$group` sur le tag. La requête a été simplifiée pour rester lisible.

---

## Q5 — Latence P95 par service

**Objectif métier :** mesurer le respect des SLA et identifier les services à optimiser.

**Opérateur `$percentile` :**
Disponible depuis **MongoDB 7.0**. Calcule un percentile approché sur un groupe de valeurs.

```javascript
p95_approx: {
  $percentile: {
    input:  "$metadata.latency_ms",
    p:      [0.95],          // liste de percentiles à calculer
    method: "approximate"    // algorithme t-digest (rapide, léger en mémoire)
  }
}
```

**Pourquoi le P95 plutôt que la moyenne :**
La moyenne est biaisée par les outliers (une requête à 30 000ms tire la moyenne vers le haut). Le P95 (95e percentile) répond à la question : "95% de mes requêtes sont résolues en moins de combien de millisecondes ?" — c'est la métrique standard pour les SLA.

**Compatibilité :** si la version MongoDB est < 7.0, remplacer par un `$sort` + `$group` avec `$push` + `$arrayElemAt` pour approximer le percentile manuellement.
