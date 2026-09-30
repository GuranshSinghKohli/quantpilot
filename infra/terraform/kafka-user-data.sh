#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
mkdir -p /var/lib/kafka/data
# apache/kafka runs as uid 1000. A root-owned bind mount fails format with
# AccessDeniedException and the broker never listens.
chown 1000:1000 /var/lib/kafka/data
# The first pull through the NAT gateway can time out. Retry before giving up.
for _ in 1 2 3 4 5; do
  docker pull apache/kafka:4.3.1 && break
  sleep 20
done
TOKEN="$(curl -sS -X PUT 'http://169.254.169.254/latest/api/token' -H 'X-aws-ec2-metadata-token-ttl-seconds: 21600')"
IP="$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)"
docker run -d --name kafka --restart unless-stopped \
  -p 9092:9092 \
  -v /var/lib/kafka/data:/var/lib/kafka/data \
  -e CLUSTER_ID=cXVhbnRwaWxvdC1rYWZrYQ \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_LISTENERS=INTERNAL://:9092,CONTROLLER://:9093 \
  -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://${IP}:9092 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=INTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@127.0.0.1:9093 \
  -e KAFKA_LOG_DIRS=/var/lib/kafka/data \
  -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  apache/kafka:4.3.1
