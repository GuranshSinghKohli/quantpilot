variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "project_name" {
  type    = string
  default = "quantpilot"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs allowed to reach the public load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "acm_certificate_arn" {
  description = "When set, the load balancer listens on 443. Empty keeps HTTP only. This configuration does not create a certificate."
  type        = string
  default     = ""
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "kafka_instance_type" {
  type    = string
  default = "t3.small"
}

variable "api_image" {
  type    = string
  default = ""
}

variable "worker_image" {
  type    = string
  default = ""
}

variable "frontend_image" {
  type    = string
  default = ""
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "frontend_desired_count" {
  type    = number
  default = 1
}

variable "research_worker_count" {
  description = "ECS tasks running python -m app.research worker. This is a count, not a measured scaling factor."
  type        = number
  default     = 1
}

variable "start_services" {
  description = "Keep false until Secrets Manager values exist. Tasks are not started on the first apply."
  type        = bool
  default     = false
}

variable "lake_bucket_name" {
  type    = string
  default = ""
}

variable "neo4j_uri" {
  description = "External Neo4j bolt URI. Empty leaves the graph projection unwired."
  type        = string
  default     = ""
}

variable "allowed_origins" {
  description = "Comma-separated https origins for the API CORS list."
  type        = string
  default     = "https://app.example.com"
}

variable "github_repository" {
  description = "owner/name allowed to assume the deploy role. Empty skips the GitHub OIDC role."
  type        = string
  default     = ""
}

variable "otel_endpoint" {
  description = "Optional OTLP HTTP endpoint. Empty leaves tracing off."
  type        = string
  default     = ""
}
