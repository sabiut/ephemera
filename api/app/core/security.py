import hmac
import hashlib
from fastapi import HTTPException, Request
from app.config import get_settings

settings = get_settings()


async def verify_github_webhook(request: Request) -> bytes:
    """
    Verify GitHub webhook signature.

    GitHub sends webhooks with a signature in the X-Hub-Signature-256 header.
    We need to verify this signature matches our computed signature.
    """
    # Get the signature from headers
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        raise HTTPException(
            status_code=403,
            detail="Missing X-Hub-Signature-256 header"
        )

    # Get raw body
    body = await request.body()

    # Compute expected signature
    secret = settings.github_webhook_secret.encode()
    expected_signature = "sha256=" + hmac.new(
        secret,
        body,
        hashlib.sha256
    ).hexdigest()

    # Compare signatures (constant-time comparison)
    if not hmac.compare_digest(signature_header, expected_signature):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature"
        )

    return body


def verify_github_delivery(request: Request) -> str:
    """Extract and return GitHub delivery ID for logging."""
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if not delivery_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-GitHub-Delivery header"
        )
    return delivery_id
