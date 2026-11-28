resource "aws_secretsmanager_secret" "database_url" {
  name = "${local.name}/database-url"
}

resource "aws_secretsmanager_secret" "openai_api_key" {
  name = "${local.name}/openai-api-key"
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name = "${local.name}/jwt-secret"
}

resource "aws_secretsmanager_secret" "news_api_key" {
  name = "${local.name}/news-api-key"
}

resource "aws_secretsmanager_secret" "market_api_key" {
  name = "${local.name}/market-api-key"
}

resource "aws_secretsmanager_secret" "neo4j_password" {
  count = var.neo4j_uri == "" ? 0 : 1
  name  = "${local.name}/neo4j-password"
}
