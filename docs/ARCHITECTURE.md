# Architecture Overview

## System Design

Ephemera is designed as a cloud-native platform for automated preview environment provisioning. The architecture follows a microservices pattern with clear separation of concerns.

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         GitHub                                │
│                    (Pull Requests)                            │
└────────────────────────────┬─────────────────────────────────┘
                             │ Webhooks (HMAC-SHA256 verified)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                      API Gateway                              │
│                    (FastAPI + Uvicorn)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Webhook    │  │  REST API   │  │  Authentication     │  │
│  │  Handler    │  │  Endpoints  │  │  (GitHub App)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└────────────┬──────────────┬──────────────────────────────────┘
             │              │
             ▼              ▼
┌─────────────────┐  ┌──────────────────────┐
│   PostgreSQL    │  │   Redis (Queue)      │
│   Database      │  │                      │
│                 │  │  ┌────────────────┐  │
│  - Users        │  │  │ Celery Worker  │  │
│  - Environments │  │  │  (Planned)     │  │
│  - Deployments  │  │  └────────────────┘  │
└─────────────────┘  └──────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────┐
│              Kubernetes Cluster (Planned)                     │
│                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │  Namespace 1  │  │  Namespace 2  │  │  Namespace N  │   │
│  │  (PR #123)    │  │  (PR #456)    │  │  (PR #...)    │   │
│  └───────────────┘  └───────────────┘  └───────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. API Layer (FastAPI)

**Responsibilities:**
- Receive and validate GitHub webhooks
- Provide REST API for environment management
- Handle authentication and authorization
- Route requests to appropriate services

**Key Files:**
- `api/app/api/webhooks.py` - Webhook event handlers
- `api/app/api/environments.py` - REST endpoints
- `api/app/core/security.py` - Security utilities

### 2. Database Layer (PostgreSQL + SQLAlchemy)

**Schema:**

```sql
users
├── id (PK)
├── github_id (unique)
├── github_login
└── avatar_url

environments
├── id (PK)
├── namespace (unique) - K8s namespace identifier
├── repository_full_name
├── pr_number
├── commit_sha
├── status (enum: pending, provisioning, ready, updating, destroying, destroyed, failed)
├── owner_id (FK → users)
└── installation_id - GitHub App installation

deployments
├── id (PK)
├── environment_id (FK → environments)
├── commit_sha
├── status (enum: queued, in_progress, success, failed)
└── logs
```

**Key Files:**
- `api/app/models/*.py` - SQLAlchemy models
- `api/app/crud/*.py` - Database operations
- `api/alembic/versions/*.py` - Migrations

### 3. GitHub Integration

**Authentication Flow:**
1. GitHub App installed on repository
2. Webhook events sent to `/webhooks/github`
3. Signature verified using HMAC-SHA256
4. Installation token obtained for API access
5. Actions performed (comment, status update)

**Supported Events:**
- `pull_request.opened` → Create environment
- `pull_request.synchronize` → Update environment
- `pull_request.closed` → Destroy environment
- `pull_request.reopened` → Recreate environment

**Key Files:**
- `api/app/services/github.py` - GitHub API client
- `api/app/schemas/github.py` - Webhook payload models

### 4. Background Workers (Planned)

**Celery Tasks:**
- Environment provisioning
- Environment updates
- Environment cleanup
- Periodic maintenance

**Queue Design:**
```
Redis Queue
├── high_priority (create, update)
├── normal_priority (updates)
└── low_priority (cleanup, maintenance)
```

### 5. Kubernetes Integration (Planned)

**Resource Isolation:**
- Each PR gets dedicated namespace
- Resource quotas enforced
- Network policies applied
- Automatic cleanup on PR close

**Namespace Pattern:**
```
pr-{number}-{repo-slug}
Example: pr-123-myapp
```

## Data Flow

### PR Opened Workflow

```
1. GitHub → Webhook Event
   ├── Event: pull_request.opened
   └── Payload: PR metadata

2. API → Validate & Parse
   ├── Verify HMAC signature
   ├── Parse payload
   └── Extract PR data

3. Database → Record Creation
   ├── Get/Create user
   ├── Create environment (status: pending)
   └── Create deployment (status: queued)

4. GitHub → Update PR
   ├── Post comment with environment URL
   └── Update commit status (pending)

5. Queue → Provision Task (Planned)
   ├── Queue Celery task
   └── Return 200 OK

6. Worker → Provision (Planned)
   ├── Create K8s namespace
   ├── Apply manifests
   ├── Configure DNS/ingress
   └── Update status (ready)

7. GitHub → Success Notification
   ├── Update commit status (success)
   └── Post "ready" comment
```

### PR Closed Workflow

```
1. GitHub → Webhook Event
   └── Event: pull_request.closed

2. API → Mark for Cleanup
   ├── Find environment
   └── Update status (destroying)

3. GitHub → Notify
   └── Post cleanup comment

4. Queue → Cleanup Task (Planned)
   └── Queue destruction

5. Worker → Cleanup (Planned)
   ├── Delete K8s namespace
   ├── Remove DNS records
   └── Update status (destroyed)
```

## Security

### Authentication
- GitHub App private key authentication
- Installation-specific access tokens
- Webhook signature verification (HMAC-SHA256)

### Data Protection
- Environment variables for secrets
- `.env` files git-ignored
- Database credentials in environment
- TLS for all external communication

### Access Control
- GitHub App permissions scoped per repository
- Installation-level isolation
- Namespace-based resource isolation (K8s)

## Scalability Considerations

### Current Limitations
- Single FastAPI instance
- No horizontal scaling yet
- Synchronous webhook processing

### Future Enhancements
- Load balancer for multiple API instances
- Celery workers for async processing
- Database connection pooling
- Redis cluster for queue distribution
- Multi-cluster K8s support

## Technology Choices

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| API Framework | FastAPI | Modern, fast, auto-documentation |
| Database | PostgreSQL | ACID compliance, JSON support |
| ORM | SQLAlchemy | Mature, flexible, migration support |
| Queue | Redis | Fast, simple, Celery compatible |
| Worker | Celery | Battle-tested, Python native |
| Container | Docker | Standard, reproducible |
| Orchestration | Kubernetes | Industry standard, scalable |

## Development Patterns

### Repository Pattern
- CRUD operations isolated in `crud/` modules
- Business logic in `services/` modules
- API routes in `api/` modules

### Dependency Injection
- FastAPI's `Depends()` for database sessions
- Singleton services (GitHub client)

### Configuration
- Environment-based configuration
- Pydantic for validation
- `.env` files for local development

## Future Architecture

### Phase 1: Current (v0.1)
- GitHub webhook integration
- Database persistence
- REST API

### Phase 2: Provisioning (v0.2)
- Celery workers
- Kubernetes integration
- Basic deployment

### Phase 3: Production (v0.3)
- Multi-cluster support
- Advanced monitoring
- Cost optimization

### Phase 4: Enterprise (v1.0)
- Multi-cloud support
- Advanced RBAC
- SLA guarantees

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [GitHub Apps Documentation](https://docs.github.com/en/apps)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
