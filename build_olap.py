"""
Construit le star schema OLAP (data/olap/*.csv) à partir des CSV OLTP (data/*.csv).

Simule la chaîne dbt du pipeline :
  staging  → cast / nettoyage
  intermediate → enrichissement (amount_usd au taux du jour, fees, refunds, disputes, fraude)
  marts    → dimensions (surrogate keys, SCD Type 2), fact_transactions, agg_*

Usage : python build_olap.py [--data data]
"""

import argparse
import hashlib
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--data", default="data")
args = parser.parse_args()

IN_DIR = args.data
OUT_DIR = os.path.join(IN_DIR, "olap")
os.makedirs(OUT_DIR, exist_ok=True)
NOW = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
EMAIL_SALT = "demo-salt-rotate-in-prod"
STRIPE_FEE_PCT, STRIPE_FEE_FIXED_USD = 0.029, 0.30

def read(name):
    return pd.read_csv(os.path.join(IN_DIR, f"{name}.csv"))

def write(df, name):
    df.to_csv(os.path.join(OUT_DIR, f"{name}.csv"), index=False, lineterminator="\n")
    print(f"  {name:<22}: {len(df):>6} lignes")

def to_dt(series):
    return pd.to_datetime(series, utc=True).dt.tz_localize(None)

# ─── Staging ─────────────────────────────────────────────────────────────────

countries        = read("countries")
currencies       = read("currencies")
exchange_rates   = read("exchange_rates")
merchants        = read("merchants")
customers        = read("customers")
payment_methods  = read("payment_methods")
products         = read("products")
transactions     = read("transactions")
refunds          = read("refunds")
disputes         = read("disputes")
fraud            = read("fraud_indicators")
audit_logs       = read("audit_logs")

transactions["created_at"] = to_dt(transactions["created_at"])
transactions["tx_date"] = transactions["created_at"].dt.date
merchants["created_at"] = to_dt(merchants["created_at"])
customers["created_at"] = to_dt(customers["created_at"])
audit_logs["created_at"] = to_dt(audit_logs["created_at"])

print("Construction du star schema OLAP")

# ─── dim_date ────────────────────────────────────────────────────────────────

d0 = transactions["tx_date"].min() - timedelta(days=31)
d1 = max(transactions["tx_date"].max(), NOW.date()) + timedelta(days=31)
days = pd.date_range(d0, d1, freq="D")
dim_date = pd.DataFrame({
    "date_sk":        days.strftime("%Y%m%d").astype(int),
    "full_date":      days.date,
    "year":           days.year,
    "quarter":        days.quarter,
    "month":          days.month,
    "month_name":     days.strftime("%B"),
    "week_of_year":   days.isocalendar().week.values,
    "day":            days.day,
    "day_of_week":    days.dayofweek + 1,
    "day_name":       days.strftime("%A"),
    "is_weekend":     days.dayofweek >= 5,
    "is_holiday":     [(d.month == 1 and d.day == 1) or (d.month == 12 and d.day == 25) for d in days],
    "fiscal_year":    days.year,
    "fiscal_quarter": days.quarter,
})
write(dim_date, "dim_date")

# ─── dim_geography / dim_currency / dim_payment_method / dim_product ─────────

dim_geography = countries.rename(columns={"name": "country_name"}).copy()
dim_geography.insert(0, "geography_sk", range(1, len(dim_geography) + 1))
dim_geography = dim_geography[["geography_sk", "country_code", "country_name", "region", "data_region", "gdpr_applies"]]
write(dim_geography, "dim_geography")
geo_sk = dict(zip(dim_geography["country_code"], dim_geography["geography_sk"]))

dim_currency = currencies.copy()
dim_currency.insert(0, "currency_sk", range(1, len(dim_currency) + 1))
dim_currency = dim_currency[["currency_sk", "currency_code", "name", "decimal_places"]]
write(dim_currency, "dim_currency")
cur_sk = dict(zip(dim_currency["currency_code"], dim_currency["currency_sk"]))

pm_combos = payment_methods[["type", "brand"]].drop_duplicates().fillna({"brand": ""}).sort_values(["type", "brand"]).reset_index(drop=True)
dim_payment_method = pd.DataFrame({
    "payment_method_sk": range(1, len(pm_combos) + 1),
    "type":  pm_combos["type"],
    "brand": pm_combos["brand"].replace("", np.nan),
    "is_digital": pm_combos["type"].isin(["wallet"]),
})
write(dim_payment_method, "dim_payment_method")
pm_sk = {(r.type, "" if pd.isna(r.brand) else r.brand): r.payment_method_sk for r in dim_payment_method.itertuples()}
pm_type_brand = payment_methods.set_index("payment_method_id")[["type", "brand"]].fillna({"brand": ""}).to_dict("index")

