# Celery Integration

This document describes the Celery-based asynchronous task processing system in Ephemera.

## Overview

Celery handles long-running Kubernetes operations asynchronously, preventing webhook handler timeouts and improving responsiveness. The system uses Redis as both the message broker and result backend.

## Architecture

```
Webhook Handler
     │
     ├─> Queue Task (instant response)
     │
     ▼
Celery Worker
     │
     ├─> Kubernetes Operation
     ├─> Database Update
     └─> GitHub Status Update
```

## Components

### 1. Celery App Configuration

**File**: [api/app/core/celery_app.py](../api/app/core/celery_app.py)

- **Broker**: Redis (shared with application cache)
- **Backend**: Redis (stores task results for 1 hour)
- **Task Queues**:
  - `environment` - Provisioning, updating, destroying environments
  - `cleanup` - Periodic cleanup tasks
- **Beat Schedule**: Hourly cleanup of stale environments

**Key Settings**:
- Task time limit: 30 minutes
- Task soft time limit: 25 minutes
- Worker prefetch multiplier: 1 (one task at a time)
- Tasks acknowledge late (ensures task completion on worker failure)

### 2. Environment Tasks

**File**: [api/app/tasks/environment.py](../api/app/tasks/environment.py)

#### provision_environment

Provisions a new environment asynchronously:

1. Create Kubernetes namespace with labels
2. Apply resource quotas (CPU, memory, pods)
3. Update database status to READY
4. Post success comment to GitHub PR
5. Update GitHub commit status

**Retry**: No automatic retry (handled by cleanup task)

**Error Handling**: Updates environment to FAILED status, posts error to GitHub

#### destroy_environment

Destroys an environment asynchronously:

1. Update database status to DESTROYING
2. Delete Kubernetes namespace
3. Update database status to DESTROYED
4. Post cleanup completion comment to GitHub PR

**Retry**: No automatic retry

**Error Handling**: Updates environment to FAILED status

#### update_environment

Updates an environment for new commits:

1. Verify namespace still exists
2. Update database status to READY
3. Update GitHub commit status

**Retry**: No automatic retry

**Error Handling**: Marks environment as FAILED if namespace missing

### 3. Cleanup Tasks

**File**: [api/app/tasks/cleanup.py](../api/app/tasks/cleanup.py)

#### cleanup_stale_environments (Periodic)

Runs every hour via Celery Beat to clean up:

- Environments stuck in PROVISIONING for > 30 minutes
- Environments stuck in DESTROYING for > 30 minutes
- Environments in READY state but namespace doesn't exist

**Actions**:
- Force delete namespace if exists
- Update database status to FAILED or DESTROYED

#### cleanup_old_environments

Deletes environment records that have been DESTROYED for > 7 days (configurable).

**Default**: 7 days retention

**Usage**:
```python
from app.tasks.cleanup import cleanup_old_environments
cleanup_old_environments.delay(days=14)  # Keep for 14 days
```

#### retry_failed_environments

Retries provisioning for recently failed environments (within 1 hour).

**Usage**:
```python
from app.tasks.cleanup import retry_failed_environments
retry_failed_environments.delay(max_age_hours=2)  # Retry within 2 hours
```

## Webhook Integration

### PR Opened

```python
# Queue async provisioning
provision_environment.delay(
    environment_id=environment.id,
    installation_id=installation_id,
    repo_full_name=repo.full_name,
    pr_number=pr.number,
    commit_sha=commit_sha
)

# Immediate response: "Environment is being created..."
```

### PR Closed

```python
# Queue async destruction
destroy_environment.delay(
    environment_id=environment.id,
    installation_id=installation_id,
    repo_full_name=repo.full_name,
    pr_number=pr.number,
    pr_merged=pr.merged
)

# Immediate response: "Environment is being destroyed..."
```

### PR Synchronized

```python
# Queue async update
update_environment.delay(
    environment_id=environment.id,
    commit_sha=commit_sha,
    installation_id=installation_id,
    repo_full_name=repo.full_name,
    pr_number=pr.number
)

# Immediate response: "Updating preview environment..."
```

## Running Celery

