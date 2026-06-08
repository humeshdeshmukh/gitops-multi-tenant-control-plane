#!/usr/bin/env python3
import time
import sys
import subprocess
import json
import urllib.request
import urllib.error

# Terminal Colors
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
CYAN = '\033[0;36m'
NC = '\033[0m'

def print_status(message, status="OK"):
    if status == "OK":
        print(f"  [{GREEN}✓{NC}] {message}")
    elif status == "WARNING":
        print(f"  [{YELLOW}!{NC}] {message}")
    else:
        print(f"  [{RED}✗{NC}] {message}")

def run_cmd(args):
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {' '.join(args)}\nError: {e.stderr}")
        return None

def check_kubernetes():
    print(f"\n{BLUE}=== Step 1: Checking Kubernetes Control Plane Services ==={NC}")
    
    # Check namespaces
    namespaces = run_cmd(["kubectl", "get", "ns", "-o", "jsonpath={.items[*].metadata.name}"])
    if not namespaces:
        print_status("Kubectl connection failed", "ERROR")
        return False
        
    for ns in ["control-plane-system", "localstack", "vault", "kyverno", "argocd", "crossplane-system"]:
        if ns in namespaces:
            print_status(f"Namespace '{ns}' exists")
        else:
            print_status(f"Namespace '{ns}' is missing", "ERROR")
            return False
            
    # Check pods status
    for ns, label in [("control-plane-system", "app=control-plane-portal"), 
                      ("localstack", "app=localstack"), 
                      ("vault", "app.kubernetes.io/name=vault"), 
                      ("kyverno", "app.kubernetes.io/part-of=kyverno")]:
        pods = run_cmd(["kubectl", "get", "pods", "-n", ns, "-l", label, "-o", "jsonpath={.items[*].status.phase}"])
        if pods and "Running" in pods:
            print_status(f"Pods in '{ns}' are Running")
        else:
            print_status(f"Pods in '{ns}' are not healthy or missing: {pods}", "WARNING")
            
    return True

def check_crossplane():
    print(f"\n{BLUE}=== Step 2: Checking Crossplane Compositions & CRDs ==={NC}")
    
    # ProviderConfig
    pc = run_cmd(["kubectl", "get", "providerconfig.aws.upbound.io", "default", "-o", "jsonpath={.metadata.name}"])
    if pc == "default":
        print_status("Crossplane default ProviderConfig exists")
    else:
        print_status("Crossplane default ProviderConfig is missing", "ERROR")
        
    # Compositions
    comp = run_cmd(["kubectl", "get", "compositions", "-o", "jsonpath={.items[*].metadata.name}"])
    if comp and "compositepostgresqlinstances.platform.devops.local" in comp:
        print_status("PostgreSQL database composition is registered")
    else:
        print_status("PostgreSQL database composition is missing", "ERROR")
        
    # Kyverno Policy
    policies = run_cmd(["kubectl", "get", "clusterpolicies", "-o", "jsonpath={.items[*].metadata.name}"])
    if policies and "limit-tenant-resources" in policies:
        print_status("Kyverno resource limits policy is active")
    else:
        print_status("Kyverno resource limits policy is missing", "ERROR")
        
    return True

