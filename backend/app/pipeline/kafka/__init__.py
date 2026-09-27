"""Kafka transport for the ingestion pipeline (confluent-kafka / librdkafka).

``config`` builds client settings from ``Settings``; ``topics`` creates and
verifies the topic set; ``producer`` publishes with delivery acknowledgement;
``consumer`` builds the manual-commit consumer and reads lag.
"""
