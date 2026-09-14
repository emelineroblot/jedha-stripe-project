"""
Génération de données synthétiques pour le business case Stripe.

OLTP (CSV, PostgreSQL)   : countries, currencies, exchange_rates, merchants, customers,
                           payment_methods, products, subscriptions, transactions,
                           refunds, disputes, fraud_indicators, audit_logs
NoSQL (JSON, MongoDB)    : logs, user_sessions, ml_features, customer_feedback, recommendations

Particularités :
  - Dates relatives à aujourd'hui (12 derniers mois) → les requêtes NOW() renvoient des résultats.
  - Fraude corrélée à des signaux (géo-mismatch, nuit, montant anormal, vélocité, IP) →
    un modèle ML peut réellement apprendre quelque chose sur ces données.
  - Label delay : les disputes (chargebacks) arrivent 20-90 jours après la transaction.
  - JSON MongoDB en Extended JSON ({"$date": ...}) → vrais BSON Date après mongoimport
    (indispensable pour les index TTL et les comparaisons de dates).

Usage : python generate_data.py [--scale 1] [--seed 42] [--out data]
"""

import argparse
import hashlib
import json
import math
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Console Windows en cp1252 : forcer l'UTF-8 pour les accents du résumé
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
from faker import Faker

# ─── CLI ─────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Génère les données synthétiques Stripe")
parser.add_argument("--scale", type=float, default=1.0, help="1.0 = 1 000 transactions")
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--out", default="data")
args = parser.parse_args()

fake = Faker()
random.seed(args.seed)
np.random.seed(args.seed)
Faker.seed(args.seed)

OUTPUT_DIR = args.out
MONGO_DIR = os.path.join(OUTPUT_DIR, "mongo")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(MONGO_DIR, exist_ok=True)

# ─── Volumes ─────────────────────────────────────────────────────────────────

S = args.scale
N_MERCHANTS      = max(10, int(50 * S))
N_CUSTOMERS      = max(40, int(200 * S))
N_TRANSACTIONS   = int(1000 * S)
N_SUBSCRIPTIONS  = max(10, int(80 * S))
N_LOGS           = int(400 * S)
N_SESSIONS       = int(500 * S)
N_FEEDBACK       = int(150 * S)
N_AUDIT          = int(300 * S)

NOW        = datetime.now(timezone.utc).replace(microsecond=0)
DATE_END   = NOW
DATE_START = NOW - timedelta(days=365)

# ─── Référentiels ────────────────────────────────────────────────────────────

COUNTRIES = [
    # code, name, region, data_region, gdpr
    ("FR", "France",         "Europe",        "EU",   True),
    ("DE", "Germany",        "Europe",        "EU",   True),
    ("ES", "Spain",          "Europe",        "EU",   True),
    ("GB", "United Kingdom", "Europe",        "EU",   True),
    ("US", "United States",  "North America", "US",   False),
    ("CA", "Canada",         "North America", "US",   False),
    ("BR", "Brazil",         "South America", "US",   False),
    ("SG", "Singapore",      "Asia",          "APAC", False),
    ("JP", "Japan",          "Asia",          "APAC", False),
    ("AU", "Australia",      "Oceania",       "APAC", False),
    ("NG", "Nigeria",        "Africa",        "EU",   False),
]
COUNTRY_CODES = [c[0] for c in COUNTRIES]

CURRENCIES = [
    # code, name, symbol, decimals, base rate → USD
    ("USD", "US Dollar",         "$",   2, 1.00),
    ("EUR", "Euro",              "€",   2, 1.08),
    ("GBP", "British Pound",     "£",   2, 1.27),
    ("CAD", "Canadian Dollar",   "CA$", 2, 0.74),
    ("BRL", "Brazilian Real",    "R$",  2, 0.18),
    ("SGD", "Singapore Dollar",  "S$",  2, 0.75),
    ("JPY", "Japanese Yen",      "¥",   0, 0.0067),
    ("AUD", "Australian Dollar", "A$",  2, 0.66),
    ("NGN", "Nigerian Naira",    "₦",   2, 0.00065),
]
CURRENCY_DECIMALS = {c[0]: c[3] for c in CURRENCIES}
CURRENCY_BASE_RATE = {c[0]: c[4] for c in CURRENCIES}

COUNTRY_CURRENCY = {
    "FR": "EUR", "DE": "EUR", "ES": "EUR", "GB": "GBP", "US": "USD", "CA": "CAD",
    "BR": "BRL", "SG": "SGD", "JP": "JPY", "AU": "AUD", "NG": "NGN",
}
# Montant moyen d'un achat ponctuel par devise
CURRENCY_AVG_AMOUNT = {
    "EUR": 85, "USD": 95, "GBP": 75, "CAD": 120, "BRL": 450,
    "SGD": 130, "JPY": 9500, "AUD": 140, "NGN": 45000,
}

