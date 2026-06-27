import logging
from kubernetes import client, config
from config import settings

logger = logging.getLogger(__name__)

class K8sGPTService:
    def __init__(self):
        try:
            # Try in-cluster config first (when running in EKS)
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes config")
        except Exception:
            # Fall back to local kubeconfig (for testing)
            config.load_kube_config()
            logger.info("Using local Kubernetes config")
        
        self.custom_api = client.CustomObjectsApi()
        self.apps_api = client.AppsV1Api()
        self.core_api = client.CoreV1Api()

    def get_results(self) -> list:
        """
        Fetch all K8sGPT Result CRDs from the k8sgpt namespace.
        These are the findings we send to Claude for RCA.
        """
        try:
            results = self.custom_api.list_namespaced_custom_object(
                group="core.k8sgpt.ai",
                version="v1alpha1",
                namespace=settings.k8sgpt_namespace,
                plural="results"
            )
            
            findings = []
            for item in results.get("items", []):
                spec = item.get("spec", {})
                errors = spec.get("error", [])
                error_text = errors[0].get("text", "") if errors else ""
                
                finding = {
                    "name": spec.get("name", ""),
                    "kind": spec.get("kind", ""),
                    "error": error_text,
                    "details": spec.get("details", ""),
                    "namespace": item["metadata"].get("namespace", ""),
                    "result_name": item["metadata"].get("name", ""),
                    "lifecycle": item.get("status", {}).get("lifecycle", "")
                }
                findings.append(finding)
            
            logger.info(f"Found {len(findings)} K8sGPT results")
            return findings

        except Exception as e:
            logger.error(f"Failed to fetch K8sGPT results: {e}")
            return []

    def get_new_results(self) -> list:
        """Get only new/active results (not historical)"""
        all_results = self.get_results()
        return [r for r in all_results if r.get("lifecycle") != "historical"]

    def restart_deployment(self, namespace: str, deployment_name: str) -> dict:
        """
        Safely restart a deployment by updating its annotation.
        This triggers a rolling restart without downtime.
        """
        try:
            import datetime
            
            # Get current deployment
            deployment = self.apps_api.read_namespaced_deployment(
                name=deployment_name,
                namespace=namespace
            )
            
            # Add restart annotation - this triggers rolling restart
            if deployment.spec.template.metadata.annotations is None:
                deployment.spec.template.metadata.annotations = {}
            
            deployment.spec.template.metadata.annotations[
                "kubectl.kubernetes.io/restartedAt"
            ] = datetime.datetime.utcnow().isoformat()
            
            # Apply the update
            self.apps_api.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=deployment
            )
            
            logger.info(f"Restarted deployment {deployment_name} in {namespace}")
            return {
                "status": "success",
                "action": "restart_deployment",
                "deployment": deployment_name,
                "namespace": namespace
            }
            
        except Exception as e:
            logger.error(f"Failed to restart deployment {deployment_name}: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }

    def rollback_deployment(self, namespace: str, deployment_name: str) -> dict:
        """
        Rollback a deployment to its previous revision.
        """
        try:
            import subprocess
            result = subprocess.run(
                ["kubectl", "rollout", "undo", 
                 f"deployment/{deployment_name}", 
                 "-n", namespace],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"Rolled back deployment {deployment_name}")
                return {
                    "status": "success",
                    "action": "rollback_deployment",
                    "deployment": deployment_name,
                    "namespace": namespace,
                    "output": result.stdout
                }
            else:
                return {
                    "status": "failed",
                    "error": result.stderr
                }
                
        except Exception as e:
            logger.error(f"Failed to rollback deployment {deployment_name}: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }

# Singleton instance
k8sgpt_service = K8sGPTService()
