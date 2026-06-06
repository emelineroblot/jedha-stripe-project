# Étape 1 — ERD OLTP (schéma normalisé 3NF)

## Schéma disponible dans `schemas/oltp_dbdiagram.txt`

À coller sur https://dbdiagram.io pour visualiser le diagramme.

---

## Organisation des tables

Le schéma est structuré en 3 couches logiques :

```
Référence          → countries, currencies
Entités métier     → customers, merchants, payment_methods
Transactions       → transactions (table centrale)
                        ├── fraud_indicators
                        └── refunds
```

---

## Justifications des choix de modélisation

### Pourquoi ce modèle est en 3NF

- `country_code` et `currency_code` sont externalisés dans leurs propres tables de référence : évite la répétition et garantit la cohérence (un seul endroit à mettre à jour si un libellé change).
- `payment_methods` est séparé de `customers` : un client peut avoir plusieurs moyens de paiement enregistrés (carte Visa + Apple Pay par exemple). Une relation 1-N est plus propre qu'un tableau dans la colonne.
- `fraud_indicators` est séparé de `transactions` : toutes les transactions n'ont pas d'indicateur de fraude. Stocker ces colonnes dans `transactions` introduirait beaucoup de `NULL` et alourdirait la table centrale.
- `refunds` est séparé de `transactions` : une transaction peut avoir plusieurs remboursements partiels. Une relation 1-N est la seule façon de modéliser ça proprement.

---

## Justifications des types de données

| Colonne | Type choisi | Raison |
|--------|-------------|--------|
| `transaction_id` | `varchar` | Stripe utilise des IDs préfixés (`ch_3Mxxx`, `py_3Mxxx`) — un entier auto-incrémenté ne convient pas |
| `amount` | `decimal(12,2)` | Jamais `float` pour les montants financiers : les arrondis flottants causent des erreurs de centimes |
| `anomaly_score` | `decimal(5,4)` | Score entre 0 et 1 avec 4 décimales de précision (ex : 0.9873) |
| `status` | `varchar` | Plus flexible qu'un `ENUM` : pas besoin de migration si un nouveau statut est ajouté |
| `last4` | `char(4)` | Longueur fixe, jamais plus de 4 chiffres — `char` est légèrement plus performant que `varchar` ici |
| `is_default` | `boolean` | Flag simple, valeur par défaut `false` |

---

## Contraintes d'intégrité

- Toutes les clés primaires sont en `varchar` pour correspondre aux IDs Stripe.
- Les foreign keys garantissent l'intégrité référentielle : impossible d'insérer une transaction pour un merchant inexistant.
- `email` sur `customers` est `unique` : évite les doublons de compte client.
- `created_at` est `not null` sur toutes les tables : nécessaire pour toute analyse temporelle.

---

## Ce que ce modèle supporte

- **ACID** : PostgreSQL garantit les propriétés transactionnelles sur ce schéma normalisé.
- **Réplication** : le schéma est compatible avec la réplication logique PostgreSQL (utilisée pour le CDC vers l'OLAP).
- **Scalabilité** : la table `transactions` peut être partitionnée par `created_at` (partitionnement par range) pour gérer des milliards de lignes.
- **Fraud detection en temps réel** : `fraud_indicators` est conçu pour être alimenté par un modèle ML externe qui écrit ses scores via l'API.
