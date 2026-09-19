#!/usr/bin/env bash
# cloud-init : installe Docker, clone le dépôt, démarre Airflow + MongoDB.
# Journal : /var/log/stripe-bootstrap.log
set -euxo pipefail
exec > >(tee -a /var/log/stripe-bootstrap.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git gnupg

# ─── Docker Engine + compose plugin ───
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker ubuntu

# ─── Dépôt ───
mkdir -p /opt/stripe
git clone --branch "${repo_ref}" --depth 1 "${repo_url}" /opt/stripe
chown -R 50000:0 /opt/stripe          # UID airflow dans les conteneurs
chmod -R g+rwX /opt/stripe

# ─── Secrets (uniquement sur l'instance, jamais dans le dépôt) ───
cat > /opt/stripe/deploy/airflow/.env <<EOF
AIRFLOW_UID=50000
AIRFLOW_ADMIN_PASSWORD=${airflow_password}
PGHOST=${rds_host}
PGUSER=${rds_user}
PGPASSWORD=${rds_password}
S3_BUCKET=${s3_bucket}
AWS_DEFAULT_REGION=${aws_region}
MONGO_URI=mongodb://mongo:27017/?replicaSet=rs0
MONGO_RS_HOST=mongo:27017
EOF
chmod 600 /opt/stripe/deploy/airflow/.env
chown 50000:0 /opt/stripe/deploy/airflow/.env

# ─── Démarrage ───
cd /opt/stripe/deploy/airflow
mkdir -p logs results
chown -R 50000:0 logs results
docker compose build
docker compose up -d

echo "bootstrap terminé : $(date -u)"
