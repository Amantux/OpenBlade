FROM python:3.12-slim

WORKDIR /app

# Install system deps for tape tools (simulator mode only)
RUN apt-get update && apt-get install -y --no-install-recommends \
    mtx \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY openblade/ ./openblade/

RUN pip install --no-cache-dir -e .

# Data directory for SQLite
RUN mkdir -p /data

ENV OPENBLADE_DB_PATH=/data/openblade.db
ENV OPENBLADE_BACKEND=simulator
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "openblade.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
