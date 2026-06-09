#!/usr/bin/env bash

# ==============================================================================
# Aegis Control Plane - Teardown / Cleanup Script
# ==============================================================================

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${RED}======================================================================${NC}"
echo -e "${RED}        TEARING DOWN PLATFORM CONTROL PLANE CONFIGURATIONS            ${NC}"
echo -e "${RED}======================================================================${NC}"

# Define script and project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Helper function to print step headers
print_step() {
    echo -e "\n${BLUE}>>> [STEP] $1...${NC}"
}

# ------------------------------------------------------------------------------
# STEP 1: Delete ArgoCD Application
# ------------------------------------------------------------------------------
print_step "1/4: Deleting ArgoCD Applications"
echo -e "${YELLOW}Deleting root platform bootstrap Application...${NC}"
kubectl delete application root-platform-bootstrap -n argocd --timeout=30s || true

# List applications and delete them
echo -e "${YELLOW}Deleting workspace application resources...${NC}"
kubectl get applications -n argocd -o name | xargs -r kubectl delete -n argocd || true

# ------------------------------------------------------------------------------
# STEP 2: Delete Tenant Namespaces
# ------------------------------------------------------------------------------
print_step "2/4: Cleaning dynamic Tenant namespaces"
echo -e "${YELLOW}Searching for tenant- namespaces...${NC}"
TENANT_NAMESPACES=$(kubectl get ns -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^tenant-' || true)

if [ -n "${TENANT_NAMESPACES}" ]; then
  for ns in ${TENANT_NAMESPACES}; do
    echo -e "${YELLOW}Deleting namespace: ${ns}...${NC}"
    kubectl delete namespace "${ns}" --timeout=45s || true
  done
else
  echo -e "${GREEN}No tenant namespaces found.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 3: Uninstall Helm Releases
# ------------------------------------------------------------------------------
print_step "3/4: Uninstalling Platform Helm Releases"
echo -e "${YELLOW}Uninstalling HashiCorp Vault...${NC}"
helm uninstall vault -n vault || true
kubectl delete ns vault || true

echo -e "${YELLOW}Uninstalling Kyverno Policy Engine...${NC}"
helm uninstall kyverno -n kyverno || true
echo -e "${YELLOW}Cleaning up leftover Kyverno webhook configurations...${NC}"
kubectl delete validatingwebhookconfiguration kyverno-cel-exception-validating-webhook-cfg kyverno-cleanup-validating-webhook-cfg kyverno-exception-validating-webhook-cfg kyverno-global-context-validating-webhook-cfg kyverno-policy-validating-webhook-cfg kyverno-resource-validating-webhook-cfg kyverno-ttl-validating-webhook-cfg --ignore-not-found=true || true
kubectl delete mutatingwebhookconfiguration kyverno-policy-mutating-webhook-cfg kyverno-resource-mutating-webhook-cfg kyverno-verify-mutating-webhook-cfg --ignore-not-found=true || true
kubectl delete ns kyverno || true

# ------------------------------------------------------------------------------
# STEP 4: Delete Core Namespaces and Policies
# ------------------------------------------------------------------------------
print_step "4/4: Deleting LocalStack, Portal, and Cluster Policies"
kubectl delete -f kubernetes/localstack.yaml || true
kubectl delete ns localstack || true

kubectl delete -f kubernetes/platform-portal.yaml || true
kubectl delete ns control-plane-system || true

kubectl delete clusterpolicy limit-tenant-resources || true
kubectl delete secret aws-creds -n crossplane-system || true

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}            PLATFORM TEARDOWN AND CLEANUP COMPLETED                   ${NC}"
echo -e "${GREEN}======================================================================${NC}\n"
