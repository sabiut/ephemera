# Ephemera

**Ephemera** is an Environment-as-a-Service (EaaS) platform that automatically creates isolated preview environments for every pull request. Built with FastAPI, PostgreSQL, and Kubernetes.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Features

- **Automatic PR Environments** - Creates isolated preview environment for each pull request
- **GitHub Integration** - Seamless GitHub App integration with webhook support
- **Database Tracking** - Full lifecycle tracking with PostgreSQL and SQLAlchemy
- **REST API** - Query and manage environments programmatically
- **Containerized** - Docker-based development and deployment

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
   # Edit api/.env with your configuration
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

## API Endpoints

### Environments

```bash
# List all environments
GET /api/v1/environments/

# Get specific environment
GET /api/v1/environments/{id}

# Get by namespace
GET /api/v1/environments/namespace/{namespace}

# Filter by repository
GET /api/v1/environments/?repository=owner/repo

# Active environments only
GET /api/v1/environments/?active_only=true
```

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
