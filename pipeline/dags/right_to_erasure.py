"""
DAG à la demande — droit à l'oubli (RGPD art. 17 / CCPA)
Déclenché par l'API de gestion des demandes avec conf={"customer_id": "cus_..."}.
Objectif interne : < 72 h (obligation légale : 30 jours).

Ordre : OLTP (source de vérité) → MongoDB → Redshift → preuve d'exécution (audit_logs).
Les faits financiers sont conservés (obligation comptable, 10 ans) mais détachés
de toute donnée identifiante.
"""

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.mongo.hooks.mongo import MongoHook
from airflow.providers.postgres.hooks.postgres import PostgresHook


def erase_oltp(**ctx):
    cid = ctx["dag_run"].conf["customer_id"]
    PostgresHook("oltp_default").run(
        """
        BEGIN;
        UPDATE customers
           SET email = 'erased+' || md5(customer_id) || '@invalid',
               deleted_at = now(), updated_at = now()
         WHERE customer_id = %(cid)s;
        -- Le montant, la date et le merchant restent (comptabilité) ; l'IP et la ville partent
        UPDATE transactions SET ip_address = NULL, geo_city = NULL, updated_at = now()
         WHERE customer_id = %(cid)s;
        DELETE FROM payment_methods
         WHERE customer_id = %(cid)s
           AND payment_method_id NOT IN (SELECT payment_method_id FROM transactions WHERE customer_id = %(cid)s);
        INSERT INTO audit_logs (audit_id, event_type, table_name, record_id, user_id, user_role)
        VALUES ('aud_' || md5(random()::text), 'ERASURE', 'customers', %(cid)s, 'dag_right_to_erasure', 'service');
        COMMIT;
        """,
        parameters={"cid": cid},
    )


def erase_mongo(**ctx):
    cid = ctx["dag_run"].conf["customer_id"]
    db = MongoHook("mongo_default").get_conn()["stripe_nosql"]
    db.user_sessions.delete_many({"customer_id": cid})
    db.customer_feedback.update_many({"customer_id": cid}, {"$unset": {"customer_id": ""}})
    # Features conservées (réentraînement), identité retirée
    db.ml_features.update_many({"customer_id": cid}, {"$unset": {"customer_id": ""}})


def erase_olap(**ctx):
    cid = ctx["dag_run"].conf["customer_id"]
    # La dimension perd son identité ; les faits restent rattachés à une SK anonyme
    PostgresHook("redshift_default").run(
        "UPDATE dim_customer SET customer_id = 'erased', email_hash = NULL WHERE customer_id = %s",
        parameters=(cid,),
    )


with DAG(
    dag_id="right_to_erasure",
    schedule=None,  # déclenché par API
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gdpr", "compliance"],
) as dag:
    (
        PythonOperator(task_id="erase_oltp", python_callable=erase_oltp)
        >> PythonOperator(task_id="erase_mongo", python_callable=erase_mongo)
        >> PythonOperator(task_id="erase_olap", python_callable=erase_olap)
    )