### Development (Docker Compose)

```bash
# Start all services including Celery worker and beat
docker-compose up

# View worker logs
docker-compose logs -f celery_worker

# View beat logs
docker-compose logs -f celery_beat
```

### Production

**Worker**:
```bash
celery -A app.core.celery_app worker \
  --loglevel=info \
  --queues=environment,cleanup \
  --concurrency=4 \
  --max-tasks-per-child=1000
```

**Beat Scheduler**:
```bash
celery -A app.core.celery_app beat \
  --loglevel=info
```

**Monitoring** (Flower):
```bash
celery -A app.core.celery_app flower \
  --port=5555
```

## Task Monitoring

### View Active Tasks

```python
from app.core.celery_app import celery_app

# Inspect active tasks
inspect = celery_app.control.inspect()
active_tasks = inspect.active()
```

### View Task Results

```python
from app.tasks.environment import provision_environment

# Queue task
result = provision_environment.delay(environment_id=123)

# Check status
result.ready()  # True if completed
result.successful()  # True if successful
result.result  # Task return value
```

### Manually Trigger Tasks

```bash
# From Django/Flask shell
from app.tasks.cleanup import cleanup_stale_environments
cleanup_stale_environments.delay()
```

## Configuration

### Environment Variables

```bash
# Celery (uses Redis by default)
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Redis
REDIS_URL=redis://redis:6379/0
```

### Beat Schedule

Edit [api/app/core/celery_app.py](../api/app/core/celery_app.py) to modify periodic tasks:

```python
beat_schedule={
    "cleanup-stale-environments": {
        "task": "app.tasks.cleanup.cleanup_stale_environments",
        "schedule": 3600.0,  # Run every hour
    },
    # Add more scheduled tasks here
}
```

## Error Handling

### Task Failures

1. **Soft Time Limit** (25 min): Warning logged, task can attempt cleanup
2. **Hard Time Limit** (30 min): Task killed, environment marked as FAILED
3. **Worker Crash**: Task requeued (due to `task_acks_late=True`)

### Database Transactions

Each task uses a dedicated database session that:
- Commits on success
- Rolls back on error
- Closes automatically after task completion

### Retry Strategy

Tasks do NOT automatically retry. Instead:
- Failed environments remain in FAILED state
- Periodic cleanup task (`retry_failed_environments`) can retry recent failures
- Manual retry via admin interface (future feature)

## Best Practices

1. **Keep Tasks Idempotent**: Tasks should be safe to run multiple times
2. **Short Database Sessions**: Don't hold connections across long operations
3. **Fail Fast**: If a prerequisite fails, mark as FAILED immediately
4. **Log Extensively**: Use structured logging for debugging
5. **Update Status Frequently**: Keep database status in sync with actual state

## Troubleshooting

### Worker Not Processing Tasks

```bash
# Check worker is running
docker-compose ps celery_worker

# Check Redis connection
docker-compose exec redis redis-cli PING

# Check queue depth
docker-compose exec redis redis-cli LLEN celery
```

### Tasks Stuck in Queue

```bash
# Purge all tasks (dangerous!)
celery -A app.core.celery_app purge

# Or purge specific queue
celery -A app.core.celery_app purge -Q environment
```

### High Memory Usage

```bash
# Restart worker to clear memory
docker-compose restart celery_worker

# Or adjust max-tasks-per-child (lower = more frequent restarts)
celery -A app.core.celery_app worker --max-tasks-per-child=100
```

## Future Enhancements

1. **Task Prioritization**: High-priority PRs get faster provisioning
2. **Retry with Backoff**: Automatic retry with exponential backoff
3. **Task Chaining**: Deploy app after namespace creation
4. **Result Hooks**: Webhook callbacks on task completion
5. **Monitoring Dashboard**: Flower or custom dashboard for task monitoring
6. **Task Rate Limiting**: Prevent resource exhaustion

## References

- [Celery Documentation](https://docs.celeryproject.org/)
- [Redis Documentation](https://redis.io/documentation)
- [Task Best Practices](https://docs.celeryproject.org/en/stable/userguide/tasks.html#best-practices)
