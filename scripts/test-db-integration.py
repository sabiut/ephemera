#!/usr/bin/env python3
"""
Test database integration without GitHub App setup.
This script directly creates records in the database to verify the integration works.
"""

import sys
import os

# Add the api directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from app.database import SessionLocal
from app.crud import user as user_crud
from app.crud import environment as environment_crud
from app.crud import deployment as deployment_crud
from app.models.environment import EnvironmentStatus
from app.models.deployment import DeploymentStatus


def test_database_integration():
    """Test the database integration"""
    db = SessionLocal()

    try:
        print("=" * 60)
        print("Testing Database Integration")
        print("=" * 60)

        # 1. Create a user
        print("\n1. Creating test user...")
        user = user_crud.get_or_create_user(
            db=db,
            github_id=12345,
            github_login="testuser",
            avatar_url="https://avatars.githubusercontent.com/u/12345"
        )
        print(f"   [x] Created user: {user.github_login} (ID: {user.id})")

        # 2. Create an environment
        print("\n2. Creating test environment...")
        environment = environment_crud.create_environment(
            db=db,
            repository_full_name="testorg/testrepo",
            repository_name="testrepo",
            pr_number=123,
            pr_title="Test PR for database integration",
            branch_name="feature/test",
            commit_sha="abc123def456",
            installation_id=11111111,
            owner=user,
            environment_url="https://pr-123-testrepo.preview.example.com"
        )
        print(f"   [x] Created environment: {environment.namespace} (ID: {environment.id})")
        print(f"     Status: {environment.status}")
        print(f"     URL: {environment.environment_url}")

        # 3. Create a deployment
        print("\n3. Creating test deployment...")
        deployment = deployment_crud.create_deployment(
            db=db,
            environment=environment,
            commit_sha="abc123def456",
            commit_message="Test commit"
        )
        print(f"   [x] Created deployment: {deployment.id}")
        print(f"     Status: {deployment.status}")
        print(f"     Commit: {deployment.commit_sha[:8]}")

        # 4. Update environment status
        print("\n4. Updating environment status to READY...")
        environment_crud.update_environment_status(
            db=db,
            environment=environment,
            status=EnvironmentStatus.READY
        )
        print(f"   [x] Environment status: {environment.status}")

        # 5. Query environments
        print("\n5. Querying active environments...")
        active_envs = environment_crud.get_active_environments(db)
        print(f"   [x] Found {len(active_envs)} active environment(s)")

        # 6. Query by PR
        print("\n6. Querying environment by PR...")
        env_by_pr = environment_crud.get_environment_by_pr(
            db=db,
            repository_full_name="testorg/testrepo",
            pr_number=123
        )
        print(f"   [x] Found environment: {env_by_pr.namespace}")

        # 7. Get deployments for environment
        print("\n7. Getting deployments for environment...")
        deployments = deployment_crud.get_deployments_by_environment(
            db=db,
            environment_id=environment.id
        )
        print(f"   [x] Found {len(deployments)} deployment(s)")

        print("\n" + "=" * 60)
        print(" Database Integration Test Complete!")
        print("=" * 60)
        print(f"\nTest Summary:")
        print(f"  - User ID: {user.id}")
        print(f"  - Environment ID: {environment.id}")
        print(f"  - Deployment ID: {deployment.id}")
        print(f"\nYou can now view these in the API:")
        print(f"  curl http://localhost:8000/api/v1/environments/")
        print(f"  curl http://localhost:8000/api/v1/environments/{environment.id}")
        print(f"  curl http://localhost:8000/api/v1/environments/namespace/{environment.namespace}")

    except Exception as e:
        print(f"\n[x] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    test_database_integration()
