# Ephemera

**Ephemera** is an Environment-as-a-Service (EaaS) platform that automatically creates isolated preview environments for every pull request. Built with FastAPI, PostgreSQL, and Kubernetes.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Features

- **Automatic PR Environments** - Creates isolated preview environment for each pull request
- **GitHub Integration** - Seamless GitHub App integration with webhook support
- **Kubernetes Native** - One isolated namespace per PR, with a ResourceQuota; runs on GKE today, Terraform for EKS included
- **Multi-Cloud Ready** - Terraform modules for AWS and GCP deployment
- **Database Tracking** - Full lifecycle tracking with PostgreSQL and SQLAlchemy
- **REST API** - Query and manage environments programmatically
- **Containerized** - Docker-based development and deployment
- **Cost Optimized** - Spot instances and auto-scaling for minimal cloud spend

## Running the tests

```bash
cd api
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The suite is self-contained (SQLite, no cluster or GitHub access needed) and
runs in CI on every pull request and before each deploy.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- PostgreSQL 15+

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/sabiut/ephemera.git
   cd ephemera
   ```

2. Configure environment:
   ```bash
   cp api/.env.example api/.env
   # Edit api/.env: DATABASE_URL, REDIS_URL, GitHub App + OAuth settings,
   # ENCRYPTION_KEY and BASE_DOMAIN are the ones you must fill in.
   ```

3. Start services:
   ```bash
   make dev
   ```

4. Run migrations:
   ```bash
   docker-compose run --rm api alembic upgrade head
   ```

5. Access the API:
   - API: http://localhost:8000
   - API Docs: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health

## Architecture

```
┌─────────────┐
│   GitHub    │
│  Webhooks   │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────┐
│      FastAPI Backend        │
│  - Webhook Handler          │
│  - GitHub Integration       │
│  - REST API                 │
└──────┬──────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│   PostgreSQL Database       │
│  - Users                    │
│  - Environments             │
│  - Deployments              │
└─────────────────────────────┘
```

See [Architecture Overview](docs/ARCHITECTURE.md) for detailed design.

## GitHub Integration

Ephemera uses a GitHub App to receive webhook events and manage PR environments.

### Setup GitHub App

1. Create a GitHub App at https://github.com/settings/apps
2. Configure webhook URL and secret
3. Download private key
4. Update `.env` with credentials

See [GitHub App Setup Guide](docs/github-app-setup.md) for detailed instructions.

### Webhook Events

- **PR Opened** → Creates new environment
- **PR Synchronized** → Updates environment with new commits
- **PR Closed** → Destroys environment and cleans up resources

## Getting a repository its first preview

Signing in to the dashboard does not connect a repository; installing the Ephemera GitHub App does. The dashboard walks through it:

1. **Connect a repository.** The overview shows a "Get your first preview" checklist until a preview is ready. The Repositories page lists the repositories the App is installed on that you collaborate on, with a link to install it on more.
2. **Check the setup.** "Check setup" reads the compose file on the default branch and reports, per service, whether it can be deployed, whether it is built from each commit, and whether reviewers get a link. It flags what previews ignore (volumes, env_file, entrypoint and so on) and services without ports, which other services cannot reach by name. Each problem comes with a fix. The same report is available at `GET /api/v1/repositories/{owner}/{repo}/check`.
3. **Create the first preview.** Open a pull request, or use "Create preview" next to an open one on the Repositories page (`GET /api/v1/repositories/{owner}/{repo}/pulls` lists them with their preview state). A failed preview offers "Retry preview".

**Which services get a public link.** Every service with `ports:` gets an in-cluster address, so other services reach it by name (for example `db:5432`). Only services that serve HTTP also get a public HTTPS link. Databases, caches and queues (Postgres, MySQL, Redis, Mongo, RabbitMQ, Kafka and similar, recognised by image or by default port) are internal. Override the guess with a compose label:

```yaml
services:
  admin:
    image: acme/admin
    ports: ["9000"]
    labels:
      ephemera.public: "true"    # or "false" to keep an HTTP service internal
```

A preview with no public service fails with an explanation, since a reviewer would have nothing to open.

Cloud credentials and API tokens sit under **Advanced**: previews created from pull requests use neither. They exist for your own CI workflows that call the Ephemera API.

## Previewing a pull request's own code

Ephemera deploys images; it does not build them. For a preview to contain the pull request's changes, the repository's CI builds an image per commit and the compose file refers to it with `${EPHEMERA_SHA}`, which Ephemera replaces with the PR's head commit:

```yaml
# docker-compose.yml
services:
  web:
    build: .
    image: ghcr.io/acme/web:${EPHEMERA_SHA}
    ports: ["8080:8080"]
  db:
    image: postgres:16        # stock images need no change
```

```yaml
# .github/workflows/preview-image.yml
on:
  pull_request:
permissions:
  contents: read
  packages: write
jobs:
  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ghcr.io/acme/web:${{ github.event.pull_request.head.sha }}
```

The webhook usually arrives before CI has pushed the image. While a pod is failing to pull an image tagged with the PR's commit, Ephemera sets the commit status to "Waiting for web image built from abc1234", retries the pull every 30 seconds, and waits up to `PREVIEW_IMAGE_WAIT_SECONDS` (default 600) before reporting that the image was never published.

The cluster pulls the image, so it must be able to reach the registry: make a GitHub Container Registry package public once (package settings, then "Change visibility"), or use a registry the cluster's nodes are authorised for.

