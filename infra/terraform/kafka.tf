data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_instance" "kafka" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.kafka_instance_type
  subnet_id                   = aws_subnet.private[0].id
  vpc_security_group_ids      = [aws_security_group.kafka.id]
  iam_instance_profile        = aws_iam_instance_profile.kafka.name
  user_data                   = file("${path.module}/kafka-user-data.sh")
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted   = true
    volume_size = 30
  }

  depends_on = [aws_nat_gateway.this]
}
