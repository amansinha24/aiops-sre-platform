import boto3
import json
import logging
import re
from config import settings
from utils.prompts import build_rca_prompt
from models.incident import RCAResult

logger = logging.getLogger(__name__)

class BedrockService:
    def __init__(self):
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region
        )
        self.model_id = settings.bedrock_model_id
        logger.info(f"Bedrock service initialized with model: {self.model_id}")

    def _clean_json_string(self, text: str) -> str:
        """Clean and extract JSON from Claude response"""
        # Find JSON block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON found in response")
        
        json_str = text[start:end]
        
        # Remove control characters that break JSON parsing
        json_str = re.sub(r'[\x00-\x1f\x7f]', ' ', json_str)
        
        # Fix common Claude JSON issues
        json_str = json_str.replace('\n', ' ')
        json_str = json_str.replace('\r', ' ')
        json_str = json_str.replace('\t', ' ')
        
        return json_str

    def analyze_finding(self, finding: dict) -> RCAResult:
        """Send K8sGPT finding to Claude for RCA"""
        try:
            prompt = build_rca_prompt(finding)
            logger.info(f"Sending finding to Claude: {finding.get('name')}")

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

            response_body = json.loads(response["body"].read())
            content = response_body["content"][0]["text"]
            logger.info(f"Claude response: {content[:200]}...")

            # Clean and parse JSON
            json_str = self._clean_json_string(content)
            rca_data = json.loads(json_str)

            return RCAResult(
                root_cause=rca_data.get("root_cause", "Unknown"),
                confidence=int(rca_data.get("confidence", 0)),
                recommended_action=rca_data.get("recommended_action", "Manual investigation required"),
                action_type=rca_data.get("action_type", "manual_intervention"),
                explanation=rca_data.get("explanation", ""),
                safe_to_automate=bool(rca_data.get("safe_to_automate", False))
            )

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Raw content was: {content[:500] if 'content' in locals() else 'N/A'}")
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
                            "content": "Say exactly: Bedrock connected"
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
            return {
                "status": "failed",
                "error": str(e)
            }

bedrock_service = BedrockService()
