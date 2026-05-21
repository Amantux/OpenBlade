FROM python:3.12-slim

WORKDIR /app

# Install system deps for tape tools (simulator mode only) + gosu for entrypoint privilege drop
RUN apt-get update && apt-get install -y --no-install-recommends \
    mtx \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY openblade/ ./openblade/

RUN pip install --no-cache-dir -e .

# Create non-root app user and data directory
RUN groupadd -r openblade && useradd -r -g openblade -d /app openblade \
    && mkdir -p /data \
    && chown openblade:openblade /data

ENV OPENBLADE_DB_PATH=/data/openblade.db
ENV OPENBLADE_BACKEND=simulator
ENV PYTHONUNBUFFERED=1

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "openblade.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