Supported substitutions follow docker compose: `${VAR}`, `$VAR`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?message}` (fails the preview with that message), and `$$` for a literal `$`. Ephemera provides `EPHEMERA_SHA`, `EPHEMERA_SHA_SHORT` (7 characters) and `EPHEMERA_REPOSITORY` (the repository's `owner/name` in lowercase, so `image: ghcr.io/${EPHEMERA_REPOSITORY}:${EPHEMERA_SHA}` keeps working in forks); other unset variables become empty strings and are listed in the PR comment. A service with a `build:` section whose image is not tagged per commit still deploys, but the comment warns that it may not contain the PR's changes.

## AI manifest generation

When `AI_DEPLOYMENT_ENABLED` is true, Ephemera asks a language model to turn the repository's compose file, Dockerfiles and config files into Kubernetes manifests, validates and caps the result, and falls back to the built-in compose converter on any failure. The PR comment says when the fallback ran.

| `AI_PROVIDER` | Key | Model setting |
|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_MODEL` |
| `deepseek` | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL` (default `deepseek-flash`; `deepseek-v4-pro` is stronger) |

DeepSeek is by far the cheapest option: a preview plan is a few thousand tokens, a small fraction of a cent on `deepseek-flash`. It is called through its OpenAI-compatible API at `https://api.deepseek.com` (override with `DEEPSEEK_BASE_URL`).

For the GKE workflow, set repository **variables** `AI_PROVIDER` (for example `deepseek`) and optionally `DEEPSEEK_MODEL`, and a repository **secret** `DEEPSEEK_API_KEY`. The next deploy picks them up.

## API Endpoints

### Environments

All environment routes require a Bearer token. Reads are scoped to the caller: you see environments for pull requests you authored and for every repository where GitHub lists you as a collaborator, so reviewers and QA can open teammates' previews. GitHub logins listed in `ADMIN_GITHUB_LOGINS` see every environment. Anything outside that scope returns 404. `GET /auth/me` reports `is_admin`, and `GET /api/v1/repositories` lists the repositories you can see along with the link to install the App on more. Collaborator answers are cached for `REPO_ACCESS_CACHE_SECONDS` (default 300).

```bash
# List your visible environments, newest first
GET /api/v1/environments/

# Get specific environment
GET /api/v1/environments/{id}

# Get by namespace
GET /api/v1/environments/namespace/{namespace}

# Filter by repository
GET /api/v1/environments/?repository=owner/repo

# Active environments only
GET /api/v1/environments/?active_only=true

# Create (or re-provision) the environment for a pull request
POST /api/v1/environments/
{"repository_full_name": "owner/repo", "pr_number": 42}
```

Creating an environment takes nothing about the repository on trust. The server looks the PR up through the GitHub App: the installation used is the one GitHub reports for the repository, the PR must exist, and its author becomes the environment's owner. The caller must be the PR author, a collaborator on the repository, or an admin. `pr_title`, `branch_name` and `commit_sha` may be supplied (a GitHub Actions run knows its own head commit) and otherwise default to the PR's current values. An `installation_id` in the body must match GitHub's or the request is rejected.

### Webhooks

```bash
# GitHub webhook endpoint
POST /webhooks/github
```

See full [API Documentation](http://localhost:8000/docs) when running locally.

## Development

### Project Structure

```
ephemera/
├── api/                    # FastAPI backend
│   ├── app/
│   │   ├── api/           # REST endpoints
│   │   ├── models/        # Database models
│   │   ├── crud/          # Database operations
│   │   ├── services/      # Business logic
│   │   └── core/          # Security, config
│   └── alembic/           # Database migrations
├── worker/                # Celery workers (planned)
├── infrastructure/        # Terraform/K8s (planned)
├── scripts/               # Utility scripts
└── docs/                  # Documentation
```

### Database Migrations

```bash
# Create migration
docker-compose run --rm api alembic revision --autogenerate -m "description"

# Apply migrations
docker-compose run --rm api alembic upgrade head

# Rollback
docker-compose run --rm api alembic downgrade -1
```

### Testing

```bash
# Run integration test
docker cp scripts/test-db-integration.py ephemera_api_1:/app/test.py
docker-compose exec api python /app/test.py

# Query via API
curl http://localhost:8000/api/v1/environments/ | jq .
```

## Roadmap

- [x] GitHub webhook integration
- [x] Database models and persistence
- [x] REST API for environment management
- [x] Kubernetes provisioning service
- [x] Webhook-K8s integration (namespace lifecycle)
- [x] Celery workers for async tasks
- [ ] Application deployment to namespaces
- [ ] DNS and ingress automation
- [ ] Production deployment (AWS EKS)
- [ ] Multi-cloud support

See [TODO.md](TODO.md) for detailed development tasks (local file, not tracked in git).

## Documentation

- [GitHub App Setup](docs/github-app-setup.md)
- [GitHub Integration Summary](docs/github-integration-summary.md)
- [Database Integration](docs/database-integration-summary.md)
- [Webhook-K8s Integration](docs/webhook-k8s-integration.md)
- [Celery Integration](docs/celery-integration.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Current State](docs/current-state.md)

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Tech Stack

- **Backend**: FastAPI (Python 3.11)
- **Database**: PostgreSQL 15
- **ORM**: SQLAlchemy 2.0
- **Migrations**: Alembic
- **Task Queue**: Celery with Redis broker
- **Cache/Queue**: Redis 7
- **Orchestration**: Kubernetes
- **Container**: Docker & Docker Compose

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/sabiut/ephemera/issues)
- **Documentation**: [docs/](docs/)

## Acknowledgments

Built with FastAPI, SQLAlchemy, and the GitHub API.
Testing AWS EKS integration
Ready for EKS testing
Final EKS test
