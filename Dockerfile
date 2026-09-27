FROM python:3.11-slim

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

COPY backend /app/backend
COPY mcp_server /app/mcp_server

WORKDIR /app/backend
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
