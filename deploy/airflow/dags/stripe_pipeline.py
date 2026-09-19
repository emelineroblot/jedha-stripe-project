"""
DAG de production — stripe_pipeline
Chaîne complète, quotidienne (02:00 UTC) ou déclenchée à la main :

  generate_data ─► build_olap ─► init_databases ─► load_oltp ─► load_olap ─► quality_checks ─► queries_oltp / queries_olap
                └► init_mongo ─► load_mongo ─────────────────────────────────┘             └► queries_nosql
                └► train_fraud_model                                                        └► upload_to_s3 ─► summary

Cibles : RDS PostgreSQL (stripe_oltp, stripe_olap), MongoDB (replica set sur l'hôte), S3 (staging + résultats).
Secrets : variables d'environnement injectées par cloud-init (.env), jamais dans ce fichier.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

PROJECT = os.environ.get("PROJECT_DIR", "/opt/airflow/project")
RESULTS = os.environ.get("RESULTS_DIR", "/opt/airflow/results")
PSQL = "psql -v ON_ERROR_STOP=1 -q"                       # PGHOST / PGUSER / PGPASSWORD viennent de l'env
MONGO_DIRECT = "mongodb://mongo:27017/?directConnection=true"
MONGO_RS = os.environ.get("MONGO_URI", "mongodb://mongo:27017/?replicaSet=rs0")

default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


def quality_checks(**ctx):
    """Tests de qualité et de cohérence inter-systèmes (équivalent des tests dbt, bloquants)."""
    import psycopg2
    from pymongo import MongoClient

    def one(db, sql):
        with psycopg2.connect(dbname=db) as conn, conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]

    checks = {}
    n_tx = one("stripe_oltp", "SELECT count(*) FROM transactions")
    checks["oltp_transactions_not_empty"] = n_tx > 0
    checks["oltp_no_orphan_fraud_indicator"] = one(
        "stripe_oltp",
        "SELECT count(*) FROM fraud_indicators f LEFT JOIN transactions t USING (transaction_id) WHERE t.transaction_id IS NULL",
    ) == 0
    checks["oltp_failed_have_reason"] = one(
        "stripe_oltp", "SELECT count(*) FROM transactions WHERE status = 'failed' AND failure_reason IS NULL"
    ) == 0
    fraud_rate = one(
        "stripe_oltp",
        "SELECT count(*) FILTER (WHERE risk_level IN ('high','critical'))::float / NULLIF((SELECT count(*) FROM transactions),0) FROM fraud_indicators",
    ) or 0
    checks["oltp_fraud_rate_in_bounds"] = 0.005 <= fraud_rate <= 0.20
    n_fact = one("stripe_olap", "SELECT count(*) FROM fact_transactions")
    checks["olap_fact_matches_oltp"] = n_fact == n_tx                 # cohérence OLTP ↔ OLAP
    checks["olap_fact_unique_transaction_id"] = one(
        "stripe_olap", "SELECT count(*) - count(DISTINCT transaction_id) FROM fact_transactions"
    ) == 0
    checks["olap_no_pii_email"] = one(
        "stripe_olap", "SELECT count(*) FROM dim_customer WHERE email_hash LIKE '%@%'"
    ) == 0                                                            # contrôle PII automatisé
    checks["olap_scd2_single_current_version"] = one(
        "stripe_olap", "SELECT count(*) FROM (SELECT merchant_id FROM dim_merchant WHERE is_current GROUP BY 1 HAVING count(*) > 1) x"
    ) == 0

    mongo = MongoClient(MONGO_RS, serverSelectionTimeoutMS=10000)["stripe_nosql"]
    n_feat = mongo.ml_features.count_documents({})
    checks["nosql_ml_features_matches_oltp"] = n_feat == n_tx         # cohérence OLTP ↔ NoSQL
    checks["nosql_logs_are_dates"] = mongo.logs.count_documents({"timestamp": {"$type": "date"}}) == mongo.logs.count_documents({})

    failed = [k for k, ok in checks.items() if not ok]
    summary = {"transactions": n_tx, "fact_rows": n_fact, "ml_features": n_feat,
               "fraud_rate_high_critical": round(fraud_rate, 4), "checks": checks}
    print(json.dumps(summary, indent=2))
    ctx["ti"].xcom_push(key="quality", value=summary)
    if failed:
        raise AssertionError(f"Contrôles en échec : {failed}")


def upload_to_s3(**ctx):
    """Staging des données générées et archivage des résultats dans S3 (rôle d'instance, aucune clé)."""
    import boto3

    bucket = os.environ["S3_BUCKET"]
    ds = ctx["ds"]
    s3 = boto3.client("s3")
    uploaded = 0
    for root_dir, prefix in [(f"{PROJECT}/data", f"runs/{ds}/data"), (f"{RESULTS}/{ds}", f"runs/{ds}/results"),
                             (f"{PROJECT}/ml/output", f"runs/{ds}/ml")]:
        for dirpath, _, files in os.walk(root_dir):
            for f in files:
                if f.endswith((".csv", ".json", ".txt", ".png")):
                    local = os.path.join(dirpath, f)
                    key = f"{prefix}/{os.path.relpath(local, root_dir)}".replace(os.sep, "/")
                    s3.upload_file(local, bucket, key)
                    uploaded += 1
    print(f"{uploaded} fichiers → s3://{bucket}/runs/{ds}/")
    ctx["ti"].xcom_push(key="s3_prefix", value=f"s3://{bucket}/runs/{ds}/")


def summary(**ctx):
    q = ctx["ti"].xcom_pull(task_ids="quality_checks", key="quality") or {}
    s3 = ctx["ti"].xcom_pull(task_ids="upload_to_s3", key="s3_prefix")
    print("=" * 70)
    print(f"Run {ctx['ds']} — pipeline Stripe")
    print(f"  transactions OLTP : {q.get('transactions')}")
    print(f"  lignes fact OLAP  : {q.get('fact_rows')}")
    print(f"  ml_features Mongo : {q.get('ml_features')}")
    print(f"  taux high/critical: {q.get('fraud_rate_high_critical')}")
    print(f"  contrôles         : {sum(q.get('checks', {}).values())}/{len(q.get('checks', {}))} OK")
    print(f"  archivage         : {s3}")
    print("=" * 70)


with DAG(
    dag_id="stripe_pipeline",
    description="Génération → OLTP (RDS) → OLAP (RDS) → NoSQL (Mongo) → contrôles → requêtes → S3",
    schedule="0 2 * * *",
    start_date=datetime(2026, 9, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["stripe", "production"],
) as dag:

    generate_data = BashOperator(
        task_id="generate_data",
        bash_command=f"cd {PROJECT} && python generate_data.py --out data --seed {{{{ ds_nodash }}}}",
    )

    build_olap = BashOperator(
        task_id="build_olap",
        bash_command=f"cd {PROJECT} && python build_olap.py --data data",
    )

    init_databases = BashOperator(
        task_id="init_databases",
        bash_command=(
            "for db in stripe_oltp stripe_olap; do "
            f"if [ \"$({PSQL} -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname = '$db'\")\" = \"1\" ]; "
            f"then echo \"$db existe\"; else {PSQL} -d postgres -c \"CREATE DATABASE $db\" && echo \"$db créée\"; fi; done"
        ),
    )

    load_oltp = BashOperator(
        task_id="load_oltp",
        bash_command=(
            f"cd {PROJECT}/data && {PSQL} -d stripe_oltp -f ../sql/ddl_oltp.sql && {PSQL} -d stripe_oltp -f ../sql/load_oltp.sql"
        ),
    )

    load_olap = BashOperator(
        task_id="load_olap",
        bash_command=(
            f"cd {PROJECT}/data && {PSQL} -d stripe_olap -f ../sql/ddl_olap.sql && {PSQL} -d stripe_olap -f ../sql/load_olap.sql"
        ),
    )

    init_mongo = BashOperator(
        task_id="init_mongo",
        bash_command=f"cd {PROJECT} && mongosh --quiet \"{MONGO_DIRECT}\" --file scripts/mongo_init.js",
    )

    load_mongo = BashOperator(
        task_id="load_mongo",
        bash_command=(
            f"cd {PROJECT}/data/mongo && for c in logs user_sessions ml_features customer_feedback recommendations; do "
            f"mongoimport --quiet --uri \"{MONGO_RS}\" --db stripe_nosql --collection $c --jsonArray --file $c.json; "
            f"echo \"$c : $(mongosh --quiet \"{MONGO_RS}\" --eval \"db.getSiblingDB('stripe_nosql').$c.countDocuments()\") documents\"; done"
        ),
    )

    quality = PythonOperator(task_id="quality_checks", python_callable=quality_checks)

    queries_oltp = BashOperator(
        task_id="queries_oltp",
        bash_command=f"mkdir -p {RESULTS}/{{{{ ds }}}} && cd {PROJECT} && {PSQL} -e -d stripe_oltp -f queries/oltp_queries.sql > {RESULTS}/{{{{ ds }}}}/oltp_results.txt && tail -n 30 {RESULTS}/{{{{ ds }}}}/oltp_results.txt",
    )

    queries_olap = BashOperator(
        task_id="queries_olap",
        bash_command=f"mkdir -p {RESULTS}/{{{{ ds }}}} && cd {PROJECT} && {PSQL} -e -d stripe_olap -f queries/olap_queries.sql > {RESULTS}/{{{{ ds }}}}/olap_results.txt && grep -c 'rows)' {RESULTS}/{{{{ ds }}}}/olap_results.txt",
    )

    queries_nosql = BashOperator(
        task_id="queries_nosql",
        bash_command=f"mkdir -p {RESULTS}/{{{{ ds }}}} && cd {PROJECT} && mongosh --quiet \"{MONGO_RS}\" --file queries/nosql_queries.js > {RESULTS}/{{{{ ds }}}}/nosql_results.txt && grep -c 'Q[0-9]* —' {RESULTS}/{{{{ ds }}}}/nosql_results.txt",
    )

    train_model = BashOperator(
        task_id="train_fraud_model",
        bash_command=f"cd {PROJECT} && python ml/train_fraud_demo.py --out ml/output 2>&1 | grep -v Warning | tail -n 25",
    )

    upload = PythonOperator(task_id="upload_to_s3", python_callable=upload_to_s3)
    done = PythonOperator(task_id="summary", python_callable=summary)

    generate_data >> build_olap >> init_databases >> load_oltp >> load_olap
    generate_data >> init_mongo >> load_mongo
    generate_data >> train_model
    [load_olap, load_mongo] >> quality >> [queries_oltp, queries_olap, queries_nosql]
    [queries_oltp, queries_olap, queries_nosql, train_model] >> upload >> done
