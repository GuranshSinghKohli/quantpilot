resource "aws_cloudwatch_log_group" "api" {
  name              = "/${local.name}/api"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/${local.name}/worker"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/${local.name}/frontend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "kafka" {
  name              = "/${local.name}/kafka"
  retention_in_days = 30
}
