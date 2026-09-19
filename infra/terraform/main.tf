# ============================================================
# Stripe business case — infrastructure de production (AWS, eu-north-1)
#
#   RDS PostgreSQL 16   : stripe_oltp (source de vérité) + stripe_olap (star schema)
#   S3                  : staging des données générées et archivage des résultats
#   EC2 (t3.medium)     : Airflow (LocalExecutor) + MongoDB 7 (replica set) via docker compose
#   IAM                 : rôle d'instance (accès S3), aucune clé dans le code
#
#   terraform init && terraform apply
#   terraform destroy      ← après la soutenance (≈ 1,5 $/jour sinon)
# ============================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.70" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
    tls    = { source = "hashicorp/tls", version = "~> 4.0" }
    http   = { source = "hashicorp/http", version = "~> 3.4" }
    local  = { source = "hashicorp/local", version = "~> 2.5" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "stripe-business-case", ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "me" {}

# ───────────────────────── Réseau : VPC par défaut ─────────────────────────

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# IP publique de l'opérateur : seul accès autorisé à SSH, Airflow et psql
data "http" "my_ip" {
  url = "https://checkip.amazonaws.com"
}

locals {
  my_cidr = var.operator_cidr != "" ? var.operator_cidr : "${chomp(data.http.my_ip.response_body)}/32"
  name    = "stripe-pipeline"
}

resource "aws_security_group" "ec2" {
  name        = "${local.name}-ec2"
  description = "Airflow + MongoDB host"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH operateur"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [local.my_cidr]
  }
  ingress {
    description = "Airflow UI operateur"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [local.my_cidr]
  }
  ingress {
    description = "MongoDB operateur (mongosh depuis le poste)"
    from_port   = 27017
    to_port     = 27017
    protocol    = "tcp"
    cidr_blocks = [local.my_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "PostgreSQL OLTP/OLAP"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "PostgreSQL depuis Airflow"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }
  ingress {
    description = "PostgreSQL operateur (psql depuis le poste)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.my_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ───────────────────────── S3 : staging + résultats ─────────────────────────

resource "random_id" "bucket" {
  byte_length = 3
}

resource "aws_s3_bucket" "data" {
  bucket        = "${local.name}-${data.aws_caller_identity.me.account_id}-${random_id.bucket.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

# ───────────────────────── RDS PostgreSQL 16 ─────────────────────────

resource "random_password" "rds" {
  length  = 24
  special = false
}

resource "aws_db_subnet_group" "rds" {
  name       = "${local.name}-rds"
  subnet_ids = data.aws_subnets.default.ids
}

# Réplication logique activée : prérequis Debezium (palier CDC), sans effet sinon
resource "aws_db_parameter_group" "pg16" {
  name   = "${local.name}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "postgres" {
  identifier             = "${local.name}-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.rds_instance_class
  allocated_storage      = 20
  storage_type           = "gp3"
  storage_encrypted      = true
  db_name                = "postgres"
  username               = "stripe"
  password               = random_password.rds.result
  parameter_group_name   = aws_db_parameter_group.pg16.name
  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = true # restreint à l'IP opérateur + SG EC2
  multi_az               = var.rds_multi_az
  backup_retention_period = 1
  skip_final_snapshot    = true
  deletion_protection    = false
  apply_immediately      = true
}

# ───────────────────────── IAM : rôle d'instance (S3 uniquement) ─────────────────────────

resource "aws_iam_role" "ec2" {
  name = "${local.name}-ec2"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "ec2_s3" {
  name = "s3-data-bucket"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.data.arn}/*" }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${local.name}-ec2"
  role = aws_iam_role.ec2.name
}

# ───────────────────────── EC2 : Airflow + MongoDB ─────────────────────────

resource "tls_private_key" "ssh" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "ssh" {
  key_name   = "${local.name}-key"
  public_key = tls_private_key.ssh.public_key_openssh
}

resource "local_sensitive_file" "ssh_key" {
  content         = tls_private_key.ssh.private_key_openssh
  filename        = "${path.module}/keys/${local.name}.pem"
  file_permission = "0600"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "random_password" "airflow_admin" {
  length  = 16
  special = false
}

resource "aws_instance" "airflow" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.ec2_instance_type
  key_name               = aws_key_pair.ssh.key_name
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  # IMDSv2 accessible depuis les conteneurs Docker (2 sauts réseau)
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  user_data = templatefile("${path.module}/user_data.sh", {
    repo_url         = var.repo_url
    repo_ref         = var.repo_ref
    rds_host         = aws_db_instance.postgres.address
    rds_user         = aws_db_instance.postgres.username
    rds_password     = random_password.rds.result
    s3_bucket        = aws_s3_bucket.data.bucket
    aws_region       = var.region
    airflow_password = random_password.airflow_admin.result
  })
  user_data_replace_on_change = true

  tags = { Name = "${local.name}-airflow" }

  depends_on = [aws_db_instance.postgres]
}
