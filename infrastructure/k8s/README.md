# Kubernetes Manifests

## Let's Encrypt Certificate Issuers

### Setup

Before applying the cert-manager ClusterIssuers, you need to replace the email placeholder:

```bash
# Load email from .env
source ../../api/.env

# Generate the actual YAML from template
envsubst < letsencrypt-issuer.yaml.template > letsencrypt-issuer-actual.yaml

# Apply to cluster
kubectl apply -f letsencrypt-issuer-actual.yaml
```

Or do it in one command:

```bash
# From project root
export LETSENCRYPT_EMAIL=$(grep LETSENCRYPT_EMAIL api/.env | cut -d'=' -f2)
envsubst < infrastructure/k8s/letsencrypt-issuer.yaml.template | kubectl apply -f -
```

### Files

- `letsencrypt-issuer.yaml` - Generic template (committed to git)
- `letsencrypt-issuer.yaml.template` - Template with ${LETSENCRYPT_EMAIL} variable
- `letsencrypt-issuer-actual.yaml` - Generated file (gitignored, not committed)

### Note

The email address is stored in `api/.env` as `LETSENCRYPT_EMAIL` and is not committed to the repository for privacy.
