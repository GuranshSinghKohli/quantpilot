data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = compact([
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
      aws_secretsmanager_secret.jwt_secret.arn,
      aws_secretsmanager_secret.news_api_key.arn,
      aws_secretsmanager_secret.market_api_key.arn,
      try(aws_secretsmanager_secret.neo4j_password[0].arn, ""),
    ])
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

data "aws_iam_policy_document" "api_task" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "api" {
  name   = "lake-read"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_task.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    # DeleteObject and AbortMultipartUpload are required by the existing Spark
    # S3A commit: it writes a staging prefix, then renames by copy and delete.
    # Scope stays this lake bucket. The API role remains read-only.
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListBucket",
      "s3:GetBucketVersioning",
    ]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
}

resource "aws_iam_role" "worker" {
  name               = "${local.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "lake-write"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_task.json
}

resource "aws_iam_role" "frontend" {
  name               = "${local.name}-frontend"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "kafka_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kafka" {
  name               = "${local.name}-kafka"
  assume_role_policy = data.aws_iam_policy_document.kafka_assume.json
}

resource "aws_iam_role_policy_attachment" "kafka_ssm" {
  role       = aws_iam_role.kafka.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "kafka" {
  name = "${local.name}-kafka"
  role = aws_iam_role.kafka.name
}

data "aws_iam_policy_document" "github_assume" {
  count = var.github_repository == "" ? 0 : 1

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.github_repository == "" ? 0 : 1
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_deploy" {
  count              = var.github_repository == "" ? 0 : 1
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume[0].json
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.github_repository == "" ? 0 : 1

  statement {
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
      "ecs:DescribeTaskDefinition",
      "ecs:RegisterTaskDefinition",
    ]
    resources = ["*"]
  }

  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.api.arn, aws_iam_role.worker.arn, aws_iam_role.frontend.arn]
  }

  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn, aws_ecr_repository.worker.arn, aws_ecr_repository.frontend.arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.github_repository == "" ? 0 : 1
  name   = "deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
