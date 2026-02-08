# GitHub OAuth Setup Guide

This guide will help you set up GitHub OAuth for the Ephemera web dashboard.

## Step 1: Create GitHub OAuth App

1. Go to GitHub Settings → Developer settings → OAuth Apps
   - Direct link: https://github.com/settings/developers

2. Click "New OAuth App"

3. Fill in the details:
   - **Application name**: `Ephemera Dashboard`
   - **Homepage URL**: `https://ephemera-api.devpreview.app`
   - **Authorization callback URL**: `https://ephemera-api.devpreview.app/auth/github/callback`
   - **Application description**: `Preview environment management platform`

4. Click "Register application"

5. You'll see your **Client ID** - copy this

6. Click "Generate a new client secret" and copy the **Client Secret**

## Step 2: Add Environment Variables

Add these to your Ephemera API deployment:

```bash
# GitHub OAuth
GITHUB_OAUTH_CLIENT_ID=<your-client-id>
GITHUB_OAUTH_CLIENT_SECRET=<your-client-secret>
GITHUB_OAUTH_REDIRECT_URI=https://ephemera-api.devpreview.app/auth/github/callback

# Encryption key for credentials
ENCRYPTION_KEY=<generate-with-command-below>
```

### Generate Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Step 3: Update Kubernetes Secret

```bash
# Update the ephemera-secrets with new environment variables
kubectl edit secret ephemera-secrets -n ephemera

# Add base64-encoded values for:
# - GITHUB_OAUTH_CLIENT_ID
# - GITHUB_OAUTH_CLIENT_SECRET
# - ENCRYPTION_KEY
```

Or use kubectl command:

```bash
kubectl create secret generic ephemera-secrets \
  --from-literal=GITHUB_OAUTH_CLIENT_ID=<client-id> \
  --from-literal=GITHUB_OAUTH_CLIENT_SECRET=<client-secret> \
  --from-literal=ENCRYPTION_KEY=<encryption-key> \
  --dry-run=client -o yaml | kubectl apply -f - -n ephemera
```

## Step 4: Run Database Migration

```bash
# SSH into API pod or run locally
cd /app
alembic upgrade head
```

## Step 5: Test the Flow

1. Visit: `https://ephemera-api.devpreview.app`
2. Click "Login with GitHub"
3. Authorize the app
4. You should be redirected to the dashboard
5. Add your GCP credentials
6. Generate an API token
7. Copy the token and add it to your GitHub repo secrets as `EPHEMERA_TOKEN`

## User Flow

```
┌─────────────────────────────────────────────────────────┐
│ 1. User visits https://ephemera-api.devpreview.app     │
│    Clicks "Login with GitHub"                           │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Redirected to GitHub OAuth                           │
│    User authorizes Ephemera                             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Callback to /auth/github/callback                    │
│    - Exchange code for GitHub token                     │
│    - Get GitHub user info                               │
│    - Create/update user in database                     │
│    - Generate session token                             │
│    - Redirect to dashboard                              │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Dashboard page                                       │
│    - Add GCP credentials (encrypted in DB)              │
│    - Generate API tokens                                │
│    - Copy token for GitHub secrets                      │
└─────────────────────────────────────────────────────────┘
```

## API Endpoints

### Authentication
- `GET /auth/github/login` - Initiate OAuth flow
- `GET /auth/github/callback` - OAuth callback handler
- `GET /auth/me` - Get current user info

### Credentials
- `POST /api/v1/credentials/` - Add cloud credentials
- `GET /api/v1/credentials/` - List credentials
- `GET /api/v1/credentials/{id}` - Get credential
- `DELETE /api/v1/credentials/{id}` - Delete credential

### Tokens
- `POST /api/v1/tokens/` - Generate API token
- `GET /api/v1/tokens/` - List tokens
- `POST /api/v1/tokens/{id}/revoke` - Revoke token
- `DELETE /api/v1/tokens/{id}` - Delete token

## Security Notes

- All credentials are encrypted using Fernet (symmetric encryption)
- Encryption key must be kept secure and backed up
- API tokens are hashed and only shown once during creation
- GitHub OAuth uses industry-standard OAuth 2.0 flow
- HTTPS required for production use
