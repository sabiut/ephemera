# Database Integration Summary

## What We Built

### 1. Database Models

Created three main models to track the environment lifecycle:

#### **User Model** ([api/app/models/user.py](../api/app/models/user.py))
- Stores GitHub user information
- Fields: `github_id`, `github_login`, `email`, `avatar_url`
- Automatically syncs with GitHub on webhook events

#### **Environment Model** ([api/app/models/environment.py](../api/app/models/environment.py))
- Tracks preview environments for PRs
- **Lifecycle States:**
  - `PENDING` - Queued for creation
  - `PROVISIONING` - Being created
  - `READY` - Active and accessible
  - `UPDATING` - Being updated with new code
  - `DESTROYING` - Being torn down
  - `DESTROYED` - Cleaned up
  - `FAILED` - Creation/update failed

- **Key Fields:**
  - `namespace` - Kubernetes namespace (auto-generated)
  - `repository_full_name` - GitHub repo (e.g., "owner/repo")
  - `pr_number` - Pull request number
  - `commit_sha` - Latest commit
  - `environment_url` - Public URL for the environment
  - `status` - Current lifecycle state

#### **Deployment Model** ([api/app/models/deployment.py](../api/app/models/deployment.py))
- Tracks individual deployments to an environment
- Each new commit creates a new deployment
- **States:** `QUEUED`, `IN_PROGRESS`, `SUCCESS`, `FAILED`
- Stores logs and error messages

### 2. Database Migrations (Alembic)

**Setup Files:**
- [api/alembic.ini](../api/alembic.ini) - Alembic configuration
- [api/alembic/env.py](../api/alembic/env.py) - Migration environment
- [api/alembic/versions/](../api/alembic/versions/) - Migration files

**Commands:**
```bash
# Create new migration
docker-compose run --rm api alembic revision --autogenerate -m "description"

# Run migrations
docker-compose run --rm api alembic upgrade head

# Rollback
docker-compose run --rm api alembic downgrade -1
```

### 3. CRUD Operations

Created repository pattern for database operations:

#### **User CRUD** ([api/app/crud/user.py](../api/app/crud/user.py))
- `get_user_by_github_id()` - Find user by GitHub ID
- `get_or_create_user()` - Upsert user (auto-sync from GitHub)
- `create_user()` - Create new user

#### **Environment CRUD** ([api/app/crud/environment.py](../api/app/crud/environment.py))
- `get_environment_by_pr()` - Find environment by repo + PR number
- `get_environment_by_namespace()` - Find by K8s namespace
- `get_active_environments()` - List all active environments
- `create_environment()` - Create new environment
- `update_environment_status()` - Update lifecycle state
- `update_environment_commit()` - Update with new commit

#### **Deployment CRUD** ([api/app/crud/deployment.py](../api/app/crud/deployment.py))
- `create_deployment()` - Create new deployment
- `get_deployments_by_environment()` - Get deployment history
- `get_latest_deployment()` - Get most recent deployment
- `update_deployment_status()` - Update deployment state

### 4. Updated Webhook Handlers

All webhook handlers now persist to database:

**PR Opened:**
1. Create/update user from GitHub data
2. Create environment record (status: PENDING)
3. Create initial deployment record
4. Post comment to PR
5. Update commit status

**PR Synchronized (new commits):**
1. Find existing environment
2. Update commit SHA
3. Create new deployment record
4. Update commit status

**PR Closed:**
1. Find existing environment
2. Update status to DESTROYING
3. Post cleanup comment
4. Queue destruction task (TODO)

### 5. REST API Endpoints

**List Environments:**
```bash
GET /api/v1/environments/
GET /api/v1/environments/?repository=owner/repo
GET /api/v1/environments/?active_only=true
```

**Get Environment:**
```bash
GET /api/v1/environments/{id}
GET /api/v1/environments/namespace/{namespace}
```

## Database Schema