MERCHANT_CATEGORIES = {
    # category: (mcc, produits types)
    "e-commerce":    ("5399", ["T-shirt", "Sneakers", "Backpack", "Headphones", "Lamp"]),
    "saas":          ("5734", ["Starter plan", "Pro plan", "Team plan", "Enterprise plan"]),
    "marketplace":   ("5999", ["Listing fee", "Premium listing", "Featured slot"]),
    "food_delivery": ("5812", ["Meal", "Family menu", "Dessert", "Drink"]),
    "travel":        ("4722", ["Flight", "Hotel night", "Car rental", "Insurance"]),
    "gaming":        ("7994", ["Gold pack", "Season pass", "Skin bundle"]),
    "healthcare":    ("8099", ["Consultation", "Lab test", "Monthly plan"]),
    "education":     ("8299", ["Course", "Bootcamp", "Monthly membership"]),
    "retail":        ("5311", ["Jacket", "Shoes", "Watch", "Perfume"]),
}
SUBSCRIPTION_PRODUCTS = {"plan", "pass", "membership", "Monthly"}

PAYMENT_TYPES  = ["card", "bank_transfer", "wallet"]
CARD_BRANDS    = ["visa", "mastercard", "amex", "discover"]
WALLET_BRANDS  = ["apple_pay", "google_pay", "paypal"]
DEVICE_TYPES   = ["mobile", "desktop", "tablet"]
FAILURE_REASONS = ["insufficient_funds", "card_declined", "expired_card", "processing_error", "authentication_required"]
LOG_SERVICES   = ["payment-api", "fraud-service", "auth-service", "webhook-service", "checkout-web"]
MODEL_VERSION  = "fraud-xgb-v2.4.1"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def rand_dt(start: datetime, end: datetime) -> datetime:
    delta = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, max(delta, 1)))

def stripe_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def mdate(dt: datetime | None):
    """Extended JSON date pour mongoimport → BSON Date."""
    return None if dt is None else {"$date": iso(dt)}

def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))

def round_amount(value: float, currency: str) -> float:
    return round(max(value, 1.0), CURRENCY_DECIMALS[currency])

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def truncate_ip(ip: str) -> str:
    return ".".join(ip.split(".")[:3]) + ".0"

def weighted_hour() -> int:
    # Activité faible la nuit (0-5h), forte 10-22h
    weights = [1, 1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 7, 6, 4, 2]
    return random.choices(range(24), weights=weights)[0]


# ─── 1. Référentiels ─────────────────────────────────────────────────────────

df_countries = pd.DataFrame(COUNTRIES, columns=["country_code", "name", "region", "data_region", "gdpr_applies"])
df_currencies = pd.DataFrame(
    [(c[0], c[1], c[2], c[3]) for c in CURRENCIES],
    columns=["currency_code", "name", "symbol", "decimal_places"],
)

# Taux de change quotidiens : marche aléatoire ±0.3 % autour du taux de base
exchange_rates = []
rate_lookup: dict[tuple[str, str], float] = {}
n_days = (DATE_END.date() - DATE_START.date()).days + 1
for code, base in CURRENCY_BASE_RATE.items():
    rate = base
    for d in range(n_days):
        day = DATE_START.date() + timedelta(days=d)
        if code != "USD":
            rate = rate * (1 + np.random.normal(0, 0.003))
        exchange_rates.append({"rate_date": day.isoformat(), "currency_code": code,
                               "rate_to_usd": round(rate, 8), "source": "ECB"})
        rate_lookup[(day.isoformat(), code)] = rate
df_exchange_rates = pd.DataFrame(exchange_rates)


# ─── 2. Merchants ────────────────────────────────────────────────────────────

merchants = []
for _ in range(N_MERCHANTS):
    category = random.choice(list(MERCHANT_CATEGORIES))
    created = rand_dt(DATE_START - timedelta(days=900), DATE_START + timedelta(days=200))
    merchants.append({
        "merchant_id":  stripe_id("mer"),
        "name":         fake.company(),
        "country_code": random.choice(COUNTRY_CODES),
        "category":     category,
        "mcc":          MERCHANT_CATEGORIES[category][0],
        "status":       random.choices(["active", "restricted", "closed"], weights=[0.92, 0.05, 0.03])[0],
        "created_at":   iso(created),
        "updated_at":   iso(created),
    })
df_merchants = pd.DataFrame(merchants)
merchant_ids = df_merchants["merchant_id"].tolist()
merchant_by_id = {m["merchant_id"]: m for m in merchants}


# ─── 3. Customers ────────────────────────────────────────────────────────────

customers = []
for _ in range(N_CUSTOMERS):
    created = rand_dt(DATE_START - timedelta(days=700), DATE_START + timedelta(days=300))
    customers.append({
        "customer_id":  stripe_id("cus"),
        "email":        fake.unique.email(),
        "country_code": random.choice(COUNTRY_CODES),
        "created_at":   iso(created),
        "updated_at":   iso(created),
        "deleted_at":   None,
    })
