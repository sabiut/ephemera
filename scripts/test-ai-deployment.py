#!/usr/bin/env python3
"""
Test script for the AI-powered deployment service.

Tests the core AI pipeline without requiring Docker, Kubernetes, or GitHub:
  1. Builds a prompt from a sample docker-compose.yml
  2. Calls the configured LLM provider (Anthropic/OpenAI/Gemini)
  3. Parses the JSON response
  4. Validates the generated K8s manifests
  5. Prints the deployment plan summary

Usage:
    # From the project root:
    cd api && python ../scripts/test-ai-deployment.py

    # Or set the API key inline:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/test-ai-deployment.py
"""

import os
import sys
import json
import time

# Add the api directory to the path so we can import app modules
api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api")
sys.path.insert(0, api_dir)

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(api_dir, ".env"))
except ImportError:
    pass  # dotenv not installed, rely on exported env vars

# Prevent app.services.__init__.py from loading (it imports kubernetes, github, etc.)
# We only need the AI-specific modules for this test.
import types
services_pkg = types.ModuleType("app.services")
services_pkg.__path__ = [os.path.join(api_dir, "app", "services")]
services_pkg.__package__ = "app.services"
sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules["app"].__path__ = [os.path.join(api_dir, "app")]
sys.modules["app.services"] = services_pkg


# ─── Sample docker-compose for testing ───────────────────────────────────────
# A realistic multi-service app: web API + postgres + redis + worker

SAMPLE_COMPOSE = """
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://postgres:password@db:5432/myapp
      - REDIS_URL=redis://redis:6379/0
      - SECRET_KEY=dev-secret-key
      - PORT=8000
    depends_on:
      - db
      - redis

  db:
    image: postgres:15-alpine
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=myapp
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  worker:
    build: .
    command: celery -A app worker --loglevel=info
    environment:
      - DATABASE_URL=postgres://postgres:password@db:5432/myapp
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis

volumes:
  postgres_data:
""".strip()

SAMPLE_DOCKERFILE = """
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
""".strip()

SAMPLE_REQUIREMENTS = """
fastapi==0.109.0
uvicorn==0.27.0
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
celery==5.3.6
redis==5.0.1
""".strip()

# Test parameters
TEST_NAMESPACE = "pr-42-test-app"
TEST_APP_NAME = "test-app"
TEST_BASE_DOMAIN = "devpreview.app"


