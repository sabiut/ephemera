# Webhook Kubernetes Integration

This document describes the integration between GitHub webhooks and Kubernetes namespace management in Ephemera.

## Overview

The webhook handlers now fully integrate with the Kubernetes service to automatically provision and destroy namespaces based on GitHub pull request events.

## Implementation

### Files Modified

1. **api/app/api/webhooks.py**
   - Imported `kubernetes_service` from `app.services.kubernetes`
   - Updated all webhook handlers to perform actual K8s operations
   - Added comprehensive error handling and status updates
   - Integrated with GitHub status API and PR comments

2. **api/app/services/kubernetes.py**
   - Created Kubernetes client service
   - Implemented namespace creation/deletion
   - Added resource quota management
   - Includes namespace existence verification

### Workflow

#### PR Opened Event

1. Webhook receives PR opened event
2. Creates database records (user, environment, deployment)
3. **Creates Kubernetes namespace** with labels:
   - `app: ephemera`
   - `pr-number: {pr_number}`
   - `repository: {repo_name}`
   - `environment-id: {environment_id}`
4. **Creates resource quota** in namespace:
   - CPU limit: 1 core
   - Memory limit: 2Gi
   - Pod limit: 10
5. Updates environment status to `READY` on success
6. Posts success comment to PR with environment URL
7. Updates GitHub commit status to "success"

On failure:
- Updates environment status to `FAILED`
- Posts failure comment to PR
- Updates GitHub commit status to "failure"

#### PR Closed Event

1. Webhook receives PR closed event
2. Retrieves environment from database
3. Updates environment status to `DESTROYING`
4. **Deletes Kubernetes namespace**
5. Updates environment status to `DESTROYED` on success
6. Posts cleanup completion comment to PR

On failure:
- Updates environment status to `FAILED`
- Logs error

#### PR Synchronize Event (New Commits)

1. Webhook receives PR synchronize event
2. Updates environment commit SHA in database
3. Creates new deployment record
4. **Verifies namespace still exists** in K8s cluster
5. Updates environment status to `READY` if namespace exists
6. Updates GitHub commit status to "success"

If namespace doesn't exist:
- Updates environment status to `FAILED`
- Updates GitHub commit status to "failure"

#### PR Reopened Event

1. Webhook receives PR reopened event
2. Delegates to `handle_pull_request_opened` handler
3. Creates new namespace if needed

## Testing Status

### Completed

- ✅ Webhook handlers updated with K8s integration
- ✅ Namespace creation logic implemented
- ✅ Namespace deletion logic implemented
- ✅ Namespace verification logic implemented
- ✅ Resource quota creation implemented
- ✅ Database status tracking integrated
- ✅ GitHub status updates integrated
- ✅ Error handling added

### Known Issues

1. **Docker Networking**: The API container cannot access the local kind cluster due to Docker networking limitations. The kind API server only listens on `127.0.0.1`, which is not accessible from inside the Docker container.

   **Workaround Options**:
   - Run K8s operations from host using kubectl
   - Deploy to a proper K8s cluster (EKS, GKE, AKS)
   - Use K8s in-cluster config when deployed to cluster

2. **GitHub App Credentials**: Full webhook testing requires setting up a GitHub App with valid credentials.

### Next Steps for Testing

1. **Deploy to Real Cluster**:
   - Set up EKS/GKE cluster
   - Deploy API with in-cluster K8s config
   - Configure GitHub App webhook to point to deployed API

2. **Manual Testing**:
   - Create test PR in GitHub repository
   - Verify namespace creation via kubectl
   - Verify namespace deletion on PR close
   - Check GitHub status updates and PR comments

3. **Integration Tests**:
   - Mock K8s client for unit testing
   - Test webhook handlers with mocked K8s service
   - Verify database state changes

## Configuration

### Environment Variables

```bash
# Kubernetes
KUBECONFIG_PATH=~/.kube/config
CLUSTER_NAME=ephemera-dev

# GitHub
GITHUB_APP_ID=your_app_id
GITHUB_APP_PRIVATE_KEY_PATH=./github-app-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret

# Application
BASE_DOMAIN=preview.yourdomain.com
```

### Resource Quotas

Default resource quotas per namespace:
- **CPU**: 1 core
- **Memory**: 2Gi
- **Pods**: 10

These can be adjusted in [webhooks.py:87-91](../api/app/api/webhooks.py#L87-L91).

## Architecture Diagram

```
GitHub PR Event
      │
      ▼
┌─────────────────┐
│  Webhook        │
│  Handler        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────┐
│  Kubernetes     │─────▶│  K8s API     │
│  Service        │      │  Server      │
└────────┬────────┘      └──────────────┘
         │                      │
         ▼                      ▼
┌─────────────────┐      ┌──────────────┐
│  Database       │      │  Namespace   │
│  (PostgreSQL)   │      │  + Quota     │
└─────────────────┘      └──────────────┘
```

## Code References

- Webhook handlers: [api/app/api/webhooks.py](../api/app/api/webhooks.py)
- Kubernetes service: [api/app/services/kubernetes.py](../api/app/services/kubernetes.py)
- Environment CRUD: [api/app/crud/environment.py](../api/app/crud/environment.py)
- GitHub service: [api/app/services/github.py](../api/app/services/github.py)

## Success Criteria

The integration is considered complete when:

1. ✅ PR opened creates K8s namespace
2. ✅ PR closed deletes K8s namespace
3. ✅ PR synchronized verifies namespace exists
4. ✅ Database tracks environment lifecycle
5. ✅ GitHub receives status updates
6. ✅ PR receives comments on environment changes
7. ⏳ End-to-end test passes with real PR (blocked by Docker networking)

## Future Enhancements

1. **Application Deployment**: Deploy actual applications to namespaces (not just empty namespaces)
2. **Ingress Configuration**: Auto-configure ingress rules for environment URLs
3. **DNS Management**: Auto-create DNS records for preview environments
4. **Cost Tracking**: Track resource usage per environment
5. **Auto-scaling**: Adjust resource quotas based on application needs
6. **Cleanup Jobs**: Periodic cleanup of stale environments