df_customers = pd.DataFrame(customers)
customer_ids = df_customers["customer_id"].tolist()
customer_by_id = {c["customer_id"]: c for c in customers}


# ─── 4. Payment methods ──────────────────────────────────────────────────────

# Quelques empreintes de carte partagées entre comptes (signal de fraude "même carte, plusieurs comptes")
shared_fingerprints = [uuid.uuid4().hex[:20] for _ in range(5)]

payment_methods = []
for cus_id in customer_ids:
    n_methods = random.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
    for i in range(n_methods):
        ptype = random.choices(PAYMENT_TYPES, weights=[0.75, 0.10, 0.15])[0]
        if ptype == "card":
            brand = random.choice(CARD_BRANDS)
            fingerprint = random.choice(shared_fingerprints) if random.random() < 0.03 else uuid.uuid4().hex[:20]
        elif ptype == "wallet":
            brand, fingerprint = random.choice(WALLET_BRANDS), None
        else:
            brand, fingerprint = None, None
        payment_methods.append({
            "payment_method_id": stripe_id("pm"),
            "customer_id":       cus_id,
            "type":              ptype,
            "brand":             brand,
            "last4":             f"{random.randint(0, 9999):04d}" if ptype == "card" else None,
            "token":             f"tok_{uuid.uuid4().hex[:24]}",
            "fingerprint":       fingerprint,
            "exp_month":         random.randint(1, 12) if ptype == "card" else None,
            "exp_year":          random.randint(NOW.year, NOW.year + 5) if ptype == "card" else None,
            "is_default":        (i == 0),
            "created_at":        iso(rand_dt(DATE_START - timedelta(days=600), DATE_START)),
        })
df_payment_methods = pd.DataFrame(payment_methods)
pm_by_customer = df_payment_methods.groupby("customer_id")["payment_method_id"].apply(list).to_dict()
pm_by_id = {p["payment_method_id"]: p for p in payment_methods}


# ─── 5. Products ─────────────────────────────────────────────────────────────

products = []
for m in merchants:
    currency = COUNTRY_CURRENCY[m["country_code"]]
    names = random.sample(MERCHANT_CATEGORIES[m["category"]][1], k=min(3, len(MERCHANT_CATEGORIES[m["category"]][1])))
    for name in names:
        is_sub = any(k in name for k in SUBSCRIPTION_PRODUCTS)
        base = CURRENCY_AVG_AMOUNT[currency]
        products.append({
            "product_id":       stripe_id("prod"),
            "merchant_id":      m["merchant_id"],
            "name":             name,
            "unit_price":       round_amount(abs(np.random.normal(base, base * 0.5)), currency),
            "currency_code":    currency,
            "billing_interval": random.choice(["month", "year"]) if is_sub else None,
            "active":           random.random() > 0.05,
            "created_at":       m["created_at"],
        })
df_products = pd.DataFrame(products)
products_by_merchant = df_products.groupby("merchant_id")["product_id"].apply(list).to_dict()
product_by_id = {p["product_id"]: p for p in products}
subscription_products = [p for p in products if p["billing_interval"]]


# ─── 6. Subscriptions ────────────────────────────────────────────────────────