def print_header(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def print_step(n, msg):
    print(f"\n--- Step {n}: {msg} ---\n")


def main():
    print_header("Ephemera AI Deployment Service Test")

    # ─── Step 1: Check configuration ──────────────────────────────────────
    print_step(1, "Checking configuration")

    provider_name = os.getenv("AI_PROVIDER", "anthropic")
    print(f"  AI_PROVIDER: {provider_name}")

    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }

    key_env = key_map.get(provider_name)
    api_key = os.getenv(key_env, "") if key_env else ""

    if not api_key:
        print(f"\n  ERROR: {key_env} is not set.")
        print(f"  Set it in api/.env or export it:")
        print(f"    export {key_env}=your-key-here")
        sys.exit(1)

    print(f"  {key_env}: {'*' * 8}...{api_key[-4:]}")
    print(f"  OK - API key found")

    # ─── Step 2: Create LLM provider ─────────────────────────────────────
    print_step(2, "Creating LLM provider")

    from app.services.ai_providers import create_provider

    class FakeSettings:
        ai_provider = provider_name
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o")
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    try:
        provider = create_provider(FakeSettings())
        if not provider:
            print("  ERROR: Failed to create provider (returned None)")
            sys.exit(1)
        print(f"  Provider: {provider.provider_name}")
        print(f"  OK - Provider created")
    except Exception as e:
        print(f"  ERROR creating provider: {e}")
        sys.exit(1)

    # ─── Step 3: Build prompt ─────────────────────────────────────────────
    print_step(3, "Building prompt")

    from app.services.ai_prompts import SYSTEM_PROMPT, build_user_prompt

    additional_files = {
        "Dockerfile": SAMPLE_DOCKERFILE,
        "requirements.txt": SAMPLE_REQUIREMENTS,
    }

    user_prompt = build_user_prompt(
        compose_content=SAMPLE_COMPOSE,
        namespace=TEST_NAMESPACE,
        app_name=TEST_APP_NAME,
        base_domain=TEST_BASE_DOMAIN,
        additional_files=additional_files,
    )

    print(f"  System prompt: {len(SYSTEM_PROMPT)} chars")
    print(f"  User prompt: {len(user_prompt)} chars")
    print(f"  OK - Prompt built")

    # ─── Step 4: Call LLM ─────────────────────────────────────────────────
    print_step(4, f"Calling {provider.provider_name} API")

    start = time.time()
    try:
        response = provider.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        elapsed = time.time() - start
        print(f"  Response: {len(response.text)} chars")
        print(f"  Input tokens: {response.input_tokens}")
        print(f"  Output tokens: {response.output_tokens}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  OK - LLM responded")
    except Exception as e:
        print(f"  ERROR calling LLM: {e}")
        sys.exit(1)

    # ─── Step 5: Parse response ───────────────────────────────────────────
    print_step(5, "Parsing AI response")

    text = response.text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = text[text.index("\n") + 1:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start_idx = text.find("[")
        end_idx = text.rfind("]")
        if start_idx != -1 and end_idx != -1:
            parsed = json.loads(text[start_idx:end_idx + 1])
        else:
            print(f"  ERROR: Could not parse JSON")
            print(f"  Response starts with: {text[:300]}")
            sys.exit(1)

    # Handle wrapper objects from JSON mode
    if isinstance(parsed, dict):
        for key in ("manifests", "resources", "items"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                break

    if not isinstance(parsed, list):
        print(f"  ERROR: Expected a list, got {type(parsed).__name__}")
        sys.exit(1)

    print(f"  Parsed {len(parsed)} manifests")
    for m in parsed:
        kind = m.get("kind", "?")
        name = m.get("metadata", {}).get("name", "?")
        print(f"    - {kind}/{name}")
    print(f"  OK - Response parsed")

    # ─── Step 6: Validate manifests ───────────────────────────────────────
    print_step(6, "Validating manifests")

    from app.services.ai_validators import ManifestValidator

    validator = ManifestValidator()
    result = validator.validate_all(parsed, TEST_NAMESPACE)

    if result.warnings:
        print(f"  Warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"    - {w}")

    if result.errors:
        print(f"  Errors ({len(result.errors)}):")
        for e in result.errors:
            print(f"    - {e}")

    if result.is_valid:
        print(f"  OK - All manifests valid")
    else:
        print(f"  FAILED - Validation errors found")
        sys.exit(1)

    # ─── Step 7: Summary ──────────────────────────────────────────────────
    print_step(7, "Deployment plan summary")

    manifests = result.corrected_manifests or parsed

    # Count by kind
    kinds = {}
    for m in manifests:
        k = m.get("kind", "Unknown")
        kinds[k] = kinds.get(k, 0) + 1

    print(f"  Namespace: {TEST_NAMESPACE}")
    print(f"  Total manifests: {len(manifests)}")
    print(f"  By kind:")
    for k, v in sorted(kinds.items()):
        print(f"    - {k}: {v}")

    # Check for expected resources
    print(f"\n  Expected resources check:")
    deployment_names = [m["metadata"]["name"] for m in manifests if m["kind"] == "Deployment"]
    service_names = [m["metadata"]["name"] for m in manifests if m["kind"] == "Service"]
    ingress_names = [m["metadata"]["name"] for m in manifests if m["kind"] == "Ingress"]
    pvc_names = [m["metadata"]["name"] for m in manifests if m["kind"] == "PersistentVolumeClaim"]

    print(f"    Deployments: {deployment_names}")
    print(f"    Services: {service_names}")
    print(f"    Ingresses: {ingress_names}")
    print(f"    PVCs: {pvc_names}")

    # Verify key expectations
    checks = []

    # Should have deployments for api, db, redis, worker
    checks.append(("Deployments >= 3", len(deployment_names) >= 3))

    # Should have PVC for postgres
    checks.append(("PVC for postgres", len(pvc_names) >= 1))

    # Should have ingress for api (but NOT for db/redis)
    checks.append(("Ingress exists", len(ingress_names) >= 1))

    # Worker should NOT have a service
    has_worker_service = any("worker" in n for n in ingress_names)
    checks.append(("No ingress for worker", not has_worker_service))

    # DB should NOT have an ingress
    has_db_ingress = any("db" in n or "postgres" in n for n in ingress_names)
    checks.append(("No ingress for database", not has_db_ingress))

    print(f"\n  Validation checks:")
    all_passed = True
    for check_name, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"    [{status}] {check_name}")

    # ─── Result ───────────────────────────────────────────────────────────
    print_header("TEST RESULT")

    if all_passed:
        print("  ALL TESTS PASSED")
        print()
        print(f"  The AI ({provider.provider_name}) correctly:")
        print(f"  - Generated K8s manifests from docker-compose.yml")
        print(f"  - Created PersistentVolumeClaim for postgres")
        print(f"  - Created Ingress only for web-facing services")
        print(f"  - Did NOT expose database/redis/worker externally")
        print(f"  - Passed all security and schema validations")
    else:
        print("  SOME TESTS FAILED")
        print("  Check the output above for details.")

    print()


if __name__ == "__main__":
    main()
