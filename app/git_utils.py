import os
import shutil
import subprocess
import logging

logger = logging.getLogger("git-utils")

def run_git_cmd(repo_path, args):
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: git {' '.join(args)}: {e.stderr}")
        raise RuntimeError(e.stderr)

def init_git_repo(repo_path, templates_path):
    if os.path.exists(os.path.join(repo_path, ".git")):
        logger.info(f"Git repository already exists at {repo_path}")
        return False
        
    os.makedirs(repo_path, exist_ok=True)
    
    # Initialize repository
    run_git_cmd(repo_path, ["init"])
    run_git_cmd(repo_path, ["config", "http.receivepack", "true"])
    run_git_cmd(repo_path, ["config", "user.name", "Control Plane Portal"])
    run_git_cmd(repo_path, ["config", "user.email", "controlplane@platform.local"])
    
    # Copy bootstrap structure
    # We will copy argocd definitions and compositions to the GitOps repo so ArgoCD manages them as well
    os.makedirs(os.path.join(repo_path, "argocd", "applicationsets"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "crossplane", "compositions"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "policies"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "environments"), exist_ok=True)
    
    # Let's copy from our main folder
    root_proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    src_appset = os.path.join(root_proj_dir, "argocd", "applicationsets", "tenant-appset.yaml")
    if os.path.exists(src_appset):
        shutil.copy(src_appset, os.path.join(repo_path, "argocd", "applicationsets", "tenant-appset.yaml"))
        
    src_comp1 = os.path.join(root_proj_dir, "crossplane", "compositions", "rds-database.yaml")
    if os.path.exists(src_comp1):
        shutil.copy(src_comp1, os.path.join(repo_path, "crossplane", "compositions", "rds-database.yaml"))

    src_comp2 = os.path.join(root_proj_dir, "crossplane", "compositions", "db-network-s3.yaml")
    if os.path.exists(src_comp2):
        shutil.copy(src_comp2, os.path.join(repo_path, "crossplane", "compositions", "db-network-s3.yaml"))
        
    src_policy = os.path.join(root_proj_dir, "policies", "limit-tenant-resources.yaml")
    if os.path.exists(src_policy):
        shutil.copy(src_policy, os.path.join(repo_path, "policies", "limit-tenant-resources.yaml"))
        
    # Commit initial structure
    run_git_cmd(repo_path, ["add", "."])
    run_git_cmd(repo_path, ["commit", "-m", "Initial platform control plane bootstrap"])
    logger.info("Initialized GitOps repository successfully")
    return True

def get_git_log(repo_path):
    try:
        output = run_git_cmd(repo_path, ["log", "--pretty=format:%h|%an|%ar|%s", "-n", "15"])
        commits = []
        if not output.strip():
            return commits
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "age": parts[2],
                    "message": parts[3]
                })
        return commits
    except Exception as e:
        logger.error(f"Failed to get git log: {e}")
        return []

def provision_tenant_git(repo_path, templates_path, tenant_name, db_storage, db_class, db_version):
    root_proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tenant_dir = os.path.join(repo_path, "environments", tenant_name)
    os.makedirs(tenant_dir, exist_ok=True)
    
    # Render and copy Network Policy
    src_netpol = os.path.join(root_proj_dir, "vcluster", "network-policies.yaml")
    dest_netpol = os.path.join(tenant_dir, "network-policies.yaml")
    if os.path.exists(src_netpol):
        with open(src_netpol, "r") as f:
            content = f.read()
        content = content.replace("tenant-namespace", tenant_name)
        with open(dest_netpol, "w") as f:
            f.write(content)
            
    # Render and copy DB Claim
    src_claim = os.path.join(root_proj_dir, "crossplane", "definitions", "db_claim.yaml")
    dest_claim = os.path.join(tenant_dir, "db-claim.yaml")
    if os.path.exists(src_claim):
        with open(src_claim, "r") as f:
            content = f.read()
        content = content.replace("tenant-db", f"{tenant_name}-db")
        content = content.replace("tenant-namespace", tenant_name)
        content = content.replace("storageGB: 20", f"storageGB: {db_storage}")
        content = content.replace("instanceClass: db.t4g.micro", f"instanceClass: {db_class}")
        content = content.replace('engineVersion: "14.5"', f'engineVersion: "{db_version}"')
        with open(dest_claim, "w") as f:
            f.write(content)
            
    # Render and copy vcluster Deployment
    src_vcluster = os.path.join(templates_path, "vcluster-template.yaml")
    dest_vcluster = os.path.join(tenant_dir, "vcluster.yaml")
    if os.path.exists(src_vcluster):
        with open(src_vcluster, "r") as f:
            content = f.read()
        content = content.replace("tenant-namespace", tenant_name)
        with open(dest_vcluster, "w") as f:
            f.write(content)
            
    run_git_cmd(repo_path, ["add", "."])
    # Check if there are changes to commit
    status = run_git_cmd(repo_path, ["status", "--porcelain"])
    if not status.strip():
        logger.info(f"No changes to commit for tenant {tenant_name} (already provisioned)")
        return f"Tenant {tenant_name} already up-to-date"
        
    message = f"Provision tenant {tenant_name} (DB: {db_storage}GB, {db_class})"
    run_git_cmd(repo_path, ["commit", "-m", message])
    return message

def delete_tenant_git(repo_path, tenant_name):
    tenant_dir = os.path.join(repo_path, "environments", tenant_name)
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir)
        
    # We must run git rm or add to register deletions
    run_git_cmd(repo_path, ["add", "-A"])
    # Check if there are changes to commit
    status = run_git_cmd(repo_path, ["status", "--porcelain"])
    if not status.strip():
        logger.info(f"No changes to commit for tenant deletion {tenant_name} (already deprovisioned)")
        return f"Tenant {tenant_name} already deprovisioned"
        
    message = f"Deprovision tenant {tenant_name}"
    run_git_cmd(repo_path, ["commit", "-m", message])
    return message
