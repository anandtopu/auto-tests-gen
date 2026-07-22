#!/usr/bin/env bash
# Deploy the AI QE platform to OpenShift (or vanilla Kubernetes).
#
#   ./deploy.sh                      in-cluster build (oc) + apply to the current project
#   ./deploy.sh -n ai-qe             target namespace/project ai-qe (created if absent)
#   IMAGE=registry/ai-qe:1.0 ./deploy.sh -n ai-qe    use a prebuilt image (skip build)
#   ./deploy.sh --delete -n ai-qe    tear everything down
#
# Requires: oc (OpenShift) or kubectl. In-cluster build needs oc; with kubectl you
# must supply a prebuilt IMAGE. See docs/deployment.md for the full walkthrough.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT=$(cd ../.. && pwd)

NS=""; ACTION="deploy"
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--namespace) NS="$2"; shift 2 ;;
    --delete)       ACTION="delete"; shift ;;
    -h|--help)      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Prefer oc; fall back to kubectl (no in-cluster build available with kubectl).
if command -v oc >/dev/null 2>&1; then K=oc; else K=kubectl; fi
command -v "$K" >/dev/null || { echo "need oc or kubectl on PATH"; exit 1; }
$K version >/dev/null 2>&1 || { echo "not logged in — run 'oc login' first"; exit 1; }

NSARG=""
if [ -n "$NS" ]; then
  NSARG="-n $NS"
  if [ "$ACTION" = "deploy" ]; then
    $K get namespace "$NS" >/dev/null 2>&1 || \
      { [ "$K" = oc ] && oc new-project "$NS" >/dev/null || $K create namespace "$NS"; }
  fi
fi
CUR_NS=${NS:-$($K config view --minify -o jsonpath='{..namespace}' 2>/dev/null || echo default)}

if [ "$ACTION" = "delete" ]; then
  echo "==> Deleting AI QE resources in namespace: $CUR_NS"
  $K delete $NSARG -k . --ignore-not-found
  $K delete $NSARG secret ai-qe-secrets --ignore-not-found
  echo "    (PVC ai-qe-reports left in place — delete it manually to drop run history)"
  exit 0
fi

# ---- image: prebuilt (IMAGE=...) or in-cluster binary build (oc only) ----
if [ -z "${IMAGE:-}" ]; then
  [ "$K" = oc ] || { echo "kubectl path needs a prebuilt image: IMAGE=... ./deploy.sh"; exit 1; }
  echo "==> Building the image in-cluster (oc binary build)…"
  oc get bc ai-qe-platform $NSARG >/dev/null 2>&1 || \
    oc new-build --name ai-qe-platform --binary --strategy docker $NSARG >/dev/null
  oc start-build ai-qe-platform --from-dir="$REPO_ROOT" --follow $NSARG
  IMAGE="image-registry.openshift-image-registry.svc:5000/${CUR_NS}/ai-qe-platform:latest"
fi
echo "==> Image: $IMAGE"

# ---- secret: real if present, else the example (with a warning) ----
if [ -f secret.yaml ]; then
  echo "==> Applying secret.yaml"
  $K apply $NSARG -f secret.yaml
else
  echo "==> WARNING: no secret.yaml — applying secret.example.yaml (placeholder tokens)."
  echo "    Create real tokens before exposing the Routes: cp secret.example.yaml secret.yaml"
  $K apply $NSARG -f secret.example.yaml
fi

# ---- render kustomize, substitute the image, apply ----
echo "==> Applying manifests…"
$K kustomize . | sed "s|image: ai-qe-platform:latest|image: ${IMAGE}|g" | $K apply $NSARG -f -

echo "==> Waiting for rollout…"
$K rollout status deployment/ai-qe $NSARG --timeout=180s

echo
echo "AI QE platform deployed to namespace: $CUR_NS"
if [ "$K" = oc ]; then
  DUI=$(oc get route ai-qe-dashboard $NSARG -o jsonpath='{.spec.host}' 2>/dev/null || true)
  DHK=$(oc get route ai-qe-receiver  $NSARG -o jsonpath='{.spec.host}' 2>/dev/null || true)
  [ -n "$DUI" ] && echo "  Dashboard:          https://$DUI"
  [ -n "$DHK" ] && echo "  TaskEvent receiver: https://$DHK/hooks/taskevent"
else
  echo "  Vanilla Kubernetes: expose via ingress.yaml, or port-forward to try it:"
  echo "    kubectl $NSARG port-forward deploy/ai-qe 4999:4999 4998:4998"
fi
echo "  Logs: $K logs $NSARG deploy/ai-qe -c dashboard -f"
