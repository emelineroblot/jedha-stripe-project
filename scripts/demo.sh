#!/usr/bin/env bash
# ============================================================
# Démo de bout en bout — Stripe business case
#   1. démarre PostgreSQL (OLTP + OLAP simulé) et MongoDB
#   2. crée les schémas et charge les données synthétiques
#   3. exécute toutes les requêtes et archive les résultats dans docs/results/
#
# Usage : bash scripts/demo.sh [--no-up] [--no-load]
# Prérequis : docker compose ; données générées (python generate_data.py && python build_olap.py)
# ============================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash (Windows) : ne pas convertir /sql/... en chemin Windows

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_NATIVE="$(cygpath -w "$ROOT" 2>/dev/null || echo "$ROOT")"   # chemin Windows natif sous Git Bash
COMPOSE="docker compose -f $ROOT_NATIVE/docker/dev/docker-compose.yml"
PG="docker exec -i -w /data stripe-postgres psql -U stripe -v ON_ERROR_STOP=1 -q"
MONGO="docker exec -i stripe-mongo mongosh --quiet"
RESULTS="$ROOT/docs/results"
mkdir -p "$RESULTS"

UP=1; LOAD=1
for arg in "$@"; do
  case "$arg" in
    --no-up)   UP=0 ;;
    --no-load) LOAD=0 ;;
  esac
done

if [[ $UP -eq 1 ]]; then
  echo "▶ Démarrage de la stack"
  $COMPOSE up -d
  echo "  attente des healthchecks…"
  for i in $(seq 1 40); do
    pg_ok=$(docker inspect -f '{{.State.Health.Status}}' stripe-postgres 2>/dev/null || echo starting)
    mg_ok=$(docker inspect -f '{{.State.Health.Status}}' stripe-mongo 2>/dev/null || echo starting)
    [[ "$pg_ok" == "healthy" && "$mg_ok" == "healthy" ]] && break
    sleep 2
  done
  echo "  postgres=$pg_ok mongo=$mg_ok"
fi

if [[ $LOAD -eq 1 ]]; then
  echo "▶ OLTP : schéma + chargement"
  $PG -d stripe_oltp -f /sql/ddl_oltp.sql
  $PG -d stripe_oltp -f /sql/load_oltp.sql

  echo "▶ OLAP : schéma + chargement"
  $PG -d stripe_olap -f /sql/ddl_olap.sql
  $PG -d stripe_olap -f /sql/load_olap.sql

  echo "▶ MongoDB : replica set, collections, index"
  $MONGO --file /scripts/mongo_init.js
  for coll in logs user_sessions ml_features customer_feedback recommendations; do
    docker exec stripe-mongo mongoimport --quiet --db stripe_nosql --collection "$coll" \
      --jsonArray --file "/data/import/$coll.json"
    n=$(docker exec stripe-mongo mongosh --quiet --eval "db.getSiblingDB('stripe_nosql').$coll.countDocuments()")
    echo "  $coll : $n documents"
  done
fi

echo "▶ Exécution des requêtes"
$PG -d stripe_oltp -e -f /queries/oltp_queries.sql > "$RESULTS/oltp_results.txt"
$PG -d stripe_olap -e -f /queries/olap_queries.sql > "$RESULTS/olap_results.txt"
$MONGO --file /queries/nosql_queries.js > "$RESULTS/nosql_results.txt"
echo "  résultats : docs/results/{oltp,olap,nosql}_results.txt"

echo "✔ Démo prête"
echo "  psql   : docker exec -it stripe-postgres psql -U stripe -d stripe_oltp"
echo "  mongosh: docker exec -it stripe-mongo mongosh stripe_nosql"
