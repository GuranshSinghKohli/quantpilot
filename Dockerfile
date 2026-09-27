# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS deps

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    QUANTPILOT_ROOT=/app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Pipeline services build with REQUIREMENTS=requirements-pipeline.txt (adds
# boto3 + confluent-kafka) on top of the layer above, which the API image shares.
ARG REQUIREMENTS=requirements.txt
COPY backend/requirements-pipeline.txt /app/backend/requirements-pipeline.txt
RUN if [ "$REQUIREMENTS" != "requirements.txt" ]; then pip install --no-cache-dir -r "/app/backend/$REQUIREMENTS"; fi

# --- Spark processing (targets spark / spark-test; the API image never builds these) ---
FROM eclipse-temurin:17-jre-noble@sha256:f7537fa73fa7c5bc4e51ea11a611c1d0d8bbf4e9f22de93b666072de98e752e2 AS jre

FROM deps AS spark-base
COPY --from=jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:${PATH}"
COPY backend/requirements-spark.txt /app/backend/requirements-spark.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-spark.txt
# S3A (s3a:// → MinIO / S3). hadoop-aws must equal the Hadoop version pyspark
# bundles (3.4.2); the SDK bundle and analytics accelerator are its declared
# compile dependencies at 3.4.2. SHA-256 pinned; SHA-1 matches Maven Central.
ARG PYSPARK_JARS=/usr/local/lib/python3.11/site-packages/pyspark/jars
ADD --chmod=644 --checksum=sha256:d2d24e6b715b5656b9d238bf13b6682a07e2004dd36aad74a73aeb854319f4d9 \
    https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.jar ${PYSPARK_JARS}/
ADD --chmod=644 --checksum=sha256:62638f33045690c6bc5e496b54b0b2e89a9ad38cbedacdf6e017fea6bc85f55c \
    https://repo1.maven.org/maven2/software/amazon/awssdk/bundle/2.29.52/bundle-2.29.52.jar ${PYSPARK_JARS}/
ADD --chmod=644 --checksum=sha256:11029552e37408e41e689b20150f4b372e0b4f232de321ccafd0fcf443cdfb5a \
    https://repo1.maven.org/maven2/software/amazon/s3/analyticsaccelerator/analyticsaccelerator-s3/1.2.1/analyticsaccelerator-s3-1.2.1.jar ${PYSPARK_JARS}/

FROM spark-base AS spark
COPY backend /app/backend
WORKDIR /app/backend
CMD ["python", "-m", "app.pipeline", "process", "--help"]

# Test runner for the spark tier (and, with the TEST_* variables, every other tier).
FROM spark-base AS spark-test
COPY backend/requirements-dev.txt /app/backend/requirements-dev.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-dev.txt
COPY backend /app/backend
WORKDIR /app/backend
CMD ["python", "-m", "pytest", "tests/spark", "-q"]

# --- API / pipeline image (default target: last stage) ---
FROM deps AS app
COPY backend /app/backend
COPY mcp_server /app/mcp_server

WORKDIR /app/backend
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
