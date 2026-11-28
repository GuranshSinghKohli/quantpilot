resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.name}-worker"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${local.name}-frontend"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "this" {
  name = local.name
}

locals {
  api_image      = var.api_image != "" ? var.api_image : "${aws_ecr_repository.api.repository_url}:initial"
  worker_image   = var.worker_image != "" ? var.worker_image : "${aws_ecr_repository.worker.repository_url}:initial"
  frontend_image = var.frontend_image != "" ? var.frontend_image : "${aws_ecr_repository.frontend.repository_url}:initial"
  app_env = [
    { name = "ENVIRONMENT", value = "production" },
    { name = "DB_AUTO_MIGRATE", value = "false" },
    { name = "LLM_PROVIDER", value = "openai" },
    { name = "EMBEDDING_PROVIDER", value = "openai" },
    { name = "LAKE_BACKEND", value = "s3" },
    { name = "LAKE_BUCKET", value = aws_s3_bucket.lake.bucket },
    { name = "LAKE_REGION", value = var.aws_region },
    { name = "KAFKA_BOOTSTRAP_SERVERS", value = "${aws_instance.kafka.private_ip}:9092" },
    { name = "KAFKA_REPLICATION_FACTOR", value = "1" },
    { name = "ALLOWED_ORIGINS", value = var.allowed_origins },
    { name = "ALLOW_HTTP_ORIGINS", value = var.acm_certificate_arn == "" ? "true" : "false" },
    { name = "NEO4J_URI", value = var.neo4j_uri },
    { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = var.otel_endpoint },
    { name = "OTEL_ENABLED", value = var.otel_endpoint == "" ? "false" : "true" },
  ]
  app_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai_api_key.arn },
    { name = "JWT_SECRET_KEY", valueFrom = aws_secretsmanager_secret.jwt_secret.arn },
    { name = "NEWS_API_KEY", valueFrom = aws_secretsmanager_secret.news_api_key.arn },
    { name = "MARKET_API_KEY", valueFrom = aws_secretsmanager_secret.market_api_key.arn },
  ]
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.api.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.api_image
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = concat(local.app_env, [{ name = "SERVICE_NAME", value = "quantpilot-api" }, { name = "OTEL_SERVICE_NAME", value = "quantpilot-api" }])
    secrets      = local.app_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.worker.arn

  container_definitions = jsonencode([{
    name        = "worker"
    image       = local.worker_image
    essential   = true
    command     = ["python", "-m", "app.research", "worker"]
    environment = concat(local.app_env, [{ name = "SERVICE_NAME", value = "quantpilot-research-worker" }, { name = "OTEL_SERVICE_NAME", value = "quantpilot-research-worker" }])
    secrets     = local.app_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.api.arn

  container_definitions = jsonencode([{
    name        = "migrate"
    image       = local.api_image
    essential   = true
    command     = ["alembic", "upgrade", "head"]
    environment = [{ name = "ENVIRONMENT", value = "production" }, { name = "DB_AUTO_MIGRATE", value = "false" }]
    secrets     = [{ name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "migrate"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.frontend.arn

  container_definitions = jsonencode([{
    name         = "frontend"
    image        = local.frontend_image
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.frontend.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "frontend"
      }
    }
  }])
}

resource "aws_lb" "this" {
  name               = local.name
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "ip"

  health_check {
    path = "/health"
  }
}

resource "aws_lb_target_group" "frontend" {
  name        = "${local.name}-web"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "ip"

  health_check {
    path = "/"
  }
}

resource "aws_lb_listener" "http" {
  count             = var.acm_certificate_arn == "" ? 1 : 0
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

locals {
  listener_arn = one(concat(aws_lb_listener.https[*].arn, aws_lb_listener.http[*].arn))
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = local.listener_arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/health", "/ready", "/metrics"]
    }
  }
}

resource "aws_lb_listener_rule" "api_docs" {
  listener_arn = local.listener_arn
  priority     = 11

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/docs", "/redoc", "/openapi.json"]
    }
  }
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.start_services ? var.api_desired_count : 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener_rule.api, aws_lb_listener_rule.api_docs]
}

resource "aws_ecs_service" "frontend" {
  name            = "frontend"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = var.start_services ? var.frontend_desired_count : 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 3000
  }

  depends_on = [aws_lb_listener.http, aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "research-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.start_services ? var.research_worker_count : 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
}
