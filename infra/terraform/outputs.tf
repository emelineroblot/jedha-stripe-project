output "airflow_url" {
  value = "http://${aws_instance.airflow.public_ip}:8080"
}

output "airflow_login" {
  value     = "admin / ${random_password.airflow_admin.result}"
  sensitive = true
}

output "ssh" {
  value = "ssh -i infra/terraform/keys/stripe-pipeline.pem ubuntu@${aws_instance.airflow.public_ip}"
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "psql_oltp" {
  value     = "PGPASSWORD='${random_password.rds.result}' psql -h ${aws_db_instance.postgres.address} -U stripe -d stripe_oltp"
  sensitive = true
}

output "psql_olap" {
  value     = "PGPASSWORD='${random_password.rds.result}' psql -h ${aws_db_instance.postgres.address} -U stripe -d stripe_olap"
  sensitive = true
}

output "mongosh" {
  value = "ssh -i infra/terraform/keys/stripe-pipeline.pem ubuntu@${aws_instance.airflow.public_ip} 'cd /opt/stripe/deploy/airflow && sudo docker compose exec airflow-scheduler mongosh \"mongodb://mongo:27017/stripe_nosql?replicaSet=rs0\"'"
}

output "s3_bucket" {
  value = aws_s3_bucket.data.bucket
}