dim_product = products.copy()
dim_product.insert(0, "product_sk", range(1, len(dim_product) + 1))
dim_product = dim_product[["product_sk", "product_id", "merchant_id", "name", "billing_interval", "unit_price", "currency_code"]]
write(dim_product, "dim_product")
prod_sk = dict(zip(dim_product["product_id"], dim_product["product_sk"]))

# ─── dim_merchant (SCD Type 2) ───────────────────────────────────────────────
# Simulation d'un snapshot dbt : 3 merchants ont changé de catégorie il y a ~6 mois.
# Version 1 : ancienne catégorie, scd_end = date du changement, is_current = false
# Version 2 : catégorie actuelle, is_current = true

change_date = (NOW - timedelta(days=180)).date()
changed = merchants.sample(n=3, random_state=7)
old_category = {"saas": "e-commerce", "e-commerce": "retail", "retail": "marketplace"}

rows, sk = [], 1
for m in merchants.itertuples():
    region = countries.loc[countries.country_code == m.country_code, "region"].iloc[0]
    if m.merchant_id in set(changed["merchant_id"]):
        rows.append(dict(merchant_sk=sk, merchant_id=m.merchant_id, name=m.name,
                         category=old_category.get(m.category, "e-commerce"), mcc=m.mcc,
                         country_code=m.country_code, region=region, status=m.status,
                         scd_start=m.created_at.date(), scd_end=change_date, is_current=False))
        sk += 1
        rows.append(dict(merchant_sk=sk, merchant_id=m.merchant_id, name=m.name, category=m.category, mcc=m.mcc,
                         country_code=m.country_code, region=region, status=m.status,
                         scd_start=change_date, scd_end=None, is_current=True))
    else:
        rows.append(dict(merchant_sk=sk, merchant_id=m.merchant_id, name=m.name, category=m.category, mcc=m.mcc,
                         country_code=m.country_code, region=region, status=m.status,
                         scd_start=m.created_at.date(), scd_end=None, is_current=True))
    sk += 1
dim_merchant = pd.DataFrame(rows)
write(dim_merchant, "dim_merchant")

def merchant_sk_at(merchant_id, tx_date):
    """Résolution de la SK valide à la date de la transaction (logique SCD2)."""
    versions = dim_merchant[dim_merchant.merchant_id == merchant_id]
    for v in versions.itertuples():
        if v.scd_start <= tx_date and (v.scd_end is None or pd.isna(v.scd_end) or tx_date < v.scd_end):
            return v.merchant_sk
    return versions.iloc[-1].merchant_sk

# ─── dim_customer (SCD Type 2, une version, pseudonymisée) ──────────────────

# Segment = déciles de dépense USD sur 12 mois (calculé plus bas après la fact) — placeholder
dim_customer = pd.DataFrame({
    "customer_sk":       range(1, len(customers) + 1),
    "customer_id":       customers["customer_id"],
    "email_hash":        [hashlib.sha256((EMAIL_SALT + e).encode()).hexdigest() for e in customers["email"]],
    "country_code":      customers["country_code"],
    "region":            customers["country_code"].map(dict(zip(countries.country_code, countries.region))),
    "data_region":       customers["country_code"].map(dict(zip(countries.country_code, countries.data_region))),
    "segment":           None,
    "acquisition_month": customers["created_at"].dt.to_period("M").dt.to_timestamp().dt.date,
    "scd_start":         customers["created_at"].dt.date,
    "scd_end":           None,
    "is_current":        True,
})
cus_sk = dict(zip(dim_customer["customer_id"], dim_customer["customer_sk"]))

# ─── Intermediate : enrichissement ───────────────────────────────────────────

rate_map = {(r.rate_date, r.currency_code): r.rate_to_usd for r in exchange_rates.itertuples()}
refund_usd = {}
refund_sum = refunds[refunds.status == "succeeded"].groupby("transaction_id")["amount"].sum()
disputed_ids = set(disputes["transaction_id"])
fraud_map = fraud.set_index("transaction_id")[["anomaly_score", "risk_level"]].to_dict("index")

