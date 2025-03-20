# Stage 1: Builder
FROM python:3.12-slim AS builder
WORKDIR /app

# Install system dependencies and Poetry
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && pip install poetry \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libevent-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy the source code
COPY main.py .
COPY src/chatagent_ws/ ./src/chatagent_ws/

# Create non-root user
RUN useradd -m -r appuser && chown appuser:appuser /app && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    APP_API_PORT=8001 \
    APP_LOG_LEVEL="INFO" \
    APP_LOG_FILE_PATH="/app/logs" \
    APP_LOG_FILE_ENABLED=True

EXPOSE 8001

# Run with gunicorn and gevent in production
#CMD ["gunicorn", "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", "--workers", "4", "--bind", "0.0.0.0:8001", "main:app"]
# Multi-stage build with Python 3.12
FROM python:3.12-slim AS builder
WORKDIR /app

# Install system dependencies and Poetry
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && pip install poetry \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

# Final stage with Python 3.12
FROM python:3.12-slim
WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy the source code
COPY main.py .
COPY src/chatagent_ws/ ./src/chatagent_ws/

# Create non-root user
RUN useradd -m -r appuser && chown appuser:appuser /app && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    APP_API_PORT=8002 \
    APP_LOG_LEVEL="INFO" \
    APP_LOG_FILE_PATH="/app/logs" \
    APP_LOG_FILE_ENABLED=True \
    APP_ENV=prod

# Expose the port FastAPI will run on
EXPOSE 8002

# Command to run the FastAPI app with uvicorn
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2", "--timeout-keep-alive", "30", "--timeout-graceful-shutdown", "10"]
CMD ["python3", "main.py"]
