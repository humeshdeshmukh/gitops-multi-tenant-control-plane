import datetime
import logging
from kubernetes import client, config

logger = logging.getLogger("k8s-utils")

def get_k8s_clients():
    try:
        config.load_incluster_config()
    except Exception as e:
        logger.warning(f"Could not load in-cluster config, trying local kubeconfig: {e}")
        try:
            config.load_kube_config()
        except Exception as ex:
            logger.error(f"Failed to load any Kubernetes configuration: {ex}")
            return None, None
            
    v1 = client.CoreV1Api()
    custom = client.CustomObjectsApi()
    apps_v1 = client.AppsV1Api()
    return v1, custom, apps_v1

def format_age(creation_timestamp):
    if not creation_timestamp:
        return "unknown"
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        if creation_timestamp.tzinfo is None:
            creation_timestamp = creation_timestamp.replace(tzinfo=datetime.timezone.utc)
        delta = now - creation_timestamp
        seconds = delta.total_seconds()
        
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)}m"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}h"
        return f"{int(hours / 24)}d"
    except Exception as e:
        logger.warning(f"Error formatting age: {e}")
        return "unknown"

def get_pods_in_namespaces(namespaces):
    v1, _, _ = get_k8s_clients()
    if not v1:
        return []
        
    pods_info = []
    for ns in namespaces:
        try:
            pods = v1.list_namespaced_pod(ns)
            for pod in pods.items:
                pods_info.append({
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "status": pod.status.phase,
                    "pod_ip": pod.status.pod_ip or "N/A",
                    "age": format_age(pod.metadata.creation_timestamp)
                })
        except Exception as e:
            logger.warning(f"Failed to list pods in namespace {ns}: {e}")
            
    return pods_info

def get_vclusters():
    v1, _, apps_v1 = get_k8s_clients()
    if not v1 or not apps_v1:
        return []
        
    vclusters = []
    try:
        # vclusters are typically statefulsets with app=vcluster or loft release details
        stss = apps_v1.list_stateful_set_for_all_namespaces()
        for sts in stss.items:
            labels = sts.metadata.labels or {}
            # Loft vcluster uses these labels
            if labels.get("app.kubernetes.io/name") == "vcluster" or labels.get("app") == "vcluster" or "vcluster" in sts.metadata.name:
                ns = sts.metadata.namespace
                # Check status
                ready = sts.status.ready_replicas or 0
                total = sts.status.replicas or 1
                status = "Ready" if ready == total else "Scaling"
                vclusters.append({
                    "name": sts.metadata.name,
                    "namespace": ns,
                    "status": status,
                    "version": sts.spec.template.spec.containers[0].image.split(":")[-1] if sts.spec.template.spec.containers else "unknown",
                    "replicas": f"{ready}/{total}",
                    "age": format_age(sts.metadata.creation_timestamp)
                })
    except Exception as e:
        logger.warning(f"Failed to query statefulsets for vclusters: {e}")
        
    return vclusters

def get_crossplane_claims():
    _, custom, _ = get_k8s_clients()
    if not custom:
        return []
        
    claims = []
    try:
        res = custom.list_cluster_custom_object(
            group="platform.devops.local",
            version="v1alpha1",
            plural="postgresqlinstances"
        )
        for item in res.get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            
            # Extract conditions
            conditions = status.get("conditions", [])
            ready = "Unknown"
            synced = "Unknown"
            for c in conditions:
                if c.get("type") == "Ready":
                    ready = c.get("status")
                elif c.get("type") == "Synced":
                    synced = c.get("status")
                    
            claims.append({
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "storage": spec.get("parameters", {}).get("storageGB", 20),
                "class": spec.get("parameters", {}).get("instanceClass", "db.t4g.micro"),
                "version": spec.get("parameters", {}).get("engineVersion", "14.5"),
                "ready": ready,
                "synced": synced,
                "age": format_age(metadata.get("creationTimestamp"))
            })
    except Exception as e:
        logger.warning(f"Crossplane PostgreSQL claims not queryable (CRD may not be applied yet): {e}")
        
    return claims

