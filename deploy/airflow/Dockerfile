# Image Airflow du pipeline : Airflow 2.10 + psql + mongosh/mongoimport + libs Python du projet
FROM apache/airflow:2.10.5-python3.12

USER root
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates postgresql-client \
 && curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
      > /etc/apt/sources.list.d/mongodb-org-7.0.list \
 && apt-get update -y \
 && apt-get install -y --no-install-recommends mongodb-mongosh mongodb-database-tools \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
