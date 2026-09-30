#!/usr/bin/env bash
# Network isolation check for a running preview (PR #51's policies).
#
# Starts a short-lived pod inside the preview's namespace, with the same
# isolation a preview's own pods get, and tries to reach:
#   - the internet (must work)            - DNS (must work)
#   - Cloud SQL's private IP (blocked)    - Memorystore Redis (blocked)
#   - the GCE metadata server (blocked)   - Ephemera's own API service (blocked)
#   - the Kubernetes API (blocked)
# Prints a PASS/FAIL line per check and exits non-zero if any fails.
#
# Run where kubectl and gcloud reach the cluster (Cloud Shell):
#   bash isolation_check.sh <pr-number> [repository-name]
set -uo pipefail

PR="${1:?usage: isolation_check.sh <pr-number> [repository-name]}"
REPO="${2:-ephemera-test-app}"
PROJECT="${PROJECT:-ephemera-dev-2025}"
REGION="${REGION:-us-central1}"

gcloud container clusters get-credentials ephemera-dev --region "$REGION" --project "$PROJECT" >/dev/null 2>&1

NS=$(kubectl get ns -l "managed-by=ephemera,pr-number=$PR,repository=$REPO" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -z "$NS" ]; then
  echo "No preview namespace for $REPO PR #$PR. Is the preview running?"; exit 2
fi
echo "Preview namespace: $NS"
echo "Policies: $(kubectl get networkpolicy -n "$NS" -o jsonpath='{.items[*].metadata.name}')"

SQL_IP=$(gcloud sql instances list --project "$PROJECT" --format='value(ipAddresses[0].ipAddress)' 2>/dev/null | head -1)
REDIS=$(gcloud redis instances list --project "$PROJECT" --region "$REGION" --format='value(host,port)' 2>/dev/null | head -1)
REDIS_HOST=$(echo "$REDIS" | awk '{print $1}'); REDIS_PORT=$(echo "$REDIS" | awk '{print $2}')
API_IP=$(kubectl get svc ephemera-api -n ephemera-system -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
API_PORT=$(kubectl get svc ephemera-api -n ephemera-system -o jsonpath='{.spec.ports[0].port}' 2>/dev/null)
echo "Targets: Cloud SQL $SQL_IP:5432, Redis $REDIS_HOST:$REDIS_PORT, Ephemera API $API_IP:$API_PORT"

POD="isolation-check-$RANDOM"
# Requests and limits: the preview's ResourceQuota requires them. No service
# account token, like the preview's own pods.
kubectl run "$POD" -n "$NS" --image=curlimages/curl:8.10.1 --restart=Never \
  --labels="app=isolation-check" \
  --overrides='{"spec":{"automountServiceAccountToken":false,"containers":[{"name":"'"$POD"'","image":"curlimages/curl:8.10.1","command":["sleep","300"],"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"securityContext":{"allowPrivilegeEscalation":false}}]}}' \
  >/dev/null
trap 'kubectl delete pod "$POD" -n "$NS" --wait=false >/dev/null 2>&1' EXIT
if ! kubectl wait --for=condition=Ready "pod/$POD" -n "$NS" --timeout=120s >/dev/null; then
  echo "The check pod did not start:"; kubectl describe pod "$POD" -n "$NS" | tail -15; exit 2
fi

FAILED=0
# curl exit codes: 0 answered; 7 refused; 28 timed out; 6 no DNS answer.
check() {  # check <name> <expect: open|blocked> <curl args...>
  local name="$1" expect="$2"; shift 2
  local code
  kubectl exec -n "$NS" "$POD" -- curl -s -o /dev/null --connect-timeout 5 -m 8 "$@" >/dev/null 2>&1
  code=$?
  local reached=no
  [ "$code" -eq 0 ] && reached=yes
  if [ "$expect" = open ] && [ "$reached" = yes ]; then echo "PASS  $name reachable"
  elif [ "$expect" = blocked ] && [ "$reached" = no ]; then echo "PASS  $name blocked (curl exit $code)"
  else echo "FAIL  $name: expected $expect, curl exit $code"; FAILED=1; fi
}

tcp() {  # tcp <name> <host> <port>: blocked unless a TCP connection opens
  local name="$1" host="$2" port="$3"
  if [ -z "$host" ]; then echo "SKIP  $name (address not found)"; return; fi
  # curl -v prints "Connected to" once a TCP connection opens, whatever the
  # protocol behind it; a blocked port only times out.
  if kubectl exec -n "$NS" "$POD" -- curl -sv --connect-timeout 5 -m 6 "telnet://$host:$port" </dev/null 2>&1 \
      | grep -q "Connected to"; then
    echo "FAIL  $name ($host:$port): connection opened"; FAILED=1
  else
    echo "PASS  $name ($host:$port) blocked"
  fi
}

check "Internet (https://www.google.com)" open https://www.google.com
check "DNS + internet (https://api.github.com)" open https://api.github.com
check "Metadata server" blocked -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/
check "Metadata server by name" blocked -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/
check "Kubernetes API" blocked -k https://kubernetes.default.svc/version
tcp "Cloud SQL" "$SQL_IP" 5432
tcp "Memorystore Redis" "$REDIS_HOST" "${REDIS_PORT:-6379}"
tcp "Ephemera API service" "$API_IP" "${API_PORT:-80}"

echo
if [ "$FAILED" -eq 0 ]; then echo "All isolation checks passed."; else echo "Some isolation checks FAILED."; fi
exit "$FAILED"
