FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as an unprivileged user. The service never needs root, and a container that
# drops it limits what a compromise of the app can do to the host.
RUN useradd --create-home --uid 10001 appuser

# Persistent library storage: the SQLite database file and the uploaded
# document directory both live under /app/data. Mount a volume (or bind-mount
# a host directory) at /app/data so a container restart or redeploy keeps the
# library -- an image rebuild or `docker run` without this mount starts with
# an empty library instead. See DATABASE_URL and DOCUMENT_STORAGE_DIR in
# .env.example for the equivalent non-Docker local-disk defaults.
RUN mkdir -p /app/data/documents && chown -R appuser:appuser /app/data
ENV DATABASE_URL=sqlite:////app/data/app.db
ENV DOCUMENT_STORAGE_DIR=/app/data/documents
VOLUME ["/app/data"]

USER appuser

EXPOSE 8000

# Reports the container unhealthy when the app stops answering. Uses the Python
# standard library rather than curl, which the slim base image does not ship.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
