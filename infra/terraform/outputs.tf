output "vpc_id" {
  value = aws_vpc.this.id
}

output "load_balancer_dns" {
  value = aws_lb.this.dns_name
}

output "lake_bucket" {
  value = aws_s3_bucket.lake.bucket
}

output "postgres_endpoint" {
  value = aws_db_instance.postgres.address
}

output "postgres_secret_arn" {
  description = "RDS-managed username and password. Compose DATABASE_URL from this and postgres_endpoint. Do not commit the value."
  value       = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

output "kafka_bootstrap_servers" {
  value = "${aws_instance.kafka.private_ip}:9092"
}

output "ecr_api" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_worker" {
  value = aws_ecr_repository.worker.repository_url
}

output "ecr_frontend" {
  value = aws_ecr_repository.frontend.repository_url
}

output "github_deploy_role_arn" {
  value = try(aws_iam_role.github_deploy[0].arn, "")
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.arn
}
