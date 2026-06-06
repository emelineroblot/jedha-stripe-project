"""
Génération de données synthétiques pour le business case Stripe.
Produit : merchants, customers, payment_methods, transactions,
          fraud_indicators, refunds (CSV) + logs (JSON).

Dépendances : pip install faker pandas numpy
"""

import os
import json
import random
import uuid
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from faker import Faker

fake = Faker()
random.seed(42)
np.random.seed(42)
Faker.seed(42)

OUTPUT_DIR = "data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Paramètres de volume ────────────────────────────────────────────────────

N_MERCHANTS   = 50
N_CUSTOMERS   = 200
N_TRANSACTIONS = 1000
FRAUD_RATE    = 0.05   # 5% de transactions frauduleuses
REFUND_RATE   = 0.03   # 3% de transactions remboursées
N_LOGS        = 300

DATE_START = datetime(2024, 1, 1)
DATE_END   = datetime(2024, 12, 31)

# ─── Données de référence ────────────────────────────────────────────────────

COUNTRIES = [
    ("FR", "France",         "Europe"),
    ("DE", "Germany",        "Europe"),
    ("GB", "United Kingdom", "Europe"),
    ("US", "United States",  "North America"),
    ("CA", "Canada",         "North America"),
    ("BR", "Brazil",         "South America"),
    ("SG", "Singapore",      "Asia"),
    ("JP", "Japan",          "Asia"),
    ("AU", "Australia",      "Oceania"),
    ("NG", "Nigeria",        "Africa"),
]

CURRENCIES = [
    ("EUR", "Euro",                "€"),
    ("USD", "US Dollar",           "$"),
    ("GBP", "British Pound",       "£"),
    ("CAD", "Canadian Dollar",     "CA$"),
    ("BRL", "Brazilian Real",      "R$"),
    ("SGD", "Singapore Dollar",    "S$"),
    ("JPY", "Japanese Yen",        "¥"),
    ("AUD", "Australian Dollar",   "A$"),
    ("NGN", "Nigerian Naira",      "₦"),
]

# Devise principale par pays
COUNTRY_CURRENCY = {
    "FR": "EUR", "DE": "EUR", "GB": "GBP", "US": "USD",
    "CA": "CAD", "BR": "BRL", "SG": "SGD", "JP": "JPY",
    "AU": "AUD", "NG": "NGN",
}

# Montant moyen par devise (ordre de grandeur réaliste)
CURRENCY_AVG_AMOUNT = {
    "EUR": 85,  "USD": 95,  "GBP": 75,  "CAD": 120,
    "BRL": 450, "SGD": 130, "JPY": 9500, "AUD": 140, "NGN": 45000,
}

MERCHANT_CATEGORIES = [
    "e-commerce", "saas", "marketplace", "food_delivery",
    "travel", "gaming", "healthcare", "education", "retail",
]

PAYMENT_TYPES = ["card", "bank_transfer", "wallet"]
CARD_BRANDS   = ["visa", "mastercard", "amex", "discover"]
DEVICE_TYPES  = ["mobile", "desktop", "tablet"]
TX_STATUSES   = ["successful", "failed", "refunded", "chargeback"]
LOG_TYPES     = ["error", "access", "audit"]
LOG_SEVERITIES = ["debug", "info", "warning", "error", "critical"]
SERVICES      = ["payment-api", "fraud-service", "auth-service", "webhook-service"]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def rand_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))

def stripe_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# ─── 1. Référentiels ─────────────────────────────────────────────────────────

df_countries = pd.DataFrame(COUNTRIES, columns=["country_code", "name", "region"])
df_currencies = pd.DataFrame(CURRENCIES, columns=["currency_code", "name", "symbol"])

country_codes   = df_countries["country_code"].tolist()
currency_codes  = df_currencies["currency_code"].tolist()


# ─── 2. Merchants ────────────────────────────────────────────────────────────

merchants = []
for _ in range(N_MERCHANTS):
    country = random.choice(country_codes)
    merchants.append({
        "merchant_id": stripe_id("mer"),
        "name":        fake.company(),
        "country_code": country,
        "category":    random.choice(MERCHANT_CATEGORIES),
        "created_at":  rand_date(datetime(2020, 1, 1), DATE_START).isoformat(),
    })

