#!/bin/bash

# Quick test without signature verification (for development)
# This just tests the webhook endpoint structure

echo "Testing GitHub webhook endpoint..."
echo ""

# Test ping event
echo "1. Testing ping event..."
RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: ping" \
  -H "X-GitHub-Delivery: test-delivery-123" \
  -H "X-Hub-Signature-256: sha256=dummy" \
  -d '{"zen": "Design for failure."}' \
  http://localhost:8000/webhooks/github)

echo "Response: $RESPONSE"
echo ""

# Test unsupported event
echo "2. Testing unsupported event..."
RESPONSE=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-GitHub-Delivery: test-delivery-124" \
  -H "X-Hub-Signature-256: sha256=dummy" \
  -d '{"ref": "refs/heads/main"}' \
  http://localhost:8000/webhooks/github)

echo "Response: $RESPONSE"
echo ""

echo "Note: Signature verification will fail without the correct webhook secret"
echo "To test with real signatures, use: ./scripts/test-webhook.sh"
