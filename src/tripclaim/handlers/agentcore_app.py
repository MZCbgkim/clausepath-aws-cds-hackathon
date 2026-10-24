"""Amazon Bedrock AgentCore Runtime entrypoint: runs one agent turn for a claim.

The inbound Lambda calls this runtime when AGENT_RUNTIME_ARN is set; tools act on the same DynamoDB/S3 data.
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ..agent import StrandsAgent
from ..app import build

app = BedrockAgentCoreApp()
_service = None


@app.entrypoint
def invoke(payload):
    global _service
    if _service is None:
        _service = build()
    agent = StrandsAgent(_service.s.model_id, _service.s.region)
    return {"reply": agent.run(_service, payload["claim_id"], payload["text"], payload["context"])}


if __name__ == "__main__":
    app.run()