subscriptions = []
for _ in range(N_SUBSCRIPTIONS):
    prod = random.choice(subscription_products)
    created = rand_dt(DATE_START, DATE_END - timedelta(days=30))
    status = random.choices(["active", "past_due", "canceled"], weights=[0.75, 0.10, 0.15])[0]
    period_days = 30 if prod["billing_interval"] == "month" else 365
    # Dernière période en cours
    elapsed_periods = max(0, (DATE_END - created).days // period_days)
    period_start = created + timedelta(days=elapsed_periods * period_days)
    canceled_at = rand_dt(created + timedelta(days=period_days), DATE_END) if status == "canceled" else None
    subscriptions.append({
        "subscription_id":      stripe_id("sub"),
        "customer_id":          random.choice(customer_ids),
        "merchant_id":          prod["merchant_id"],
        "product_id":           prod["product_id"],
        "status":               status,
        "current_period_start": period_start.date().isoformat(),
        "current_period_end":   (period_start + timedelta(days=period_days)).date().isoformat(),
        "canceled_at":          iso(canceled_at),
        "created_at":           iso(created),
        "updated_at":           iso(canceled_at or period_start),
        "_created_dt":          created,
        "_canceled_dt":         canceled_at,
        "_period_days":         period_days,
    })
df_subscriptions = pd.DataFrame(subscriptions).drop(columns=["_created_dt", "_canceled_dt", "_period_days"])


# ─── 7. Transactions (+ signaux de fraude) ───────────────────────────────────

def make_transaction(cus_id, mer_id, created, amount=None, product_id=None, subscription_id=None):
    merchant = merchant_by_id[mer_id]
    customer = customer_by_id[cus_id]
    currency = COUNTRY_CURRENCY[merchant["country_code"]]
    if amount is None:
        base = CURRENCY_AVG_AMOUNT[currency]
        # Distribution log-normale : beaucoup de petits montants, quelques gros tickets
        amount = round_amount(np.random.lognormal(math.log(base), 0.6), currency)
    pm_id = random.choice(pm_by_customer[cus_id])
    geo_country = customer["country_code"] if random.random() < 0.90 else random.choice(COUNTRY_CODES)
    ip = fake.ipv4_public()
    return {
        "transaction_id":    stripe_id("ch"),
        "merchant_id":       mer_id,
        "customer_id":       cus_id,
        "payment_method_id": pm_id,
        "product_id":        product_id,
        "subscription_id":   subscription_id,
        "amount":            amount,
        "currency_code":     currency,
        "ip_address":        ip,
        "geo_country_code":  geo_country,
        "geo_city":          fake.city(),
        "device_type":       random.choices(DEVICE_TYPES, weights=[0.55, 0.35, 0.10])[0],
        "idempotency_key":   uuid.uuid4().hex,
        "_created_dt":       created,
    }

transactions = []

# 7a. Charges récurrentes des abonnements
for sub in subscriptions:
    prod = product_by_id[sub["product_id"]]
    t = sub["_created_dt"]
    end = sub["_canceled_dt"] or DATE_END
    while t <= end:
        transactions.append(make_transaction(
            sub["customer_id"], sub["merchant_id"], t,
            amount=prod["unit_price"], product_id=prod["product_id"], subscription_id=sub["subscription_id"],
        ))
        t += timedelta(days=sub["_period_days"])

# 7b. Paiements ponctuels
n_oneoff = max(0, N_TRANSACTIONS - len(transactions))
for _ in range(n_oneoff):
    cus_id = random.choice(customer_ids)
    mer_id = random.choice(merchant_ids)
    created = rand_dt(DATE_START, DATE_END).replace(hour=weighted_hour())
    prod_id = random.choice(products_by_merchant[mer_id]) if random.random() < 0.7 else None
    amount = product_by_id[prod_id]["unit_price"] if prod_id else None
    transactions.append(make_transaction(cus_id, mer_id, created, amount=amount, product_id=prod_id))

# 7c. Rafales de vélocité (bots / cartes testées) : 8 clients, 4-6 transactions en < 1 h
for cus_id in random.sample(customer_ids, k=max(2, int(8 * S))):
    mer_id = random.choice(merchant_ids)
    start = rand_dt(DATE_START, DATE_END)
    for k in range(random.randint(4, 6)):
        transactions.append(make_transaction(cus_id, mer_id, start + timedelta(minutes=random.randint(0, 55))))

transactions.sort(key=lambda t: t["_created_dt"])

# Signaux de fraude — calculés dans l'ordre chronologique
seen_devices: dict[str, set] = {}
recent_by_customer: dict[str, list] = {}
tx_by_id = {}

for tx in transactions:
    cus_id, created = tx["customer_id"], tx["_created_dt"]
    currency = tx["currency_code"]
    avg = CURRENCY_AVG_AMOUNT[currency]
    z = (tx["amount"] - avg) / (avg * 0.6)

    recent = [d for d in recent_by_customer.get(cus_id, []) if created - d <= timedelta(hours=1)]
    velocity_1h = len(recent)
    recent_24h = len([d for d in recent_by_customer.get(cus_id, []) if created - d <= timedelta(hours=24)])
    recent_by_customer.setdefault(cus_id, []).append(created)

    device_key = f"{cus_id}:{tx['device_type']}"
    device_seen = device_key in seen_devices.setdefault(cus_id, set())
    seen_devices[cus_id].add(device_key)

    geo_mismatch = tx["geo_country_code"] != customer_by_id[cus_id]["country_code"]
    night = created.hour < 6
    ip_reputation = round(min(1.0, abs(np.random.normal(0.1, 0.12))) if random.random() > 0.05 else random.uniform(0.6, 1.0), 3)
    shared_card = pm_by_id[tx["payment_method_id"]]["fingerprint"] in shared_fingerprints

    # Vérité terrain (latente) : logit pondéré des signaux + bruit
    logit = (-4.2 + 2.4 * geo_mismatch + 0.9 * night + 0.7 * max(0.0, z - 1.5)
             + 1.6 * (velocity_1h >= 3) + 0.8 * (not device_seen) + 3.0 * ip_reputation
             + 1.8 * shared_card + np.random.normal(0, 0.6))
    true_fraud = random.random() < sigmoid(logit)

    # Prédiction du modèle : même information, bruit différent → détection imparfaite
    fraud_probability = round(sigmoid(logit + np.random.normal(0, 0.9)), 4)
    risk_level = ("critical" if fraud_probability >= 0.85 else "high" if fraud_probability >= 0.6
                  else "medium" if fraud_probability >= 0.3 else "low")
    action = {"low": "allow", "medium": "challenge_3ds", "high": "review", "critical": "block"}[risk_level]

    if action == "block":
        status, failure_reason = "failed", "fraudulent"
    elif random.random() < 0.07:
        status, failure_reason = "failed", random.choice(FAILURE_REASONS)
    else:
        status, failure_reason = "succeeded", None

    tx.update({
        "status": status,
        "failure_reason": failure_reason,
        "created_at": iso(created),
        "updated_at": iso(created),
        "_features": {
            "amount_zscore":          round(float(z), 3),
            "transactions_last_1h":   velocity_1h,
            "transactions_last_24h":  recent_24h,
            "avg_amount_30d":         round(float(avg * random.uniform(0.7, 1.3)), 2),
            "distinct_countries_30d": 2 if geo_mismatch else 1,
            "geo_mismatch":           bool(geo_mismatch),
            "night_transaction":      bool(night),
            "velocity_score":         round(min(1.0, velocity_1h / 5), 3),
            "device_seen_before":     bool(device_seen),
            "shared_card_fingerprint": bool(shared_card),
            "ip_reputation_score":    ip_reputation,
        },
        "_true_fraud": true_fraud,
        "_fraud_probability": fraud_probability,
        "_risk_level": risk_level,
        "_action": action,
    })
    tx_by_id[tx["transaction_id"]] = tx

TX_COLUMNS = ["transaction_id", "merchant_id", "customer_id", "payment_method_id", "product_id",
              "subscription_id", "amount", "currency_code", "status", "failure_reason", "ip_address",
              "geo_country_code", "geo_city", "device_type", "idempotency_key", "created_at", "updated_at"]
df_transactions = pd.DataFrame(transactions)[TX_COLUMNS]


# ─── 8. Fraud indicators (OLTP : risque >= medium uniquement) ────────────────

fraud_indicators = []
for tx in transactions:
    if tx["_risk_level"] == "low":
        continue
    fraud_indicators.append({
        "fraud_id":       stripe_id("fr"),
        "transaction_id": tx["transaction_id"],
        "anomaly_score":  tx["_fraud_probability"],
        "risk_level":     tx["_risk_level"],
        "action_taken":   tx["_action"],
        "model_version":  MODEL_VERSION,
        "flagged_at":     iso(tx["_created_dt"] + timedelta(milliseconds=random.randint(20, 180))),
    })
df_fraud = pd.DataFrame(fraud_indicators)


# ─── 9. Refunds ──────────────────────────────────────────────────────────────

succeeded = [tx for tx in transactions if tx["status"] == "succeeded"]
refunds = []
for tx in random.sample(succeeded, k=int(len(succeeded) * 0.04)):
    refund_dt = tx["_created_dt"] + timedelta(days=random.randint(1, 14))
    if refund_dt > DATE_END:
        continue
    pct = random.choice([0.5, 0.75, 1.0])
    refunds.append({
        "refund_id":      stripe_id("re"),
        "transaction_id": tx["transaction_id"],
        "amount":         round_amount(tx["amount"] * pct, tx["currency_code"]),
        "reason":         random.choices(["requested_by_customer", "duplicate", "fraudulent"], weights=[0.7, 0.2, 0.1])[0],
        "status":         random.choices(["succeeded", "pending", "failed"], weights=[0.9, 0.07, 0.03])[0],
        "created_at":     iso(refund_dt),
    })
df_refunds = pd.DataFrame(refunds)


# ─── 10. Disputes (chargebacks, label delay 20-90 j) ─────────────────────────

disputes = []
dispute_by_tx = {}
for tx in succeeded:
    if tx["_true_fraud"]:
        will_dispute = random.random() < 0.65
    else:
        will_dispute = random.random() < 0.006
    if not will_dispute:
        continue
    opened = tx["_created_dt"] + timedelta(days=random.randint(20, 90))
    if opened > DATE_END:
        continue  # pas encore arrivé : la fraude n'est pas encore connue
    resolved = opened + timedelta(days=random.randint(15, 45))
    if resolved <= DATE_END:
        status = "lost" if (tx["_true_fraud"] and random.random() < 0.8) or random.random() < 0.3 else "won"
    else:
        status, resolved = random.choice(["needs_response", "under_review"]), None
    reason = random.choices(["fraudulent", "product_not_received", "duplicate", "credit_not_processed"],
                            weights=[0.8, 0.1, 0.05, 0.05] if tx["_true_fraud"] else [0.2, 0.4, 0.2, 0.2])[0]
    d = {
        "dispute_id":      stripe_id("dp"),
        "transaction_id":  tx["transaction_id"],
        "amount":          tx["amount"],
        "reason":          reason,
        "status":          status,
        "evidence_due_by": (opened + timedelta(days=7)).date().isoformat(),
        "opened_at":       iso(opened),
        "resolved_at":     iso(resolved),
    }
    disputes.append(d)
    dispute_by_tx[tx["transaction_id"]] = d
df_disputes = pd.DataFrame(disputes)


# ─── 11. Audit logs ──────────────────────────────────────────────────────────

AUDIT_USERS = [("u_alice", "analyst"), ("u_bob", "data_engineer"), ("u_carol", "compliance"),
               ("u_dave", "admin"), ("svc_scoring", "service"), ("u_eve", "readonly")]
audit_logs = []
for _ in range(N_AUDIT):
    user, role = random.choice(AUDIT_USERS)
    event = random.choices(["SELECT", "INSERT", "UPDATE", "DELETE", "LOGIN", "LOGIN_FAILED", "EXPORT", "ERASURE"],
                           weights=[0.45, 0.2, 0.08, 0.02, 0.12, 0.03, 0.06, 0.04])[0]
    table = None if event.startswith("LOGIN") else random.choices(
        ["transactions", "customers", "payment_methods", "merchants", "refunds", "disputes"],
        weights=[0.4, 0.2, 0.15, 0.1, 0.1, 0.05])[0]
    audit_logs.append({
        "audit_id":   stripe_id("aud"),
        "event_type": event,
        "table_name": table,
        "record_id":  random.choice(customer_ids) if table == "customers" else None,
        "user_id":    user,
        "user_role":  role,
        "ip_address": fake.ipv4_private(),
        "query_hash": uuid.uuid4().hex[:16],
        "created_at": iso(rand_dt(DATE_START, DATE_END)),
    })
df_audit = pd.DataFrame(audit_logs)


# ─── 12. MongoDB — ml_features ───────────────────────────────────────────────

ml_features = []
for tx in transactions:
    d = dispute_by_tx.get(tx["transaction_id"])
    age_days = (DATE_END - tx["_created_dt"]).days
    if d and d["status"] == "lost":
        label = {"is_fraud": True, "source": "chargeback", "labeled_at": mdate(datetime.fromisoformat(d["resolved_at"].replace("Z", "+00:00")))}
    elif d and d["status"] == "won":
        label = {"is_fraud": False, "source": "dispute_won", "labeled_at": mdate(datetime.fromisoformat(d["resolved_at"].replace("Z", "+00:00")))}
    elif age_days > 120 and tx["status"] == "succeeded":
        label = {"is_fraud": False, "source": "matured_no_dispute", "labeled_at": mdate(tx["_created_dt"] + timedelta(days=120))}
    else:
        label = {"is_fraud": None, "source": None, "labeled_at": None}

    feats = tx["_features"]
    contributions = sorted(
        [("geo_mismatch", 0.31 if feats["geo_mismatch"] else 0.02),
         ("ip_reputation_score", round(feats["ip_reputation_score"] * 0.4, 3)),
         ("velocity_score", round(feats["velocity_score"] * 0.3, 3)),
         ("amount_zscore", round(max(0, feats["amount_zscore"]) * 0.08, 3)),
         ("shared_card_fingerprint", 0.25 if feats["shared_card_fingerprint"] else 0.01),
         ("night_transaction", 0.09 if feats["night_transaction"] else 0.01)],
        key=lambda x: -x[1])[:3]
    ml_features.append({
        "_id":            f"feat_{uuid.uuid4().hex[:12]}",
        "transaction_id": tx["transaction_id"],
        "customer_id":    tx["customer_id"],
        "merchant_id":    tx["merchant_id"],
        "computed_at":    mdate(tx["_created_dt"]),
        "model_target":   "fraud_detection",
        "features":       feats,
        "prediction": {
            "fraud_probability": tx["_fraud_probability"],
            "risk_level":        tx["_risk_level"],
            "action_taken":      tx["_action"],
            "model_version":     MODEL_VERSION,
            "latency_ms":        random.randint(18, 120),
            "predicted_at":      mdate(tx["_created_dt"] + timedelta(milliseconds=random.randint(20, 180))),
            "top_shap":          [{"feature": f, "value": v} for f, v in contributions],
        },
        "label": label,
    })


# ─── 13. MongoDB — user_sessions ─────────────────────────────────────────────

FUNNEL = ["/", "/products", "/product/42", "/cart", "/checkout", "/confirmation"]
OS_BY_DEVICE = {"mobile": ["iOS 17", "Android 14"], "desktop": ["Windows 11", "macOS 14"], "tablet": ["iPadOS 17"]}
sessions = []
succeeded_pool = succeeded.copy()
random.shuffle(succeeded_pool)
for i in range(N_SESSIONS):
    converted = i < int(N_SESSIONS * 0.6) and succeeded_pool
    tx = succeeded_pool.pop() if converted else None
    cus_id = tx["customer_id"] if tx else random.choice(customer_ids)
    device = tx["device_type"] if tx else random.choice(DEVICE_TYPES)
    start = tx["_created_dt"] - timedelta(minutes=random.randint(2, 15)) if tx else rand_dt(DATE_START, DATE_END)
    events, t = [], start
    depth = len(FUNNEL) if converted else random.randint(1, 5)
    for page in FUNNEL[:depth]:
        t += timedelta(seconds=random.randint(5, 90))
        events.append({"event": "page_view", "page": page, "timestamp": mdate(t)})
        if page == "/checkout":
            n_clicks = 1 if converted else random.randint(1, 12)  # abandons répétés au checkout = signal bot
            for _ in range(n_clicks):
                t += timedelta(seconds=random.randint(3, 40))
                events.append({"event": "click", "element": "pay_button", "timestamp": mdate(t)})
    sessions.append({
        "_id":              f"sess_{uuid.uuid4().hex[:10]}",
        "customer_id":      cus_id,
        "session_start":    mdate(start),
        "session_end":      mdate(t),
        "duration_seconds": int((t - start).total_seconds()),
        "device": {"type": device, "os": random.choice(OS_BY_DEVICE[device]),
                   "browser": random.choice(["Chrome", "Safari", "Firefox", "Edge"]),
                   "screen_resolution": random.choice(["390x844", "1920x1080", "1366x768", "1024x1366"])},
        "geo": {"ip_address": truncate_ip(tx["ip_address"] if tx else fake.ipv4_public()),
                "country_code": tx["geo_country_code"] if tx else customer_by_id[cus_id]["country_code"],
                "city": fake.city()},
        "events":           events,
        "event_count":      len(events),
        "converted":        bool(converted),
        **({"transaction_id": tx["transaction_id"]} if tx else {}),
    })


# ─── 14. MongoDB — customer_feedback ─────────────────────────────────────────

FEEDBACK_TEXT = {
    "positive": ["Paiement rapide et sans friction.", "Checkout très simple, merci.", "Smooth payment, great experience."],
    "neutral":  ["Ça fonctionne, rien de spécial.", "Correct mais la confirmation met du temps.", "Fine, nothing special."],
    "negative": ["Paiement refusé deux fois sans explication.", "Trop lent, j'ai failli abandonner.", "Payment failed and support never answered."],
}
TAGS = {"positive": ["checkout", "speed", "ux"], "neutral": ["confirmation", "speed"], "negative": ["declined", "speed", "support", "checkout"]}
feedback = []
for tx in random.sample(succeeded, k=min(N_FEEDBACK, len(succeeded))):
    overall = random.choices([1, 2, 3, 4, 5], weights=[0.08, 0.10, 0.17, 0.35, 0.30])[0]
    label = "negative" if overall <= 2 else "neutral" if overall == 3 else "positive"
    feedback.append({
        "_id":            f"fbk_{uuid.uuid4().hex[:10]}",
        "customer_id":    tx["customer_id"],
        "merchant_id":    tx["merchant_id"],
        "merchant_name":  merchant_by_id[tx["merchant_id"]]["name"],
        "country_code":   customer_by_id[tx["customer_id"]]["country_code"],
        "transaction_id": tx["transaction_id"],
        "submitted_at":   mdate(tx["_created_dt"] + timedelta(days=random.randint(1, 5))),
        "channel":        random.choice(["email_survey", "in_app", "support_ticket"]),
        "scores": {"overall": overall, "ease_of_payment": min(5, max(1, overall + random.randint(-1, 1))),
                   "speed": min(5, max(1, overall + random.randint(-1, 1)))},
        "nps_score":      min(10, max(0, overall * 2 + random.randint(-2, 1))),
        "text":           random.choice(FEEDBACK_TEXT[label]),
        "language":       "fr",
        "sentiment": {"label": label, "score": round(random.uniform(0.55, 0.98), 2), "model_version": "sentiment-v1.2"},
        "tags":           random.sample(TAGS[label], k=random.randint(1, len(TAGS[label]))),
    })


# ─── 15. MongoDB — recommendations ───────────────────────────────────────────

month_start = NOW.replace(day=1, hour=0, minute=0, second=0)
recommendations = []
for m in merchants:
    recommendations.append({
        "_id":          f"rec_{m['merchant_id']}_{month_start.strftime('%Y-%m')}",
        "merchant_id":  m["merchant_id"],
        "generated_at": mdate(month_start + timedelta(hours=3)),
        "valid_from":   mdate(month_start),
        "valid_until":  mdate((month_start + timedelta(days=32)).replace(day=1)),
        "recommendations": [
            {"segment": seg, "suggested_method": random.choice(["card", "wallet", "bank_transfer"]),
             "confidence": round(random.uniform(0.55, 0.95), 2)}
            for seg in ["high_value", "mid_value", "low_value"]
        ],
        "model_version": "personalization-v1.3",
    })


# ─── 16. MongoDB — logs (time-series) ────────────────────────────────────────

LOG_MESSAGES = {
    "error":  ["Timeout on charge attempt", "Upstream 5xx from issuer", "Idempotency key conflict", "DB connection pool exhausted"],
    "access": ["POST /v1/charges", "GET /v1/customers/{id}", "POST /v1/refunds", "POST /v1/payment_intents"],
    "audit":  ["Role changed", "Secret rotated", "Export requested", "Erasure request processed"],
}
logs = []
for _ in range(N_LOGS):
    log_type = random.choices(["error", "access", "audit"], weights=[0.3, 0.6, 0.1])[0]
    severity = (random.choices(["error", "critical"], weights=[0.8, 0.2])[0] if log_type == "error"
                else random.choices(["debug", "info", "warning"], weights=[0.3, 0.6, 0.1])[0])
    tx = random.choice(transactions)
    ts = rand_dt(DATE_START, DATE_END)
    latency = random.randint(800, 6000) if log_type == "error" else int(np.random.lognormal(math.log(120), 0.5))
    logs.append({
        "_id":       f"log_{uuid.uuid4().hex[:10]}",
        "timestamp": mdate(ts),
        "meta":      {"service": random.choice(LOG_SERVICES), "type": log_type, "severity": severity},
        "message":   random.choice(LOG_MESSAGES[log_type]),
        "metadata": {
            "transaction_id": tx["transaction_id"],
            "merchant_id":    tx["merchant_id"],
            "http_status":    random.choice([500, 502, 504, 429]) if log_type == "error" else random.choice([200, 200, 200, 201, 400, 401, 404]),
            "latency_ms":     latency,
        },
        "trace": {"request_id": f"req_{uuid.uuid4().hex[:10]}", "ip_address": truncate_ip(fake.ipv4_public()),
                  "user_agent": fake.user_agent()},
    })


# ─── Export ──────────────────────────────────────────────────────────────────

def to_csv(df: pd.DataFrame, name: str):
    df.to_csv(os.path.join(OUTPUT_DIR, f"{name}.csv"), index=False)

def to_json(docs: list, name: str):
    with open(os.path.join(MONGO_DIR, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)

to_csv(df_countries,       "countries")
to_csv(df_currencies,      "currencies")
to_csv(df_exchange_rates,  "exchange_rates")
to_csv(df_merchants,       "merchants")
to_csv(df_customers,       "customers")
to_csv(df_payment_methods, "payment_methods")
to_csv(df_products,        "products")
to_csv(df_subscriptions,   "subscriptions")
to_csv(df_transactions,    "transactions")
to_csv(df_refunds,         "refunds")
to_csv(df_disputes,        "disputes")
to_csv(df_fraud,           "fraud_indicators")
to_csv(df_audit,           "audit_logs")

to_json(logs,            "logs")
to_json(sessions,        "user_sessions")
to_json(ml_features,     "ml_features")
to_json(feedback,        "customer_feedback")
to_json(recommendations, "recommendations")

# Vérité terrain complète (jamais chargée en base) — sert au script ml/train_fraud_demo.py
pd.DataFrame([{"transaction_id": t["transaction_id"], "is_fraud_true": t["_true_fraud"], **t["_features"]}
              for t in transactions]).to_csv(os.path.join(OUTPUT_DIR, "_ground_truth_fraud.csv"), index=False)

# ─── Résumé ──────────────────────────────────────────────────────────────────

n_true_fraud = sum(t["_true_fraud"] for t in transactions)
print(f"Données générées dans {OUTPUT_DIR}/ (période {DATE_START.date()} → {DATE_END.date()})")
for name, df in [("countries", df_countries), ("currencies", df_currencies), ("exchange_rates", df_exchange_rates),
                 ("merchants", df_merchants), ("customers", df_customers), ("payment_methods", df_payment_methods),
                 ("products", df_products), ("subscriptions", df_subscriptions), ("transactions", df_transactions),
                 ("refunds", df_refunds), ("disputes", df_disputes), ("fraud_indicators", df_fraud), ("audit_logs", df_audit)]:
    print(f"  {name:<18}: {len(df):>6} lignes")
for name, docs in [("logs", logs), ("user_sessions", sessions), ("ml_features", ml_features),
                   ("customer_feedback", feedback), ("recommendations", recommendations)]:
    print(f"  mongo/{name:<12}: {len(docs):>6} documents")
print(f"\n  Fraude réelle (latente) : {n_true_fraud} / {len(transactions)} = {100 * n_true_fraud / len(transactions):.1f} %")
print(f"  Transactions succeeded  : {len(succeeded)} · disputes connues : {len(disputes)}")
