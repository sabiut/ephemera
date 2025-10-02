# Ephemera - Current State

## Overview

Ephemera is an **Environment-as-a-Service (EaaS)** platform that automatically creates preview environments for every pull request. Currently, the foundation is complete with GitHub integration and database persistence.

## What's Built 

### 1. Core Infrastructure

**Docker Stack:**
- PostgreSQL database for persistence
- Redis for caching/queues (ready for Celery)
- FastAPI backend with hot-reload
- Containerized development environment

**Tech Stack:**
- **Backend:** FastAPI (Python 3.11)
- **Database:** PostgreSQL 15
- **ORM:** SQLAlchemy 2.0
- **Migrations:** Alembic
- **Cache/Queue:** Redis 7

### 2. GitHub Integration

**Webhook Handler** ([api/app/api/webhooks.py](../api/app/api/webhooks.py)):
-  Signature verification (HMAC-SHA256)
-  Event parsing and validation
-  Handles PR opened, closed, synchronized, reopened
-  Posts comments to PRs
-  Updates commit status

**GitHub API Client** ([api/app/services/github.py](../api/app/services/github.py)):
-  GitHub App authentication
-  Installation-based access tokens
-  Comment posting
-  Status updates

### 3. Database Layer

**Models:**
-  **User** - GitHub user info
-  **Environment** - Preview environment tracking
-  **Deployment** - Deployment history

**CRUD Operations:**
-  User management (auto-sync from GitHub)
-  Environment lifecycle tracking
-  Deployment history

**Migrations:**
-  Alembic configured
-  Initial schema created
-  Migration workflow established

### 4. REST API

**Endpoints:**
```
GET  /health                              # Health check
GET  /api/v1/environments/                # List environments
GET  /api/v1/environments/{id}            # Get environment
GET  /api/v1/environments/namespace/{ns}  # Get by namespace
POST /webhooks/github                     # GitHub webhook handler
```

## Current Workflow

When a PR is opened on GitHub:

```
1. GitHub sends webhook → /webhooks/github
2. Verify signature
3. Parse PR data
4. Get/Create user in database
5. Create environment record (status: PENDING)
6. Create deployment record
7. Post comment to PR with environment URL
8. Update commit status to "pending"
9. [TODO] Queue Celery task for actual provisioning
```

**Environment URL Format:**
```
https://pr-{number}-{repo}.preview.yourdomain.com
```

**Kubernetes Namespace:**
```
pr-{number}-{repo-slug}  (e.g., pr-123-myapp)
```

## What's Missing 

### 1. Background Workers (Celery)

**What's needed:**
- Celery worker configuration
- Task definitions for:
  - Environment provisioning
  - Environment updates
  - Environment destruction
  - Periodic cleanup

**Status:** Infrastructure ready (Redis), just need to implement tasks

### 2. Kubernetes Integration

**What's needed:**
- K8s Python client configuration
- Namespace creation/deletion logic
- Resource quota management
- Deployment manifest application
- Service/Ingress creation

**Considerations:**
- How will user apps be deployed?
  - Docker Compose → K8s conversion?
  - Helm charts?
  - Custom manifests?

### 3. DNS & Ingress

**What's needed:**
- cert-manager for TLS certificates
- external-dns for automatic DNS records
- Ingress controller (nginx/traefik)
- Wildcard DNS setup

### 4. AWS Infrastructure

**What's needed:**
- Terraform modules for:
  - EKS cluster
  - VPC/networking
  - RDS (production database)
  - Load balancers
  - IAM roles

### 5. Monitoring & Observability

**What's needed:**
- Prometheus/Grafana for metrics
- Centralized logging (ELK/Loki)
- Cost tracking per environment
- Resource usage monitoring

## Next Steps (Prioritized)

### Option 1: Kubernetes Provisioning (Recommended)
**Goal:** Actually create/destroy K8s namespaces

**Tasks:**
1. Set up local Kubernetes (minikube/kind)
2. Implement K8s client service
3. Create namespace creation logic
4. Test locally with sample app

**Why:** Core functionality - without this, environments don't actually exist

### Option 2: Celery Workers
**Goal:** Background task processing

**Tasks:**
1. Configure Celery app
2. Create provisioning tasks
3. Update webhook handlers to queue tasks
4. Test task execution

**Why:** Enables async operations, prevents blocking webhooks

### Option 3: AWS/Production Setup
**Goal:** Deploy to production infrastructure

**Tasks:**
1. Create Terraform modules
2. Set up EKS cluster
3. Configure DNS/ingress
4. Deploy application

**Why:** Move from local dev to real infrastructure

## Testing Status

### What's Tested 

**Database Integration:**
```bash
# Automated test script
docker-compose exec api python /app/test-db-integration.py
```

**API Endpoints:**
```bash
curl http://localhost:8000/api/v1/environments/
```

**Webhook Structure:**
```bash
./scripts/test-webhook.sh
```

### What Needs Testing 

- [ ] K8s namespace creation
- [ ] Actual deployment provisioning
- [ ] Environment cleanup
- [ ] Load testing (concurrent PRs)
- [ ] Failure scenarios

## File Organization

```
ephemera/
├── api/
│   ├── alembic/              # Database migrations
│   ├── app/
│   │   ├── api/              # REST endpoints
│   │   ├── crud/             # Database operations
│   │   ├── models/           # SQLAlchemy models
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # Business logic
│   │   └── core/             # Security, config
│   ├── Dockerfile
│   └── requirements.txt
├── worker/                   # Celery workers (TODO)
├── infrastructure/           # Terraform/K8s (TODO)
├── scripts/                  # Helper scripts
├── docs/                     # Documentation
└── docker-compose.yml
```

## Key Decisions Made

1. **Namespace Isolation:** Each PR gets its own K8s namespace
2. **Database-First:** All state tracked in PostgreSQL
3. **Async Tasks:** Celery for background operations
4. **GitHub App:** App-based auth (not PAT)
5. **Wildcard DNS:** `*.preview.domain.com` pattern

## Key Decisions Pending

1. **App Deployment Method:**
   - How do users define their app deployment?
   - Docker Compose file in repo?
   - Helm chart?
   - Custom manifest?

2. **Resource Limits:**
   - Default CPU/memory per environment?
   - Cost caps per user/organization?

3. **Cleanup Strategy:**
   - Immediate destruction on PR close?
   - Grace period (e.g., 7 days)?
   - Manual override?

4. **Multi-tenancy:**
   - How to isolate customers?
   - Namespace quotas?
   - Network policies?

## Metrics

**Current Scale:**
- Database: 3 tables, fully indexed
- API: 5 endpoints
- Webhook: 4 event handlers
- Test Data: 1 user, 1 environment, 1 deployment

**Target Scale (MVP):**
- 5-10 beta customers
- 50-100 environments
- 100+ deployments/day

## Get Involved

**To continue development:**

1. **Choose next feature** from "Next Steps" above
2. **Read relevant docs:**
   - [GitHub Integration](github-integration-summary.md)
   - [Database Integration](database-integration-summary.md)
3. **Start coding!**

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy Docs](https://docs.sqlalchemy.org/)
- [Kubernetes Python Client](https://github.com/kubernetes-client/python)
- [Celery Docs](https://docs.celeryproject.org/)
