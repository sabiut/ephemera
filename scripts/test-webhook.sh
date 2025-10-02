#!/bin/bash

# Script to test GitHub webhook locally
# This simulates a GitHub webhook event

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}Testing GitHub Webhook Integration${NC}"
echo ""

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-your_webhook_secret_here}"

# Sample PR opened payload
PAYLOAD=$(cat <<'EOF'
{
  "action": "opened",
  "number": 123,
  "pull_request": {
    "id": 123456789,
    "number": 123,
    "title": "Test PR",
    "state": "open",
    "html_url": "https://github.com/test/repo/pull/123",
    "head": {
      "ref": "feature-branch",
      "sha": "abc123def456",
      "repo": {
        "name": "repo",
        "full_name": "test/repo"
      }
    },
    "base": {
      "ref": "main",
      "sha": "def456abc123",
      "repo": {
        "name": "repo",
        "full_name": "test/repo"
      }
    },
    "user": {
      "id": 12345,
      "login": "testuser",
      "avatar_url": "https://avatars.githubusercontent.com/u/12345",
      "html_url": "https://github.com/testuser"
    },
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "merged": false,
    "draft": false
  },
  "repository": {
    "id": 987654321,
    "name": "repo",
    "full_name": "test/repo",
    "private": false,
    "html_url": "https://github.com/test/repo",
    "clone_url": "https://github.com/test/repo.git",
    "default_branch": "main"
  },
  "sender": {
    "id": 12345,
    "login": "testuser",
    "avatar_url": "https://avatars.githubusercontent.com/u/12345",
    "html_url": "https://github.com/testuser"
  },
  "installation": {
    "id": 11111111
  }
}
EOF
)

# Generate signature
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.* //')

# Generate delivery ID
DELIVERY_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "test-delivery-$(date +%s)")

echo -e "${BLUE}Sending webhook to: ${API_URL}/webhooks/github${NC}"
echo -e "${BLUE}Event Type: pull_request${NC}"
echo -e "${BLUE}Delivery ID: ${DELIVERY_ID}${NC}"
echo ""

# Send webhook
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-GitHub-Delivery: ${DELIVERY_ID}" \
  -H "X-Hub-Signature-256: sha256=${SIGNATURE}" \
  -d "$PAYLOAD" \
  "${API_URL}/webhooks/github")

# Extract HTTP code and body
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

# Check response
if [ "$HTTP_CODE" = "200" ]; then
  echo -e "${GREEN}[x] Webhook received successfully!${NC}"
  echo ""
  echo -e "${BLUE}Response:${NC}"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
else
  echo -e "${RED}✗ Webhook failed with HTTP $HTTP_CODE${NC}"
  echo ""
  echo -e "${RED}Response:${NC}"
  echo "$BODY"
fi

echo ""
echo -e "${BLUE}Check your API logs for processing details${NC}"
