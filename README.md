# Ephemera

Environment-as-a-Service Platform

## Quick Start

1. Copy the environment file:
```bash
cp api/.env.example api/.env
```

2. Start the development environment:
```bash
make dev
```

3. Access the API:
- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## Project Structure

```
ephemera/
├── api/              # FastAPI backend
├── worker/           # Celery workers
├── infrastructure/   # Terraform/K8s configs
├── tests/           # Tests
└── docs/            # Documentation
```

## Development

- `make dev` - Start local environment
- `make test` - Run tests
- `make clean` - Clean up containers
- `make install` - Install dependencies

## GitHub Integration

### 1. Create GitHub App

Follow the detailed guide: [docs/github-app-setup.md](docs/github-app-setup.md)

Quick summary:
1. Create a GitHub App at https://github.com/settings/apps
2. Generate and download private key
3. Configure webhook URL (use ngrok for local testing)
4. Update `.env` with your credentials

### 2. Test Webhook Locally

```bash
# Set your webhook secret
export WEBHOOK_SECRET="your_webhook_secret_here"

# Run the test script
./scripts/test-webhook.sh
```

### 3. Set Up Webhook Tunnel (for local development)

```bash
# Using ngrok
ngrok http 8000

# Or using cloudflared
cloudflared tunnel --url http://localhost:8000
```

Update your GitHub App's webhook URL to point to the tunnel.

## Database Integration

### Run Migrations

```bash
# Run database migrations
docker-compose run --rm api alembic upgrade head

# View migration history
docker-compose exec api alembic current
```

### Test Database

```bash
# Run integration test
docker cp scripts/test-db-integration.py ephemera_api_1:/app/test-db-integration.py
docker-compose exec api python /app/test-db-integration.py

# Query via API
curl http://localhost:8000/api/v1/environments/ | jq .
```

See [docs/database-integration-summary.md](docs/database-integration-summary.md) for detailed documentation.

## Progress

1. [x] GitHub webhook integration
2. [x] Database models for environments (User, Environment, Deployment)
3. [x] CRUD operations and REST API
4. [ ] Implement Kubernetes provisioning
5. [ ] Set up background workers (Celery)
6. [ ] Deploy to AWS EKS cluster

## Documentation

- [GitHub App Setup Guide](docs/github-app-setup.md)
- [GitHub Integration Summary](docs/github-integration-summary.md)
- [Database Integration Summary](docs/database-integration-summary.md)
