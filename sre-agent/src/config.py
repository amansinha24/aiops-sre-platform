from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # AWS Settings
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    
    # Kubernetes Settings
    k8sgpt_namespace: str = "k8sgpt"
    target_namespace: str = "application"
    
    # SRE Agent Settings
    confidence_threshold: int = 70
    auto_remediation_enabled: bool = True
    
    # Safe actions list - ONLY these actions can be auto-executed
    safe_actions: list = ["restart_deployment", "rollback_deployment"]
    
    class Config:
        env_file = ".env"

settings = Settings()