df_merchants = pd.DataFrame(merchants)
merchant_ids = df_merchants["merchant_id"].tolist()


# ─── 3. Customers ────────────────────────────────────────────────────────────

customers = []
for _ in range(N_CUSTOMERS):
    country = random.choice(country_codes)
    customers.append({
        "customer_id": stripe_id("cus"),
        "email":       fake.unique.email(),
        "country_code": country,
        "created_at":  rand_date(datetime(2021, 1, 1), DATE_START).isoformat(),
    })

df_customers = pd.DataFrame(customers)
customer_ids = df_customers["customer_id"].tolist()


# ─── 4. Payment methods ──────────────────────────────────────────────────────

payment_methods = []
assigned_customers = set()

for cus_id in customer_ids:
    n_methods = random.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
    for i in range(n_methods):
        ptype = random.choice(PAYMENT_TYPES)
        payment_methods.append({
            "payment_method_id": stripe_id("pm"),
            "customer_id":       cus_id,
            "type":              ptype,
            "brand":             random.choice(CARD_BRANDS) if ptype == "card" else None,
            "last4":             str(random.randint(1000, 9999)) if ptype == "card" else None,
            "is_default":        (i == 0),
            "created_at":        rand_date(datetime(2021, 6, 1), DATE_START).isoformat(),
        })

df_payment_methods = pd.DataFrame(payment_methods)


# ─── 5. Transactions ─────────────────────────────────────────────────────────

# Index customer → ses payment_methods
cus_to_pm = df_payment_methods.groupby("customer_id")["payment_method_id"].apply(list).to_dict()

transactions = []
for _ in range(N_TRANSACTIONS):
    cus_id  = random.choice(customer_ids)
    mer_id  = random.choice(merchant_ids)
    pm_id   = random.choice(cus_to_pm.get(cus_id, [stripe_id("pm")]))

    # Devise cohérente avec le merchant
    mer_country  = df_merchants.loc[df_merchants["merchant_id"] == mer_id, "country_code"].values[0]
    currency     = COUNTRY_CURRENCY.get(mer_country, "USD")
    avg_amount   = CURRENCY_AVG_AMOUNT.get(currency, 100)
    amount       = round(abs(np.random.normal(avg_amount, avg_amount * 0.4)), 2)

    # Statut pondéré
    status = random.choices(
        ["successful", "failed", "refunded", "chargeback"],
        weights=[0.88, 0.07, 0.03, 0.02]
    )[0]

    transactions.append({
        "transaction_id":    stripe_id("ch"),
        "merchant_id":       mer_id,
        "customer_id":       cus_id,
        "payment_method_id": pm_id,
        "amount":            amount,
        "currency_code":     currency,
        "status":            status,
        "ip_address":        fake.ipv4_public(),
        "device_type":       random.choice(DEVICE_TYPES),
        "created_at":        rand_date(DATE_START, DATE_END).isoformat(),
    })

df_transactions = pd.DataFrame(transactions)
tx_ids = df_transactions["transaction_id"].tolist()


# ─── 6. Fraud indicators ─────────────────────────────────────────────────────

# Fraude concentrée sur ~5% des transactions + toutes les chargebacks
chargeback_ids = df_transactions.loc[
    df_transactions["status"] == "chargeback", "transaction_id"
].tolist()

random_fraud_ids = random.sample(
    [t for t in tx_ids if t not in chargeback_ids],
    k=int(N_TRANSACTIONS * FRAUD_RATE)
)

flagged_ids = list(set(chargeback_ids + random_fraud_ids))

fraud_indicators = []
for tx_id in flagged_ids:
    score = round(random.uniform(0.5, 0.99), 4)
    risk  = "critical" if score > 0.9 else "high" if score > 0.75 else "medium"
    tx_date = df_transactions.loc[
        df_transactions["transaction_id"] == tx_id, "created_at"
    ].values[0]
    fraud_indicators.append({
        "fraud_id":      stripe_id("fr"),
        "transaction_id": tx_id,
        "anomaly_score": score,
        "risk_level":    risk,
        "flagged_at":    tx_date,
        "model_version": random.choice(["v2.3.0", "v2.4.1"]),
    })

df_fraud = pd.DataFrame(fraud_indicators)


