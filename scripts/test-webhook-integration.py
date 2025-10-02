#!/usr/bin/env python3
"""
Test webhook integration with Kubernetes.

This script simulates a GitHub webhook event for PR opened/closed to verify
that the webhook handlers correctly create/delete Kubernetes namespaces.
"""

import requests
import json
import hmac
import hashlib
from datetime import datetime

# Configuration
API_URL = "http://localhost:8000"
WEBHOOK_SECRET = "your_webhook_secret"  # From .env file

def create_signature(payload: str, secret: str) -> str:
    """Create GitHub webhook signature."""
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

def test_pr_opened():
    """Test PR opened webhook."""
    print("\n=== Testing PR Opened Event ===")

    payload = {
        "action": "opened",
        "number": 123,
        "pull_request": {
            "id": 1,
            "number": 123,
            "title": "Test PR for webhook integration",
            "user": {
                "id": 12345,
                "login": "testuser",
                "avatar_url": "https://avatars.githubusercontent.com/u/12345"
            },
            "head": {
                "ref": "feature/test-webhook",
                "sha": "abc123def456"
            },
            "merged": False
        },
        "repository": {
            "id": 1,
            "name": "test-repo",
            "full_name": "testuser/test-repo"
        },
        "installation": {
            "id": 12345678
        }
    }

    payload_str = json.dumps(payload)
    signature = create_signature(payload_str, WEBHOOK_SECRET)

    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": f"test-delivery-{datetime.now().timestamp()}",
        "X-Hub-Signature-256": signature
    }

    response = requests.post(
        f"{API_URL}/webhooks/github",
        data=payload_str,
        headers=headers
    )

    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    if response.status_code == 200:
        print("✓ PR opened webhook processed successfully")
        # Check if namespace was created
        import subprocess
        result = subprocess.run(
            ["kubectl", "get", "namespace", "pr-123-test-repo"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("✓ Kubernetes namespace created successfully")
            print(result.stdout)
        else:
            print("✗ Kubernetes namespace not found")
            print(result.stderr)
    else:
        print("✗ PR opened webhook failed")

    return response.status_code == 200

def test_pr_closed():
    """Test PR closed webhook."""
    print("\n=== Testing PR Closed Event ===")

    payload = {
        "action": "closed",
        "number": 123,
        "pull_request": {
            "id": 1,
            "number": 123,
            "title": "Test PR for webhook integration",
            "user": {
                "id": 12345,
                "login": "testuser",
                "avatar_url": "https://avatars.githubusercontent.com/u/12345"
            },
            "head": {
                "ref": "feature/test-webhook",
                "sha": "abc123def456"
            },
            "merged": False
        },
        "repository": {
            "id": 1,
            "name": "test-repo",
            "full_name": "testuser/test-repo"
        },
        "installation": {
            "id": 12345678
        }
    }

    payload_str = json.dumps(payload)
    signature = create_signature(payload_str, WEBHOOK_SECRET)

    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": f"test-delivery-{datetime.now().timestamp()}",
        "X-Hub-Signature-256": signature
    }

    response = requests.post(
        f"{API_URL}/webhooks/github",
        data=payload_str,
        headers=headers
    )

    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    if response.status_code == 200:
        print("✓ PR closed webhook processed successfully")
        # Check if namespace was deleted
        import subprocess
        import time
        time.sleep(2)  # Wait for async deletion
        result = subprocess.run(
            ["kubectl", "get", "namespace", "pr-123-test-repo"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print("✓ Kubernetes namespace deleted successfully")
        else:
            print("✗ Kubernetes namespace still exists")
            print(result.stdout)
    else:
        print("✗ PR closed webhook failed")

    return response.status_code == 200

def main():
    """Run webhook integration tests."""
    print("Starting Webhook Integration Tests")
    print("=" * 50)

    # Test PR opened
    opened_success = test_pr_opened()

    if opened_success:
        # Wait a bit for async processing
        import time
        time.sleep(3)

        # Test PR closed
        test_pr_closed()

    print("\n" + "=" * 50)
    print("Tests completed")

if __name__ == "__main__":
    main()