fact_rows = []
for i, t in enumerate(transactions.itertuples(), start=1):
    rate = rate_map.get((str(t.tx_date), t.currency_code), 1.0)
    amount_usd = round(t.amount * rate, 2)
    refund_amt_usd = round(refund_sum.get(t.transaction_id, 0.0) * rate, 2)
    fee_usd = round(amount_usd * STRIPE_FEE_PCT + STRIPE_FEE_FIXED_USD, 2) if t.status == "succeeded" else 0.0
    f = fraud_map.get(t.transaction_id, {})
    pmk = pm_type_brand[t.payment_method_id]
    fact_rows.append({
        "transaction_sk":    i,
        "transaction_id":    t.transaction_id,
        "date_sk":           int(t.created_at.strftime("%Y%m%d")),
        "hour_of_day":       t.created_at.hour,
        "merchant_sk":       merchant_sk_at(t.merchant_id, t.tx_date),
        "customer_sk":       cus_sk[t.customer_id],
        "geography_sk":      geo_sk[t.geo_country_code],
        "payment_method_sk": pm_sk[(pmk["type"], pmk["brand"])],
        "product_sk":        prod_sk.get(t.product_id) if isinstance(t.product_id, str) else None,
        "currency_sk":       cur_sk[t.currency_code],
        "status":            t.status,
        "amount":            t.amount,
        "amount_usd":        amount_usd,
        "fee_usd":           fee_usd,
        "net_amount_usd":    round(amount_usd - fee_usd - refund_amt_usd, 2) if t.status == "succeeded" else 0.0,
        "refund_amount_usd": refund_amt_usd,
        "is_refunded":       refund_amt_usd > 0,
        "is_disputed":       t.transaction_id in disputed_ids,
        "is_fraud_flagged":  f.get("risk_level") in ("high", "critical"),
        "is_subscription":   isinstance(t.subscription_id, str),
        "anomaly_score":     f.get("anomaly_score"),
        "risk_level":        f.get("risk_level"),
        "device_type":       t.device_type,
        "loaded_at":         NOW,
    })
fact = pd.DataFrame(fact_rows)
fact["product_sk"] = fact["product_sk"].astype("Int64")
write(fact, "fact_transactions")

# Segment client = déciles de dépense (succeeded, 12 mois)
spend = fact[fact.status == "succeeded"].groupby("customer_sk")["amount_usd"].sum()
deciles = pd.qcut(spend.rank(method="first"), 10, labels=False) + 1
segment = deciles.map(lambda d: "high_value" if d >= 9 else "mid_value" if d >= 5 else "low_value")
dim_customer["segment"] = dim_customer["customer_sk"].map(segment).fillna("low_value")
write(dim_customer, "dim_customer")

# ─── fact_audit_events ───────────────────────────────────────────────────────

fact_audit = pd.DataFrame({
    "audit_sk":     range(1, len(audit_logs) + 1),
    "audit_id":     audit_logs["audit_id"],
    "date_sk":      audit_logs["created_at"].dt.strftime("%Y%m%d").astype(int),
    "event_type":   audit_logs["event_type"],
    "table_name":   audit_logs["table_name"],
    "user_role":    audit_logs["user_role"],
    "user_id_hash": [hashlib.sha256(u.encode()).hexdigest()[:16] for u in audit_logs["user_id"]],
    "is_sensitive": audit_logs["table_name"].isin(["payment_methods", "customers"]) | audit_logs["event_type"].isin(["EXPORT", "ERASURE"]),
})
write(fact_audit, "fact_audit_events")

# ─── exchange_rates (copie de référence dans l'OLAP) ─────────────────────────

write(exchange_rates[["rate_date", "currency_code", "rate_to_usd"]], "exchange_rates")

# ─── agg_daily_revenue ───────────────────────────────────────────────────────

fact_cur = fact.merge(dim_currency[["currency_sk", "currency_code"]], on="currency_sk")
agg_daily = fact_cur.groupby(["date_sk", "merchant_sk", "currency_code"]).agg(
    transaction_count=("transaction_sk", "count"),
    succeeded_count=("status", lambda s: (s == "succeeded").sum()),
    failed_count=("status", lambda s: (s == "failed").sum()),
    total_amount=("amount", lambda s: s[fact_cur.loc[s.index, "status"] == "succeeded"].sum()),
    total_amount_usd=("amount_usd", lambda s: s[fact_cur.loc[s.index, "status"] == "succeeded"].sum()),
    net_amount_usd=("net_amount_usd", "sum"),
    refund_count=("is_refunded", "sum"),
    dispute_count=("is_disputed", "sum"),
    fraud_count=("is_fraud_flagged", "sum"),
).reset_index()
for c in ["total_amount", "total_amount_usd", "net_amount_usd"]:
    agg_daily[c] = agg_daily[c].round(2)
write(agg_daily, "agg_daily_revenue")

# ─── agg_monthly_fraud ───────────────────────────────────────────────────────

fact_m = fact.copy()
fact_m["year_month"] = fact_m["date_sk"].astype(str).str[:4] + "-" + fact_m["date_sk"].astype(str).str[4:6]
agg_fraud = fact_m.groupby(["year_month", "geography_sk"]).agg(
    total_transactions=("transaction_sk", "count"),
    total_flagged=("is_fraud_flagged", "sum"),
    total_critical=("risk_level", lambda s: (s == "critical").sum()),
    total_disputed=("is_disputed", "sum"),
    avg_anomaly_score=("anomaly_score", "mean"),
).reset_index()
agg_fraud["avg_anomaly_score"] = agg_fraud["avg_anomaly_score"].round(4)
agg_fraud["fraud_rate_pct"] = (100 * agg_fraud["total_flagged"] / agg_fraud["total_transactions"]).round(3)
write(agg_fraud, "agg_monthly_fraud")

print(f"\nStar schema écrit dans {OUT_DIR}/")