def test_gitops_flow():
    print(f"\n{BLUE}=== Step 3: Verifying Self-Service Provisioning API ==={NC}")
    
    # Try localhost first (for local port-forwarding)
    portal_url = "http://localhost:5006"
    status_endpoint = f"{portal_url}/api/v1/k8s/status"
    
    print(f"Connecting to Portal at: {portal_url}")
    connected = False
    
    try:
        req = urllib.request.Request(status_endpoint)
        with urllib.request.urlopen(req, timeout=3) as response:
            status_data = json.loads(response.read().decode('utf-8'))
            print_status(f"Connected to portal API on localhost. Active tenants count: {len(status_data.get('tenants', []))}")
            connected = True
    except Exception as e:
        print(f"  Localhost connection failed, checking Minikube NodePort IP...")
        
    if not connected:
        minikube_ip = run_cmd(["minikube", "ip", "-p", "multi-tenant-platform"])
        if minikube_ip:
            portal_url = f"http://{minikube_ip}:32080"
            status_endpoint = f"{portal_url}/api/v1/k8s/status"
            print(f"Connecting to Portal at: {portal_url}")
            try:
                req = urllib.request.Request(status_endpoint)
                with urllib.request.urlopen(req, timeout=5) as response:
                    status_data = json.loads(response.read().decode('utf-8'))
                    print_status(f"Connected to portal API on Minikube IP. Active tenants: {len(status_data.get('tenants', []))}")
                    connected = True
            except Exception as ex:
                pass
                
    if not connected:
        print_status("Failed to connect to Portal API. Please make sure port-forward is running.", "ERROR")
        print(f"  Run: {YELLOW}kubectl port-forward svc/control-plane-portal 5006:5006 -n control-plane-system{NC} in another terminal.")
        return False
        
    provision_endpoint = f"{portal_url}/api/v1/provision"
    deprovision_endpoint = f"{portal_url}/api/v1/deprovision"

    # Provision tenant-integration-test
    tenant_payload = {
        "tenant_name": "integration-test",
        "db_storage": 10,
        "db_class": "db.t4g.micro",
        "db_version": "14.5"
    }
    
    print(f"Provisioning tenant 'tenant-integration-test' via API...")
    try:
        req = urllib.request.Request(
            provision_endpoint,
            data=json.dumps(tenant_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get("success"):
                print_status(f"Provisioning request succeeded: {res_data.get('message')}")
            else:
                print_status(f"Provisioning failed: {res_data.get('message')}", "ERROR")
                return False
    except Exception as e:
        print_status(f"Failed to post provisioning request: {e}", "ERROR")
        return False
    # Trigger ArgoCD sync on root platform
    sync_endpoint = f"{portal_url}/api/v1/sync"
    print("Triggering manual ArgoCD sync on root-platform-bootstrap...")
    try:
        req = urllib.request.Request(
            sync_endpoint,
            data=json.dumps({"app_name": "root-platform-bootstrap"}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            print_status("ArgoCD manual sync triggered on root-platform-bootstrap")
    except Exception as e:
        print_status(f"Failed to trigger ArgoCD sync: {e}", "WARNING")

    # Wait for ArgoCD reconciliation
    print(f"Waiting 15 seconds for ArgoCD to detect Git commit and provision resources...")
    time.sleep(15)
    
    # Verify Namespace is created
    namespaces = run_cmd(["kubectl", "get", "ns", "-o", "jsonpath={.items[*].metadata.name}"])
    if "tenant-integration-test" in namespaces:
        print_status("Tenant namespace 'tenant-integration-test' was created by GitOps sync")
    else:
        print_status("Tenant namespace 'tenant-integration-test' was not created", "ERROR")
        return False
        
    # Verify NetworkPolicy exists
    netpols = run_cmd(["kubectl", "get", "netpol", "-n", "tenant-integration-test", "-o", "jsonpath={.items[*].metadata.name}"])
    if "deny-cross-tenant" in netpols:
        print_status("NetworkPolicy 'deny-cross-tenant' successfully applied")
    else:
        print_status("NetworkPolicy was not applied in tenant namespace", "ERROR")
        
    # Verify Crossplane Claim exists
    claims = run_cmd(["kubectl", "get", "postgresqlinstances.platform.devops.local", "-n", "tenant-integration-test", "-o", "jsonpath={.items[*].metadata.name}"])
    if "tenant-integration-test-db" in claims:
        print_status("Crossplane database claim 'tenant-integration-test-db' successfully synced")
    else:
        print_status("Crossplane database claim was not found in tenant namespace", "ERROR")

    # Clean up and Deprovision
    print(f"Cleaning up: Deprovisioning tenant 'tenant-integration-test' via API...")
    try:
        req = urllib.request.Request(
            deprovision_endpoint,
            data=json.dumps({"tenant_name": "tenant-integration-test"}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get("success"):
                print_status(f"Deprovisioning request succeeded: {res_data.get('message')}")
            else:
                print_status(f"Deprovisioning failed: {res_data.get('message')}", "ERROR")
    except Exception as e:
        print_status(f"Failed to post deprovisioning request: {e}", "WARNING")

    return True

if __name__ == "__main__":
    print(f"{CYAN}====================================================={NC}")
    print(f"{CYAN}    AEGIS CONTROL PLANE PLATFORM VERIFICATION TEST    {NC}")
    print(f"{CYAN}====================================================={NC}")
    
    k8s_ok = check_kubernetes()
    xp_ok = check_crossplane()
    
    if k8s_ok and xp_ok:
        test_gitops_flow()
    else:
        print(f"\n{RED}Verification halted: Prerequisites not met.{NC}")
        sys.exit(1)
        
    print(f"\n{GREEN}Verification process completed!{NC}\n")
