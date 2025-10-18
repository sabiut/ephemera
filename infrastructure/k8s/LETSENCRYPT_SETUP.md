# Let's Encrypt Setup Guide

This guide explains how to set up Let's Encrypt ClusterIssuer with your email address.

## Overview

The Let's Encrypt ClusterIssuer requires an email address for certificate registration and renewal notifications. This email should be stored in an environment variable, not hardcoded.

## Setup Steps

### 1. Set Email in .env File

The email is already configured in `api/.env`:

```bash
# TLS/SSL (for Let's Encrypt ClusterIssuer)
LETSENCRYPT_EMAIL=suabiut@gmail.com
```

### 2. Generate ClusterIssuer YAML from Template

Use `envsubst` to replace the placeholder with your actual email:

```bash
# From project root
export LETSENCRYPT_EMAIL=$(grep LETSENCRYPT_EMAIL api/.env | cut -d'=' -f2)
envsubst < infrastructure/k8s/letsencrypt-issuer.yaml.template > infrastructure/k8s/letsencrypt-issuer.yaml
```

Or in one command:

```bash
LETSENCRYPT_EMAIL=$(grep LETSENCRYPT_EMAIL api/.env | cut -d'=' -f2) \
  envsubst < infrastructure/k8s/letsencrypt-issuer.yaml.template \
  > infrastructure/k8s/letsencrypt-issuer.yaml
```

### 3. Apply to Kubernetes Cluster

```bash
kubectl apply -f infrastructure/k8s/letsencrypt-issuer.yaml
```

### 4. Verify ClusterIssuer is Ready

```bash
kubectl get clusterissuer
kubectl describe clusterissuer letsencrypt-prod
```

You should see `Ready: True` in the status.

## Files

- **letsencrypt-issuer.yaml.template** - Template with `${LETSENCRYPT_EMAIL}` placeholder (committed to git)
- **letsencrypt-issuer.yaml** - Generated file with actual email (gitignored, NOT committed)
- **api/.env** - Contains `LETSENCRYPT_EMAIL` variable (gitignored, NOT committed)

## Security Notes

- ✅ **Email address is NOT hardcoded** in committed files
- ✅ **Email is stored in .env** which is gitignored
- ✅ **Generated YAML is gitignored** to prevent accidental commits
- ✅ **Template file** is safe to commit (contains placeholder only)

## Troubleshooting

### ClusterIssuer Not Ready

```bash
# Check events
kubectl describe clusterissuer letsencrypt-prod

# Common issues:
# - Email domain is forbidden (example.com, test.com, etc.)
# - Rate limits hit (use letsencrypt-staging for testing)
```

### Email Not Substituted

If you see `${LETSENCRYPT_EMAIL}` in the generated file:

```bash
# Make sure LETSENCRYPT_EMAIL is set
echo $LETSENCRYPT_EMAIL

# If empty, source the .env file
export $(grep LETSENCRYPT_EMAIL api/.env | xargs)
```

## Alternative: Direct Application (One Command)

You can apply directly without creating the intermediate file:

```bash
LETSENCRYPT_EMAIL=$(grep LETSENCRYPT_EMAIL api/.env | cut -d'=' -f2) \
  envsubst < infrastructure/k8s/letsencrypt-issuer.yaml.template \
  | kubectl apply -f -
```

This pipes the substituted YAML directly to kubectl without creating a file.