# ─── 7. Refunds ──────────────────────────────────────────────────────────────

refund_ids = df_transactions.loc[
    df_transactions["status"] == "refunded", "transaction_id"
].tolist()

refunds = []
for tx_id in refund_ids:
    original_amount = df_transactions.loc[
        df_transactions["transaction_id"] == tx_id, "amount"
    ].values[0]
    # Remboursement partiel (50-100%) ou total
    refund_pct = random.choice([0.5, 0.75, 1.0])
    tx_date = df_transactions.loc[
        df_transactions["transaction_id"] == tx_id, "created_at"
    ].values[0]
    refund_date = (
        datetime.fromisoformat(tx_date) + timedelta(days=random.randint(1, 14))
    ).isoformat()

    refunds.append({
        "refund_id":      stripe_id("re"),
        "transaction_id": tx_id,
        "amount":         round(original_amount * refund_pct, 2),
        "reason":         random.choice(["customer_request", "duplicate", "fraudulent"]),
        "status":         random.choice(["processed", "pending"]),
        "created_at":     refund_date,
    })

df_refunds = pd.DataFrame(refunds)


# ─── 8. Logs (JSON pour MongoDB) ─────────────────────────────────────────────

logs = []
for _ in range(N_LOGS):
    log_type = random.choice(LOG_TYPES)
    severity = (
        random.choice(["error", "critical"])
        if log_type == "error"
        else random.choices(LOG_SEVERITIES, weights=[0.3, 0.4, 0.2, 0.08, 0.02])[0]
    )
    tx_id  = random.choice(tx_ids)
    mer_id = random.choice(merchant_ids)
    log_date = rand_date(DATE_START, DATE_END)

    logs.append({
        "_id":       f"log_{uuid.uuid4().hex[:8]}",
        "type":      log_type,
        "severity":  severity,
        "service":   random.choice(SERVICES),
        "message":   fake.sentence(nb_words=8),
        "timestamp": log_date.isoformat() + "Z",
        "metadata": {
            "transaction_id": tx_id,
            "merchant_id":    mer_id,
            "http_status":    random.choice([200, 400, 401, 404, 429, 500, 504]),
            "latency_ms":     random.randint(50, 5000),
        },
        "trace": {
            "request_id": f"req_{uuid.uuid4().hex[:8]}",
            "ip_address": fake.ipv4_public(),
            "user_agent": fake.user_agent(),
        },
        "ttl": (log_date + timedelta(days=90)).isoformat() + "Z",
    })


# ─── Export ──────────────────────────────────────────────────────────────────

df_countries.to_csv(       f"{OUTPUT_DIR}/countries.csv",        index=False)
df_currencies.to_csv(      f"{OUTPUT_DIR}/currencies.csv",       index=False)
df_merchants.to_csv(       f"{OUTPUT_DIR}/merchants.csv",        index=False)
df_customers.to_csv(       f"{OUTPUT_DIR}/customers.csv",        index=False)
df_payment_methods.to_csv( f"{OUTPUT_DIR}/payment_methods.csv",  index=False)
df_transactions.to_csv(    f"{OUTPUT_DIR}/transactions.csv",     index=False)
df_fraud.to_csv(           f"{OUTPUT_DIR}/fraud_indicators.csv", index=False)
df_refunds.to_csv(         f"{OUTPUT_DIR}/refunds.csv",          index=False)

with open(f"{OUTPUT_DIR}/logs.json", "w", encoding="utf-8") as f:
    json.dump(logs, f, indent=2, ensure_ascii=False)

# ─── Résumé ──────────────────────────────────────────────────────────────────

print("Données générées avec succès :")
print(f"  countries        : {len(df_countries)} lignes")
print(f"  currencies       : {len(df_currencies)} lignes")
print(f"  merchants        : {len(df_merchants)} lignes")
print(f"  customers        : {len(df_customers)} lignes")
print(f"  payment_methods  : {len(df_payment_methods)} lignes")
print(f"  transactions     : {len(df_transactions)} lignes")
print(f"  fraud_indicators : {len(df_fraud)} lignes")
print(f"  refunds          : {len(df_refunds)} lignes")
print(f"  logs (JSON)      : {len(logs)} documents")
print(f"\nFichiers dans : {OUTPUT_DIR}/")
