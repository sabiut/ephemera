# Build wheels with a compiler present, then copy only the installed packages
# into a runtime image that has no build tools and no cloud CLIs. The app
# talks to Kubernetes through the Python client with in-cluster config and
# never shells out, so the AWS CLI, kubectl and the gcloud SDK the previous
# image carried were dead weight that every node had to pull on every
# rollout. This image is about 380 MB.
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim

WORKDIR /app

# libpq5 is the only runtime library psycopg2-binary does not bundle on all
# platforms; it is small and avoids a hard-to-diagnose import failure.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

COPY --from=builder /install /usr/local
COPY --chown=app:app . .

USER app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# The same image runs the API, the Celery worker and Celery beat; the
# Kubernetes manifests override the command for the last two.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
