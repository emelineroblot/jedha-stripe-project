# Étape 4 — Génération de données synthétiques

## Script disponible dans `generate_data.py`
## Données générées dans `data/`

---

## Ce qui a été généré

| Fichier | Lignes / docs | Description |
|---------|--------------|-------------|
| `countries.csv` | 10 | Référentiel pays |
| `currencies.csv` | 9 | Référentiel devises |
| `merchants.csv` | 50 | Entreprises clientes de Stripe |
| `customers.csv` | 200 | Clients des merchants |
| `payment_methods.csv` | 322 | Moyens de paiement (1-3 par client) |
| `transactions.csv` | 1 000 | Transactions financières |
| `fraud_indicators.csv` | ~63 | Indicateurs de fraude (~6%) |
| `refunds.csv` | ~41 | Remboursements (~4%) |
| `logs.json` | 300 | Documents MongoDB |

---

## Justifications des choix de génération

### Seed fixe (`random.seed(42)`, `Faker.seed(42)`)

Toutes les sources d'aléatoire sont initialisées avec la même graine. Cela garantit que le script produit exactement les mêmes données à chaque exécution — indispensable pour la reproductibilité des analyses et des requêtes.

### Montants par devise : `np.random.normal`

Les montants ne sont pas uniformes : ils suivent une distribution normale centrée sur le montant moyen typique de chaque devise.

```python
avg_amount = CURRENCY_AVG_AMOUNT.get(currency, 100)
amount = round(abs(np.random.normal(avg_amount, avg_amount * 0.4)), 2)
```

**Pourquoi :** une distribution uniforme entre 0 et 1000€ n'est pas réaliste. En réalité, la majorité des transactions e-commerce se situe autour d'un montant moyen avec quelques outliers — ce que la loi normale modélise bien. Le `abs()` évite les montants négatifs.

### Devise cohérente avec le pays du merchant

La devise est déterminée par le pays du merchant (pas du client) via `COUNTRY_CURRENCY`. Un merchant français facture en EUR même si son client est américain.

**Pourquoi :** c'est le comportement réel de Stripe — c'est le merchant qui choisit sa devise de facturation.

### Statuts pondérés avec `random.choices`

```python
status = random.choices(
    ["successful", "failed", "refunded", "chargeback"],
    weights=[0.88, 0.07, 0.03, 0.02]
)[0]
```

**Pourquoi :** dans la réalité Stripe, ~88% des transactions aboutissent. Un tirage uniforme donnerait 25% d'échecs — irréaliste et biaisé pour les requêtes analytiques.

### Fraude concentrée sur les chargebacks

Toutes les transactions `chargeback` reçoivent automatiquement un `fraud_indicator`. Des fraudes supplémentaires sont ajoutées sur des transactions `successful` (fraudes non encore détectées).

**Pourquoi :** un chargeback est par définition une transaction contestée — il serait incohérent qu'elle n'ait pas d'indicateur de fraude. Les fraudes sur transactions réussies simulent les cas où le modèle détecte a posteriori.

### Remboursements partiels avec délai réaliste

```python
refund_pct = random.choice([0.5, 0.75, 1.0])
refund_date = tx_date + timedelta(days=random.randint(1, 14))
```

**Pourquoi :** en pratique, Stripe supporte les remboursements partiels (ex : remboursement d'un seul article d'une commande multi-produits). Le délai de 1-14 jours correspond au temps de traitement habituel.

### `payment_methods` : 1 à 3 méthodes par client

```python
n_methods = random.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
```

**Pourquoi :** 60% des utilisateurs n'ont qu'une carte enregistrée, 30% en ont deux (carte perso + carte pro), 10% en ont trois. Modélise la réalité d'un wallet multi-cartes.

---

## Dépendances

```bash
pip install faker pandas numpy
```

| Librairie | Usage |
|-----------|-------|
| `faker` | Génération de noms, emails, IPs, user-agents réalistes |
| `pandas` | Construction des DataFrames et export CSV |
| `numpy` | Distribution normale pour les montants, seed reproductible |
| `uuid` | Génération des IDs au format Stripe (`ch_xxxx`, `cus_xxxx`) |
