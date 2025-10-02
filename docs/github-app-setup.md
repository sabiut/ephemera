# GitHub App Setup Guide

## Step 1: Create a GitHub App

1. Go to GitHub Settings:
   - Personal account: https://github.com/settings/apps
   - Organization: https://github.com/organizations/YOUR_ORG/settings/apps

2. Click **"New GitHub App"**

3. Fill in the details:

   **Basic Information:**
   - **GitHub App name**: `Ephemera (Dev)` or your preferred name
   - **Homepage URL**: `http://localhost:8000` (for now)
   - **Webhook URL**: `https://YOUR_NGROK_URL/webhooks/github` (we'll set this up next)
   - **Webhook secret**: Generate a random string (save this!)
     ```bash
     openssl rand -hex 32
     ```

4. **Repository permissions** (what the app can access):
   - **Contents**: Read-only (to read PR code)
   - **Pull requests**: Read & write (to comment on PRs)
   - **Deployments**: Read & write (optional, for deployment status)
   - **Metadata**: Read-only (repository metadata)

5. **Subscribe to events** (what triggers webhooks):
   -  Pull request
   -  Pull request review
   -  Push (optional)

6. **Where can this GitHub App be installed?**
   - Select: "Only on this account" (for testing)

7. Click **"Create GitHub App"**

## Step 2: Generate and Download Private Key

1. After creating the app, scroll down to **"Private keys"**
2. Click **"Generate a private key"**
3. Download the `.pem` file
4. Move it to your project:
   ```bash
   mv ~/Downloads/your-app-name.*.private-key.pem /home/sabiut/Documents/Personal/ephemera/github-app-key.pem
   chmod 600 /home/sabiut/Documents/Personal/ephemera/github-app-key.pem
   ```

## Step 3: Note Your App Details

From the GitHub App settings page, note these values:

- **App ID**: Found at the top of the settings page
- **Client ID**: In the "Basic information" section
- **Private key path**: `./github-app-key.pem`
- **Webhook secret**: The one you generated earlier

Update your `.env` file:
```bash
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY_PATH=./github-app-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret_here
```

## Step 4: Install the App on a Repository

1. Go to your GitHub App settings page
2. Click **"Install App"** in the left sidebar
3. Select the repository you want to test with
4. Choose either:
   - **All repositories** (not recommended for production)
   - **Only select repositories** (recommended)

## Step 5: Set Up Webhook Tunneling (for local development)

Since GitHub needs to send webhooks to your local machine, use ngrok or similar:

### Option A: Using ngrok
```bash
# Install ngrok
brew install ngrok  # macOS
# or download from https://ngrok.com/download

# Start tunnel
ngrok http 8000
```

This gives you a public URL like: `https://abc123.ngrok.io`

### Option B: Using Cloudflare Tunnel
```bash
# Install cloudflared
brew install cloudflare/cloudflare/cloudflared

# Start tunnel
cloudflared tunnel --url http://localhost:8000
```

### Update Webhook URL
1. Go back to your GitHub App settings
2. Update **Webhook URL** to: `https://YOUR_TUNNEL_URL/webhooks/github`
3. Save changes

## Step 6: Test the Setup

Once everything is configured, test it:

1. Make sure your API is running: `make dev`
2. Create a test PR in your installed repository
3. Check your API logs to see the webhook event

## Troubleshooting

**Webhook not received?**
- Check ngrok/cloudflare tunnel is running
- Verify webhook URL in GitHub App settings
- Check "Recent Deliveries" in GitHub App settings

**Authentication errors?**
- Verify App ID matches your `.env`
- Check private key file exists and is readable
- Ensure webhook secret matches

**Permission denied?**
- Make sure the app is installed on the repository
- Verify permissions in GitHub App settings
