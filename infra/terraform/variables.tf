variable "region" {
  description = "Région AWS (Stockholm : résidence des données UE)"
  type        = string
  default     = "eu-north-1"
}

variable "repo_url" {
  description = "Dépôt public cloné par l'instance au démarrage"
  type        = string
  default     = "https://github.com/emelineroblot/jedha-stripe-project.git"
}

variable "repo_ref" {
  description = "Branche ou tag déployé"
  type        = string
  default     = "main"
}

variable "ec2_instance_type" {
  description = "Airflow + MongoDB : 4 Go de RAM minimum (compte free plan : type éligible free tier obligatoire)"
  type        = string
  default     = "m7i-flex.large"   # éligible free tier, 8 Go
}

variable "rds_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "rds_multi_az" {
  description = "Standby synchrone dans une autre AZ (RPO 0) — double le coût RDS"
  type        = bool
  default     = false
}

variable "operator_cidr" {
  description = "CIDR autorisé pour SSH (clé), Airflow (mot de passe) et RDS (mot de passe). Ouvert par défaut : IP opérateur variable"
  type        = string
  default     = "0.0.0.0/0"
}