```sql
-- Users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    github_id INTEGER UNIQUE NOT NULL,
    github_login VARCHAR NOT NULL,
    email VARCHAR,
    avatar_url VARCHAR,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Environments table
CREATE TABLE environments (
    id SERIAL PRIMARY KEY,
    repository_full_name VARCHAR NOT NULL,
    repository_name VARCHAR NOT NULL,
    pr_number INTEGER NOT NULL,
    pr_title VARCHAR,
    branch_name VARCHAR NOT NULL,
    commit_sha VARCHAR NOT NULL,
    namespace VARCHAR UNIQUE NOT NULL,
    environment_url VARCHAR,
    status VARCHAR NOT NULL,  -- ENUM
    installation_id INTEGER NOT NULL,
    owner_id INTEGER REFERENCES users(id),
    error_message TEXT,
    last_deployed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    destroyed_at TIMESTAMP WITH TIME ZONE
);

-- Deployments table
CREATE TABLE deployments (
    id SERIAL PRIMARY KEY,
    environment_id INTEGER REFERENCES environments(id),
    commit_sha VARCHAR NOT NULL,
    commit_message VARCHAR,
    status VARCHAR NOT NULL,  -- ENUM
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    logs TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);
```

## Data Flow

```
GitHub PR Event
      ↓
Webhook Handler
      ↓
1. Get/Create User (from PR author)
2. Create/Update Environment
3. Create Deployment record
4. Update status in database
      ↓
Background Task (TODO: Celery)
      ↓
Update deployment status
Update environment status
```

## Testing

### 1. Database Integration Test

```bash
# Run the test script
docker cp scripts/test-db-integration.py ephemera_api_1:/app/test-db-integration.py
docker-compose exec api python /app/test-db-integration.py
```

This creates:
- 1 test user
- 1 test environment
- 1 test deployment

### 2. Query via API

```bash
# List all environments
curl http://localhost:8000/api/v1/environments/ | jq .

# Get specific environment
curl http://localhost:8000/api/v1/environments/1 | jq .

# Get by namespace
curl http://localhost:8000/api/v1/environments/namespace/pr-123-testrepo | jq .
```

### 3. Query Database Directly

```bash
# View users
docker-compose exec -T postgres psql -U postgres -d ephemera \
  -c "SELECT id, github_login FROM users;"

# View environments
docker-compose exec -T postgres psql -U postgres -d ephemera \
  -c "SELECT id, namespace, status, pr_number FROM environments;"

# View deployments
docker-compose exec -T postgres psql -U postgres -d ephemera \
  -c "SELECT id, commit_sha, status FROM deployments;"
```

## What's Working

 Database models defined
 Alembic migrations configured
 CRUD operations implemented
 Webhook handlers persist to database
 REST API returns data from database
 Users auto-created from GitHub webhooks
 Environments tracked through lifecycle
 Deployment history maintained

## What's Next

The database layer is complete! Next steps:

1. **Celery Workers** - Background tasks for actual provisioning
2. **Kubernetes Integration** - Create/destroy namespaces
3. **Deployment Logic** - Actually deploy applications
4. **Cleanup Jobs** - Periodic cleanup of destroyed environments

## File Structure

```
api/
├── alembic/
│   ├── env.py                 # Migration environment
│   └── versions/              # Migration files
├── app/
│   ├── models/
│   │   ├── user.py           # User model
│   │   ├── environment.py    # Environment model
│   │   └── deployment.py     # Deployment model
│   ├── crud/
│   │   ├── user.py           # User CRUD ops
│   │   ├── environment.py    # Environment CRUD ops
│   │   └── deployment.py     # Deployment CRUD ops
│   ├── schemas/
│   │   └── environment.py    # API response schemas
│   └── api/
│       ├── webhooks.py       # Updated with DB persistence
│       └── environments.py   # REST API endpoints
└── alembic.ini               # Alembic config
```

## Common Tasks

**Create migration after model changes:**
```bash
docker-compose run --rm api alembic revision --autogenerate -m "Add new field"
docker-compose run --rm api alembic upgrade head
```

**Reset database (destructive):**
```bash
docker-compose down -v
docker-compose up -d postgres redis
docker-compose run --rm api alembic upgrade head
docker-compose up -d api
```

**View migration history:**
```bash
docker-compose exec api alembic history
docker-compose exec api alembic current
```