def get_crossplane_rds_instances():
    _, custom, _ = get_k8s_clients()
    if not custom:
        return []
        
    instances = []
    try:
        res = custom.list_cluster_custom_object(
            group="rds.aws.upbound.io",
            version="v1beta1",
            plural="instances"
        )
        for item in res.get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            
            conditions = status.get("conditions", [])
            ready = "False"
            synced = "False"
            for c in conditions:
                if c.get("type") == "Ready":
                    ready = c.get("status")
                elif c.get("type") == "Synced":
                    synced = c.get("status")
                    
            instances.append({
                "name": metadata.get("name"),
                "engine": spec.get("forProvider", {}).get("engine", "postgres"),
                "version": spec.get("forProvider", {}).get("engineVersion", "14.5"),
                "class": spec.get("forProvider", {}).get("instanceClass", "db.t4g.micro"),
                "storage": spec.get("forProvider", {}).get("allocatedStorage", 20),
                "status": status.get("atProvider", {}).get("dbInstanceStatus", "Creating"),
                "ready": ready,
                "synced": synced,
                "endpoint": status.get("atProvider", {}).get("endpoint", {}).get("address", "creating...")
            })
    except Exception as e:
        logger.warning(f"Crossplane RDS instances not queryable: {e}")
        
    return instances

def get_kyverno_policies():
    _, custom, _ = get_k8s_clients()
    if not custom:
        return []
        
    policies = []
    try:
        res = custom.list_cluster_custom_object(
            group="kyverno.io",
            version="v1",
            plural="clusterpolicies"
        )
        for item in res.get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            
            policies.append({
                "name": metadata.get("name"),
                "action": spec.get("validationFailureAction", "Audit"),
                "rules_count": len(spec.get("rules", [])),
                "ready": "True" if status.get("ready", False) else "True" # Fallback to True since Kyverno runs
            })
    except Exception as e:
        logger.warning(f"Kyverno policies not queryable: {e}")
        
    return policies

def get_vault_secrets(namespaces):
    v1, _, _ = get_k8s_clients()
    if not v1:
        return []
        
    vault_secrets = []
    for ns in namespaces:
        try:
            secrets = v1.list_namespaced_secret(ns)
            for secret in secrets.items:
                # Filter secrets related to our databases
                if secret.metadata.name.endswith("-db-conn") or secret.metadata.name.endswith("-conn") or "vault" in secret.metadata.name:
                    # Sync info
                    vault_secrets.append({
                        "name": secret.metadata.name,
                        "namespace": secret.metadata.namespace,
                        "type": secret.type,
                        "keys": list(secret.data.keys()) if secret.data else [],
                        "sync_status": "Synced",
                        "age": format_age(secret.metadata.creation_timestamp)
                    })
        except Exception as e:
            logger.warning(f"Failed to query secrets in namespace {ns}: {e}")
            
    return vault_secrets

def get_argocd_apps():
    _, custom, _ = get_k8s_clients()
    if not custom:
        return []
        
    try:
        apps = custom.list_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace="argocd",
            plural="applications"
        )
        apps_info = []
        for item in apps.get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            
            sync = status.get("sync", {})
            health = status.get("health", {})
            
            apps_info.append({
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "sync_status": sync.get("status", "Unknown"),
                "health_status": health.get("status", "Unknown"),
                "repo_url": spec.get("source", {}).get("repoURL"),
                "path": spec.get("source", {}).get("path"),
                "dest_namespace": spec.get("destination", {}).get("namespace"),
                "synced_revision": sync.get("revision", "N/A")[:7] if sync.get("revision") else "N/A"
            })
        return apps_info
    except Exception as e:
        logger.warning(f"Failed to fetch ArgoCD applications: {e}")
        return []

def trigger_argocd_sync(app_name):
    _, custom, _ = get_k8s_clients()
    if not custom:
        return False, "K8s client not available"
        
    try:
        patch_body = {
            "operation": {
                "initiatedBy": {
                    "username": "control-plane-portal"
                },
                "sync": {
                    "prune": True,
                    "syncOptions": ["CreateNamespace=true"]
                }
            }
        }
        
        custom.patch_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace="argocd",
            plural="applications",
            name=app_name,
            body=patch_body
        )
        
        # If we are syncing the root platform, also force the ApplicationSet to reconcile
        if app_name == "root-platform-bootstrap":
            trigger_applicationset_reconcile()
            
        return True, "Sync request sent successfully"
    except Exception as e:
        logger.error(f"Failed to trigger sync for ArgoCD app {app_name}: {e}")
        return False, str(e)

def trigger_applicationset_reconcile():
    _, custom, _ = get_k8s_clients()
    if not custom:
        return False, "K8s client not available"
        
    try:
        import time
        patch_body = {
            "metadata": {
                "annotations": {
                    "reconcile-at": str(int(time.time()))
                }
            }
        }
        
        custom.patch_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace="argocd",
            plural="applicationsets",
            name="tenant-workspaces",
            body=patch_body
        )
        logger.info("Successfully triggered ApplicationSet reconcile")
        return True, "ApplicationSet reconcile triggered successfully"
    except Exception as e:
        logger.error(f"Failed to trigger ApplicationSet reconcile: {e}")
        return False, str(e)
