import boto3
import json
import logging
from config import settings
from utils.prompts import build_rca_prompt
from models.incident import RCAResult

logger = logging.getLogger(__name__)

class BedrockService:
    def __init__(self):
        # boto3 automatically uses IRSA credentials when running in EKS
        # No hardcoded credentials needed
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region
        )
        self.model_id = settings.bedrock_model_id
        logger.info(f"Bedrock service initialized with model: {self.model_id}")

    def analyze_finding(self, finding: dict) -> RCAResult:
        """
        Send K8sGPT finding to Claude for RCA.
        Returns structured RCAResult.
        """
        try:
            # Build the SRE prompt
            prompt = build_rca_prompt(finding)
            logger.info(f"Sending finding to Claude: {finding.get('name')}")

            # Call Bedrock
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1000,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                })
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            content = response_body["content"][0]["text"]
            
            logger.info(f"Claude response received: {content[:100]}...")

            # Parse JSON from Claude response
            # Claude sometimes adds text before/after JSON
            start = content.find("{")
            end = content.rfind("}") + 1
            json_str = content[start:end]
            rca_data = json.loads(json_str)

            return RCAResult(
                root_cause=rca_data.get("root_cause", "Unknown"),
                confidence=rca_data.get("confidence", 0),
                recommended_action=rca_data.get("recommended_action", "Manual investigation required"),
                action_type=rca_data.get("action_type", "manual_intervention"),
                explanation=rca_data.get("explanation", ""),
                safe_to_automate=rca_data.get("safe_to_automate", False)
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            return RCAResult(
                root_cause="Failed to parse AI response",
                confidence=0,
                recommended_action="Manual investigation required",
                action_type="manual_intervention",
                explanation=str(e),
                safe_to_automate=False
            )
        except Exception as e:
            logger.error(f"Bedrock call failed: {e}")
            raise

    def test_connection(self) -> dict:
        """Test Bedrock connectivity"""
        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 50,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Say 'Bedrock connected' in exactly those words."
                        }
                    ]
                })
            )
            response_body = json.loads(response["body"].read())
            content = response_body["content"][0]["text"]
            return {
                "status": "connected",
                "model": self.model_id,
                "response": content
            }
        except Exception as e:
            logger.error(f"Bedrock connection test failed: {e}")
            return {
                "status": "failed",
                "error": str(e)
            }

# Singleton instance
bedrock_service = BedrockService()
