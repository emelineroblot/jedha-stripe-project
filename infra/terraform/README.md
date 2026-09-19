# Infrastructure AWS — Terraform

Voir [`docs/10_deployment_aws.md`](../../docs/10_deployment_aws.md).

```bash
bash tf.sh init
bash tf.sh apply
bash tf.sh output          # airflow_url, ssh, rds_endpoint, s3_bucket ; -raw airflow_login / psql_oltp / psql_olap
bash tf.sh destroy
```

`tf.sh` exécute Terraform dans Docker (contournement de l'interception TLS antivirus sous Windows). Sous macOS/Linux, `terraform` natif fonctionne aussi depuis ce dossier.

Fichiers non versionnés : `keys/` (clé SSH générée), `*.tfstate`, `.terraform/`.
