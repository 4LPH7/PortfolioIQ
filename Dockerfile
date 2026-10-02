FROM python:3.12-slim

# Install system deps (psycopg2 needs libpq, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl postgresql-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (caching layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Setup entrypoint script and non-root user
RUN chmod +x /app/docker-entrypoint.sh && \
    mkdir -p /app/logs /app/data && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# Health check
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:5000/api/v1/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "flask_app:app", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
