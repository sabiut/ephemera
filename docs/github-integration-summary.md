# GitHub Integration Summary

## What We Built

### 1. Webhook Security ([api/app/core/security.py](../api/app/core/security.py))
- **Signature Verification**: Validates GitHub webhook signatures using HMAC-SHA256
- **Delivery ID Tracking**: Extracts and validates GitHub delivery IDs for logging
- **Protection**: Prevents unauthorized webhook requests

### 2. GitHub API Service ([api/app/services/github.py](../api/app/services/github.py))
- **GitHub App Authentication**: Uses private key for app-level authentication
- **Installation-based Access**: Gets installation-specific tokens for repository access
- **PR Operations**:
  - Post comments to pull requests
  - Update commit status (pending, success, failure, error)
  - Build environment URLs

### 3. Webhook Event Handlers ([api/app/api/webhooks.py](../api/app/api/webhooks.py))
Handles these PR events:

#### PR Opened (`pull_request.opened`)
- Posts initial comment with environment URL
- Sets commit status to "pending"
- Queues environment creation (TODO)

#### PR Closed (`pull_request.closed`)
- Posts cleanup comment
- Distinguishes between merged vs closed
- Queues environment destruction (TODO)

#### PR Synchronized (`pull_request.synchronize`)
- Triggered when new commits are pushed
- Updates commit status
- Queues environment update (TODO)

#### PR Reopened (`pull_request.reopened`)
- Treats as a new PR
- Recreates environment

### 4. Data Models ([api/app/schemas/github.py](../api/app/schemas/github.py))
Pydantic schemas for:
- Pull Request webhook payloads
- Repository information
- User information
- Generic webhook events

## How It Works

```
GitHub PR Event (opened/closed/sync)
         ↓
GitHub sends webhook to /webhooks/github
         ↓
Verify signature (HMAC-SHA256)
         ↓
Parse payload & validate structure
         ↓
Route to appropriate handler based on action
         ↓
- Post comment to PR
- Update commit status
- Queue background task (TODO)
```

## Environment URL Format

```
https://pr-{number}-{repo}.{BASE_DOMAIN}
```

Example:
- PR #123 in repo "my-app"
- Base domain: "preview.yourdomain.com"
- Result: `https://pr-123-my-app.preview.yourdomain.com`

## What's Working Now

 Webhook endpoint receives GitHub events
 Signature verification (when configured)
 Event parsing and validation
 PR action routing (opened, closed, sync, reopened)
 GitHub API integration setup
 Comment posting capability
 Commit status updates

## What's Next (TODO)

 Queue environment creation tasks (Celery)
 Queue environment destruction tasks
 Database models for tracking environments
 Kubernetes provisioning logic
 Actual environment deployment

## Testing

### Local Testing (Without GitHub App)
```bash
# API health check
curl http://localhost:8000/health

# View API docs
open http://localhost:8000/docs
```

### With GitHub App Configured

1. **Set up GitHub App**: Follow [github-app-setup.md](github-app-setup.md)

2. **Test with real webhook**:
   ```bash
   # Set up tunnel
   ngrok http 8000

   # Update webhook URL in GitHub App settings
   # Create a PR in installed repo
   # Watch logs: docker-compose logs -f api
   ```

3. **Test locally with signature**:
   ```bash
   export WEBHOOK_SECRET="your_webhook_secret"
   ./scripts/test-webhook.sh
   ```

## Security Considerations

1. **Webhook Secret**: Keep `GITHUB_WEBHOOK_SECRET` secure
2. **Private Key**: Never commit `github-app-key.pem` (in .gitignore)
3. **Signature Verification**: Always enabled in production
4. **HTTPS Required**: GitHub only sends webhooks to HTTPS endpoints (use ngrok/cloudflared for local dev)

## File Structure

```
api/
├── app/
│   ├── api/
│   │   └── webhooks.py          # Webhook endpoint & handlers
│   ├── services/
│   │   └── github.py             # GitHub API client
│   ├── schemas/
│   │   └── github.py             # Pydantic models
│   └── core/
│       └── security.py           # Webhook verification
```

## Logs to Watch

```bash
# Watch API logs
docker-compose logs -f api

# Look for:
# - "Received GitHub webhook: pull_request"
# - "PR #{number} opened in {repo}"
# - "Posted comment to PR"
# - "Updated status for {sha}"
```

## Common Issues

**GitHub App not configured**:
- Warning: "GitHub App private key not found"
- Solution: Follow setup guide, add private key

**Signature verification fails**:
- Error: "Invalid webhook signature"
- Solution: Ensure `GITHUB_WEBHOOK_SECRET` matches GitHub App settings

**Webhook not received**:
- Check ngrok/cloudflared tunnel is running
- Verify webhook URL in GitHub App settings
- Check "Recent Deliveries" in GitHub App settings for errors

## Next Steps

1. **Set up GitHub App** following [github-app-setup.md](github-app-setup.md)
2. **Test webhook integration** with a real PR
3. **Implement database models** for environment tracking
4. **Build Kubernetes provisioning** logic
5. **Connect to Celery workers** for async tasks
