import os
import sys
import logging
import subprocess
from flask import Flask, jsonify, request, send_from_directory, Response

from git_utils import init_git_repo, get_git_log, provision_tenant_git, delete_tenant_git
from k8s_utils import (
    get_pods_in_namespaces,
    get_vclusters,
    get_crossplane_claims,
    get_crossplane_rds_instances,
    get_kyverno_policies,
    get_vault_secrets,
    get_argocd_apps,
    trigger_argocd_sync
)

# ==============================================================================
# Logging Configuration
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("control-plane-portal")

# ==============================================================================
# Environment Configuration & Path Init
# ==============================================================================
REPO_PATH = os.getenv("REPO_PATH", "/app/platform-repo.git")
TEMPLATES_PATH = os.getenv("TEMPLATES_PATH", "/app/gitops-templates")

# Initialize Flask App
app = Flask(__name__, static_folder='static', static_url_path='')

# Diagnostic Logging for Paths
logger.info(f"CWD: {os.getcwd()}")
logger.info(f"__file__: {__file__}")
logger.info(f"Abs path of __file__: {os.path.abspath(__file__)}")
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logger.info(f"Calculated Root Dir: {root_dir}")
logger.info(f"Files in root_dir: {os.listdir(root_dir) if os.path.exists(root_dir) else 'Root dir missing'}")
logger.info(f"Checking src_appset: {os.path.exists(os.path.join(root_dir, 'argocd', 'applicationsets', 'tenant-appset.yaml'))}")

# Initialize local GitOps repo on startup
logger.info(f"Initializing GitOps repository path: {REPO_PATH} with templates: {TEMPLATES_PATH}")
init_git_repo(REPO_PATH, TEMPLATES_PATH)

# ==============================================================================
# Git HTTP Server CGI Endpoint
# ==============================================================================
@app.route('/git/<path:req_path>', methods=['GET', 'POST'])
def git_backend(req_path):
    project_root = os.path.dirname(REPO_PATH)
    path_info = f"/{req_path}"
    
    env = os.environ.copy()
    env["GIT_PROJECT_ROOT"] = project_root
    env["GIT_HTTP_EXPORT_ALL"] = "1"
    env["PATH_INFO"] = path_info
    env["REQUEST_METHOD"] = request.method
    env["QUERY_STRING"] = request.query_string.decode('utf-8')
    env["CONTENT_TYPE"] = request.headers.get("Content-Type", "")
    
    req_data = request.get_data()
    
    try:
        proc = subprocess.run(
            ["git", "http-backend"],
            input=req_data,
            env=env,
            capture_output=True
        )
        
        response_data = proc.stdout
        header_part, _, body_part = response_data.partition(b'\r\n\r\n')
        if not body_part:
            header_part, _, body_part = response_data.partition(b'\n\n')
            
        headers = {}
        for line in header_part.decode('utf-8', errors='ignore').split('\r\n'):
            if not line:
                continue
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip()] = v.strip()
                
        status_code = 200
        if 'Status' in headers:
            status_code = int(headers['Status'].split()[0])
            del headers['Status']
            
        res = Response(body_part, status=status_code)
        for k, v in headers.items():
            res.headers[k] = v
        return res
    except Exception as e:
        logger.error(f"Git backend error: {e}")
        return Response(f"Internal Git server error: {e}", status=500)

# ==============================================================================
# UI Routes
# ==============================================================================
@app.route("/")
def index():
    return send_from_directory('static', 'index.html')

@app.route("/health")
def health():
    return jsonify({
        "status": "UP",
        "gitops_repo": REPO_PATH
    })

# ==============================================================================
# REST API Endpoints
# ==============================================================================

@app.route("/api/v1/git/log", methods=["GET"])
def git_log():
    commits = get_git_log(REPO_PATH)
    return jsonify(commits)

@app.route("/api/v1/k8s/status", methods=["GET"])
def k8s_status():
    # Discover active tenants dynamically from Git repository folders
    tenants = []
    envs_dir = os.path.join(REPO_PATH, "environments")
    if os.path.exists(envs_dir):
        try:
            tenants = [d for d in os.listdir(envs_dir) if os.path.isdir(os.path.join(envs_dir, d)) and d.startswith("tenant-")]
        except Exception as e:
            logger.warning(f"Failed to read environments folder: {e}")
            
    # System namespaces to query pods for
    system_namespaces = ["control-plane-system", "argocd", "crossplane-system", "kyverno", "localstack", "vault"]
    
    # Query all pods
    all_namespaces = system_namespaces + tenants
    pods = get_pods_in_namespaces(all_namespaces)
    
    # Query other resources
    vclusters = get_vclusters()
    claims = get_crossplane_claims()
    rds_instances = get_crossplane_rds_instances()
    policies = get_kyverno_policies()
    secrets = get_vault_secrets(tenants + ["crossplane-system"])
    argocd_apps = get_argocd_apps()
    
    return jsonify({
        "tenants": tenants,
        "pods": pods,
        "vclusters": vclusters,
        "claims": claims,
        "rds_instances": rds_instances,
        "policies": policies,
        "secrets": secrets,
        "argocd_apps": argocd_apps
    })

@app.route("/api/v1/provision", methods=["POST"])
def provision_tenant():
    data = request.get_json() or {}
    tenant_name = data.get("tenant_name")
    db_storage = data.get("db_storage", 20)
    db_class = data.get("db_class", "db.t4g.micro")
    db_version = data.get("db_version", "14.5")
    
    if not tenant_name:
        return jsonify({"success": False, "message": "Missing tenant_name parameter"}), 400
        
    tenant_name = tenant_name.lower().strip()
    if not tenant_name.startswith("tenant-"):
        tenant_name = f"tenant-{tenant_name}"
        
    try:
        msg = provision_tenant_git(REPO_PATH, TEMPLATES_PATH, tenant_name, db_storage, db_class, db_version)
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        logger.error(f"Tenant provisioning failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/v1/deprovision", methods=["POST"])
def deprovision_tenant():
    data = request.get_json() or {}
    tenant_name = data.get("tenant_name")
    
    if not tenant_name:
        return jsonify({"success": False, "message": "Missing tenant_name parameter"}), 400
        
    try:
        msg = delete_tenant_git(REPO_PATH, tenant_name)
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        logger.error(f"Tenant deletion failed: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/v1/sync", methods=["POST"])
def sync():
    data = request.get_json() or {}
    app_name = data.get("app_name")
    
    if not app_name:
        return jsonify({"success": False, "message": "Missing app_name"}), 400
        
    success, msg = trigger_argocd_sync(app_name)
    if success:
        return jsonify({"success": True, "message": msg})
    else:
        return jsonify({"success": False, "message": msg}), 500

# ==============================================================================
# Application Startup
# ==============================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5006))
    logger.info(f"Starting GitOps Platform Control Plane Portal on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
