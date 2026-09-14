"""
DAG quotidien — 02:00 UTC
Rafraîchit les taux de change, construit les agrégats OLAP via dbt,
exécute les tests de qualité (bloquants) et alerte Slack en cas d'échec.

Pré-requis Airflow : connexions `redshift_default`, `ecb_api`, `slack_alerts`,
variable `DBT_PROJECT_DIR` (= pipeline/dbt).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.slack.notifications.slack import send_slack_notification

DBT = "cd {{ var.value.DBT_PROJECT_DIR }} && dbt"

default_args = {
    "owner": "data-platform",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": [
        send_slack_notification(
            slack_conn_id="slack_alerts",
            channel="#data-alerts",
            text=":red_circle: {{ dag.dag_id }}.{{ ti.task_id }} a échoué — {{ ts }}",
        )
    ],
}

with DAG(
    dag_id="daily_aggregates",
    schedule="0 2 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["olap", "dbt"],
) as dag:

    fetch_exchange_rates = SimpleHttpOperator(
        task_id="fetch_exchange_rates",
        http_conn_id="ecb_api",
        endpoint="/v1/latest?base=USD",
        method="GET",
        log_response=True,
    )

    load_exchange_rates = BashOperator(
        task_id="load_exchange_rates",
        bash_command=f"{DBT} run-operation load_exchange_rates "
                     "--args '{{ ti.xcom_pull(task_ids=\"fetch_exchange_rates\") }}'",
    )

    # SCD Type 2 : dim_merchant, dim_customer
    dbt_snapshot = BashOperator(
        task_id="dbt_snapshot",
        bash_command=f"{DBT} snapshot",
    )

    # fact_transactions incrémental + agg_daily_revenue + agg_monthly_fraud
    dbt_run_marts = BashOperator(
        task_id="dbt_run_marts",
        bash_command=f"{DBT} run --select marts+",
        sla=timedelta(hours=1),
    )

    # Bloquant : un test rouge = les dashboards ne sont pas rafraîchis
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"{DBT} test --select marts+ --store-failures",
    )

    refresh_mv = BashOperator(
        task_id="refresh_materialized_views",
        bash_command="psql $REDSHIFT_URL -c 'REFRESH MATERIALIZED VIEW mv_monthly_revenue_by_country;'",
    )

    fetch_exchange_rates >> load_exchange_rates >> dbt_snapshot >> dbt_run_marts >> dbt_test >> refresh_mv
