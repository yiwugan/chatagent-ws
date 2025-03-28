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

# Install runtime dependencies including curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy the source code
COPY chatagent_ws/main.py .
COPY chatagent_ws/ ./src/chatagent_ws/

# Create non-root user
RUN useradd -m -r appuser && chown appuser:appuser /app && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    APP_LOG_LEVEL="INFO" \
    APP_LOG_FILE_PATH="/app/logs" \
    APP_LOG_FILE_ENABLED=True

EXPOSE 8001

# Command to run the FastAPI app with uvicorn
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2", "--timeout-keep-alive", "30", "--timeout-graceful-shutdown", "10"]
CMD ["python3", "main.py"]
