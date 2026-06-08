#!/usr/bin/env bash

# ==============================================================================
# Aegis Control Plane - Multi-Tenant Platform Control Plane Bootstrapping Script
# ==============================================================================

set -e

# Terminal Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}        BOOTSTRAPPING MULTI-TENANT PLATFORM CONTROL PLANE             ${NC}"
echo -e "${CYAN}======================================================================${NC}"

# Define script and project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Helper function to print step headers
print_step() {
    echo -e "\n${BLUE}>>> [STEP] $1...${NC}"
}

# ------------------------------------------------------------------------------
# STEP 1: Deploy LocalStack (AWS API Emulator)
# ------------------------------------------------------------------------------
print_step "1/7: Deploying LocalStack AWS Emulator"
echo -e "${YELLOW}Applying LocalStack deployment and service...${NC}"
kubectl apply -f kubernetes/localstack.yaml

echo -e "${YELLOW}Waiting for LocalStack pod to become ready...${NC}"
kubectl rollout status deployment/localstack -n localstack --timeout=360s
echo -e "${GREEN}[OK] LocalStack is online.${NC}"

# ------------------------------------------------------------------------------
# STEP 2: Deploy Kyverno Policy Engine
# ------------------------------------------------------------------------------
print_step "2/7: Installing Kyverno Admission Controller"
echo -e "${YELLOW}Adding Kyverno Helm repository...${NC}"
helm repo add kyverno https://kyverno.github.io/kyverno/ || true
helm repo update kyverno

echo -e "${YELLOW}Deploying Kyverno into 'kyverno' namespace...${NC}"
helm upgrade --install kyverno kyverno/kyverno \
  --namespace kyverno \
  --create-namespace \
  --set "admissionController.replicas=1" \
  --set "backgroundController.replicas=1" \
  --set "cleanupController.replicas=1" \
  --set "reportsController.replicas=1"

echo -e "${YELLOW}Waiting for Kyverno deployment to be ready...${NC}"
kubectl rollout status deployment/kyverno-admission-controller -n kyverno --timeout=360s
echo -e "${GREEN}[OK] Kyverno policy engine is active.${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Deploy HashiCorp Vault
# ------------------------------------------------------------------------------
print_step "3/7: Installing HashiCorp Vault (Secrets Engine)"
echo -e "${YELLOW}Adding HashiCorp Helm repository...${NC}"
helm repo add hashicorp https://helm.releases.hashicorp.com || true
helm repo update hashicorp

echo -e "${YELLOW}Deploying Vault in development mode...${NC}"
helm upgrade --install vault hashicorp/vault \
  --namespace vault \
  --create-namespace \
  --set "server.dev.enabled=true"

echo -e "${YELLOW}Waiting for Vault server to be ready...${NC}"
kubectl wait --for=condition=Ready pod/vault-0 -n vault --timeout=360s
echo -e "${GREEN}[OK] Vault is active in dev mode.${NC}"

# ------------------------------------------------------------------------------
# STEP 4: Configure Crossplane AWS provider
# ------------------------------------------------------------------------------
print_step "4/7: Configuring Crossplane AWS Provider"
echo -e "${YELLOW}Checking if AWS RDS Provider is installed...${NC}"
kubectl get providers provider-aws

echo -e "${YELLOW}Creating mock AWS Credentials Secret for Crossplane...${NC}"
kubectl create secret generic aws-creds -n crossplane-system \
  --from-literal=credentials="[default]
aws_access_key_id = mock_access_key
aws_secret_access_key = mock_secret_key" \
  --dry-run=client -o yaml | kubectl apply -f -

echo -e "${YELLOW}Applying Crossplane ProviderConfig, Functions, and RDS Compositions...${NC}"
kubectl apply -f crossplane/providers/provider-config.yaml
kubectl apply -f crossplane/providers/function-patch-and-transform.yaml
kubectl apply -f crossplane/compositions/rds-database.yaml
kubectl apply -f crossplane/compositions/db-network-s3.yaml
kubectl apply -f policies/limit-tenant-resources.yaml
echo -e "${GREEN}[OK] Crossplane configurations and Kyverno ClusterPolicies applied.${NC}"

# ------------------------------------------------------------------------------
# STEP 5: Build and Load Platform Control Portal Image
# ------------------------------------------------------------------------------
print_step "5/7: Building Platform Dashboard Image"
echo -e "${YELLOW}Building Docker Image 'control-plane-portal:latest'...${NC}"
docker build -t control-plane-portal:latest .

