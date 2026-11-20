FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LEADPIPE_CONFIG=/app/config.yaml
WORKDIR /app

COPY pyproject.toml README.md ./
COPY leadpipe ./leadpipe
RUN pip install --no-cache-dir .

# config.yaml and .env are mounted at runtime; the database lives in the /app/data volume.
VOLUME ["/app/data"]
EXPOSE 8787

HEALTHCHECK --interval=2m --timeout=10s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health').status==200 else 1)"
CMD ["leadpipe", "start", "--host", "0.0.0.0"]
