provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}

data "aws_availability_zones" "available" {
  state = "available"
}