echo -e "${YELLOW}Loading image into Minikube (profile: multi-tenant-platform)...${NC}"
minikube image load control-plane-portal:latest -p multi-tenant-platform
echo -e "${GREEN}[OK] Image loaded successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 6: Deploy Control Portal Dashboard
# ------------------------------------------------------------------------------
print_step "6/7: Deploying Platform Dashboard Portal"
echo -e "${YELLOW}Applying dashboard deployment and NodePort service...${NC}"
kubectl apply -f kubernetes/platform-portal.yaml

echo -e "${YELLOW}Waiting for dashboard pod to become ready...${NC}"
kubectl rollout status deployment/control-plane-portal -n control-plane-system --timeout=360s
echo -e "${GREEN}[OK] Dashboard is active.${NC}"

# ------------------------------------------------------------------------------
# STEP 7: Register Root GitOps Application
# ------------------------------------------------------------------------------
print_step "7/7: Bootstrapping GitOps Loop via ArgoCD"
echo -e "${YELLOW}Applying ArgoCD Root platform bootstrap Application...${NC}"
kubectl apply -f argocd/bootstrap/application.yaml

echo -e "${YELLOW}Waiting for ArgoCD Application controller to reconcile...${NC}"
sleep 10
echo -e "${GREEN}[OK] ArgoCD root bootstrap active.${NC}"

# ------------------------------------------------------------------------------
# SERVICE ACCESS DETAILS
# ------------------------------------------------------------------------------
# Ensure services are configured as NodePort for direct external access
echo -e "\n${YELLOW}Configuring services for direct external access...${NC}"
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}' >/dev/null 2>&1 || true
kubectl patch svc vault -n vault -p '{"spec": {"type": "NodePort"}}' >/dev/null 2>&1 || true
kubectl patch svc localstack -n localstack -p '{"spec": {"type": "NodePort"}}' >/dev/null 2>&1 || true

MINIKUBE_IP=$(minikube ip -p multi-tenant-platform)
PORTAL_PORT=32080
PORTAL_URL="http://${MINIKUBE_IP}:${PORTAL_PORT}"

# Retrieve NodePorts and Credentials
ARGOCD_PORT=$(kubectl get svc argocd-server -n argocd -o jsonpath='{.spec.ports[?(@.port==80)].nodePort}')
ARGOCD_URL="http://${MINIKUBE_IP}:${ARGOCD_PORT}"
ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

VAULT_PORT=$(kubectl get svc vault -n vault -o jsonpath='{.spec.ports[?(@.port==8200)].nodePort}')
VAULT_URL="http://${MINIKUBE_IP}:${VAULT_PORT}"

LOCALSTACK_PORT=$(kubectl get svc localstack -n localstack -o jsonpath='{.spec.ports[?(@.port==4566)].nodePort}')
LOCALSTACK_URL="http://${MINIKUBE_IP}:${LOCALSTACK_PORT}"

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}      PLATFORM CONTROL PLANE BOOTSTRAP AND GITOPS LOOP COMPLETE       ${NC}"
echo -e "${GREEN}======================================================================${NC}"

echo -e "\n${YELLOW}Service Endpoints:${NC}"
echo -e "----------------------------------------------------------------------"
echo -e "${CYAN}1. Aegis Platform Control Panel Portal (Self-Service)${NC}"
echo -e "   - Access URL:  ${PORTAL_URL}"
echo -e "   - CLI Tunnel:  kubectl port-forward svc/control-plane-portal 5006:5006 -n control-plane-system"

echo -e "\n${CYAN}2. ArgoCD Web Console${NC}"
echo -e "   - Access URL:  ${ARGOCD_URL}"
echo -e "   - Credentials: User: admin / Password: ${ARGOCD_PASSWORD}"
echo -e "   - CLI Tunnel:  kubectl port-forward svc/argocd-server 8080:80 -n argocd"

echo -e "\n${CYAN}3. HashiCorp Vault Server (Dev Mode)${NC}"
echo -e "   - Access URL:  ${VAULT_URL}"
echo -e "   - Status:      Online (namespace: vault)"
echo -e "   - CLI Tunnel:  kubectl port-forward svc/vault 8200:8200 -n vault"

echo -e "\n${CYAN}4. LocalStack AWS Emulator${NC}"
echo -e "   - Access URL:  ${LOCALSTACK_URL}"
echo -e "   - Status:      Online (namespace: localstack, port: 4566)"
echo -e "   - CLI Tunnel:  kubectl port-forward svc/localstack 4566:4566 -n localstack"
echo -e "----------------------------------------------------------------------\n"
