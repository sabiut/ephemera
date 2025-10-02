#!/usr/bin/env python3
"""
Test Kubernetes service functionality.
"""

import sys
import os

# Add the api directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from app.services.kubernetes import kubernetes_service

def test_kubernetes_service():
    """Test the Kubernetes service"""

    print("=" * 60)
    print("Testing Kubernetes Service")
    print("=" * 60)

    test_namespace = "test-pr-123-myapp"

    # 1. Create namespace
    print(f"\n1. Creating namespace: {test_namespace}")
    success = kubernetes_service.create_namespace(
        namespace=test_namespace,
        labels={
            "app": "ephemera",
            "pr-number": "123",
            "repo": "myapp"
        }
    )
    print(f"   {'✓' if success else '✗'} Namespace created: {success}")

    # 2. Check if namespace exists
    print(f"\n2. Checking if namespace exists...")
    exists = kubernetes_service.namespace_exists(test_namespace)
    print(f"   {'✓' if exists else '✗'} Namespace exists: {exists}")

    # 3. Get namespace status
    print(f"\n3. Getting namespace status...")
    status = kubernetes_service.get_namespace_status(test_namespace)
    print(f"   Status: {status}")

    # 4. Create resource quota
    print(f"\n4. Creating resource quota...")
    quota_success = kubernetes_service.create_resource_quota(
        namespace=test_namespace,
        cpu_limit="1",
        memory_limit="2Gi",
        pod_limit="5"
    )
    print(f"   {'✓' if quota_success else '✗'} Resource quota created: {quota_success}")

    # 5. Delete namespace
    print(f"\n5. Deleting namespace: {test_namespace}")
    delete_success = kubernetes_service.delete_namespace(test_namespace)
    print(f"   {'✓' if delete_success else '✗'} Namespace deleted: {delete_success}")

    # 6. Verify deletion
    print(f"\n6. Verifying namespace deletion...")
    still_exists = kubernetes_service.namespace_exists(test_namespace)
    print(f"   {'✓' if not still_exists else '✗'} Namespace removed: {not still_exists}")

    print("\n" + "=" * 60)
    if success and exists and quota_success and delete_success and not still_exists:
        print("✓ All Kubernetes service tests passed!")
    else:
        print("✗ Some tests failed")
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_kubernetes_service()
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
