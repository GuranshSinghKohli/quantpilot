resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier                      = local.name
  engine                          = "postgres"
  engine_version                  = "16"
  instance_class                  = var.db_instance_class
  allocated_storage               = 20
  storage_encrypted               = true
  db_name                         = "quantpilot"
  username                        = "quantpilot"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  publicly_accessible             = false
  multi_az                        = false
  backup_retention_period         = var.db_backup_retention_days
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.name}-final"
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
}
