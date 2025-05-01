"""
Monitoring Agent
────────────────
• Pulls recent logs from Azure Monitor
• Uses Azure OpenAI via Semantic-Kernel to spot anomalies
• Publishes (simulated) alerts
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import List

import pandas as pd
import semantic_kernel as sk
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

# Separate sync/async credentials can be useful
from azure.identity import (
    DefaultAzureCredential as SyncDefaultAzureCredential,
)
from azure.identity.aio import (
    DefaultAzureCredential as AsyncDefaultAzureCredential,
)
from azure.monitor.query.aio import LogsQueryClient
from azure.monitor.query import LogsQueryStatus # Corrected import

from dotenv import load_dotenv

load_dotenv()

# ── ENV ────────────────────────────────────────────────────────────────
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")      # omit if using AAD / MSI
AZURE_AI_MODEL_DEPLOYMENT_NAME = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
AZURE_MONITOR_WORKSPACE_ID = os.getenv("AZURE_MONITOR_WORKSPACE_ID")

# ── LOGGING ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ── Lightweight message object (simulated bus) ─────────────────────────
class AgentMessage:
    def __init__(self, sender_id: str, topic: str, payload: dict):
        self.sender_id = sender_id
        self.topic = topic
        self.payload = payload


# ── Monitoring Agent ───────────────────────────────────────────────────
class MonitoringAgent:
    def __init__(self, agent_id: str = "monitoring_agent") -> None:
        self.agent_id = agent_id
        self.publish_topic = "monitoring.anomalies"
        logger.info("initialising %s …", self.agent_id)

        # 1️⃣ Credentials (Moved up, separate sync/async)
        self.async_cred = AsyncDefaultAzureCredential()
        self.sync_cred = SyncDefaultAzureCredential() # Needed for the ad_token_provider lambda

        # 2️⃣ Azure Monitor client
        self.logs_client = LogsQueryClient(self.async_cred)
        logger.info("Azure Monitor client initialized.")

        # 3️⃣ Kernel + Azure OpenAI chat service
        self.kernel = sk.Kernel()
        try:
            if AZURE_OPENAI_KEY:
                # Use API Key if provided
                chat_service = AzureChatCompletion(
                    deployment_name=AZURE_AI_MODEL_DEPLOYMENT_NAME,
                    endpoint=AZURE_OPENAI_ENDPOINT,
                    api_key=AZURE_OPENAI_KEY,
                )
                logger.info("SK Service: Configured using API Key.")
            elif AZURE_OPENAI_ENDPOINT:
                 # Use AAD/MSI via ad_token_provider lambda if no key but endpoint exists
                 # The lambda provides a callable that returns the token string
                chat_service = AzureChatCompletion(
                    deployment_name=AZURE_AI_MODEL_DEPLOYMENT_NAME,
                    endpoint=AZURE_OPENAI_ENDPOINT,
                    ad_token_provider=lambda scope="https://cognitiveservices.azure.com/.default": self.sync_cred.get_token(scope).token,
                 )
                logger.info("SK Service: Configured using AAD Token Provider.")
            else:
                 logger.error("Azure OpenAI Key or Endpoint not configured in .env file.")
                 raise ValueError("Missing Azure OpenAI Key or Endpoint configuration.")

            self.kernel.add_service(chat_service)
            logger.info("Semantic Kernel service added.")

        except Exception as e:
            logger.error(f"Error adding Semantic Kernel service: {e}", exc_info=True)
            raise

        # 4️⃣ Register prompt-based function
        anomaly_prompt = """
Analyse the following log entries and identify any potential anomalies,
errors (e.g. HTTP 5xx) or unusual patterns (spikes, latency jumps, …).
If nothing stands out, reply with “No anomalies found”.

Log entries:
---
{{$input}}
---

Findings:
"""
        try:
            # Define execution settings as a dictionary or specific settings object
            # Passing as dict might be more flexible across SK versions
            exec_settings = {
                 # service_id might be inferred if only one service is added
                "max_tokens": 300,
                "temperature": 0.2,
                "top_p": 0.5,
            }
            # Or use specific settings class:
            # from semantic_kernel.connectors.ai.open_ai import AzureChatPromptExecutionSettings
            # exec_settings = AzureChatPromptExecutionSettings(max_tokens=300, temperature=0.2, top_p=0.5)

            self.anomaly_detector = self.kernel.add_function(
                 function_name="DetectAnomalies",
                 plugin_name="monitoring", # Good practice plugin name
                 prompt=anomaly_prompt,
                 execution_settings=exec_settings,
            )
            logger.info("Semantic Kernel function 'DetectAnomalies' registered.")
        except Exception as e:
            logger.error(f"Error creating semantic function: {e}", exc_info=True)
            raise

        logger.info("%s initialised successfully.", self.agent_id)

    # ── Fetch logs ──────────────────────────────────────────────────────
    async def _fetch_logs(self, minutes: int = 15) -> List[str]:
        """Fetches logs from Azure Monitor Log Analytics"""
        logger.debug(f"Fetching logs for the last {minutes} minutes...")
        if not AZURE_MONITOR_WORKSPACE_ID:
            logger.error("AZURE_MONITOR_WORKSPACE_ID not set.")
            return []

        # --- Make sure Kusto query targets a table that exists in your workspace ---
        query = f"""
        AppTraces
        | where TimeGenerated > ago({minutes}m)
        | project TimeGenerated, Message
        | order by TimeGenerated desc
        | limit 100
        """
        # --- End Query ---

        try:
            rsp = await self.logs_client.query_workspace(
                AZURE_MONITOR_WORKSPACE_ID, query=query, timespan=None
            )
        except Exception as exc:
            logger.error("Kusto query failed: %s", exc, exc_info=True)
            return []

        def _rows(table):  # helper to format rows
            if not table or not table.rows:
                return []
            try:
                df = pd.DataFrame(table.rows, columns=table.columns)
                # Ensure expected columns exist before trying to access
                if 'TimeGenerated' in df.columns and 'Message' in df.columns:
                     return [f"{r.TimeGenerated} - {r.Message}" for r in df.itertuples()]
                else:
                     logger.warning(f"Expected columns ('TimeGenerated', 'Message') not found in query result table: {table.columns}")
                     # Fallback: return raw rows or try other columns if available
                     return [str(row) for row in table.rows]
            except Exception as e:
                 logger.error(f"Error processing query result table rows: {e}")
                 return []


        if rsp.status == LogsQueryStatus.SUCCESS:
            logger.debug("Log query successful.")
            return _rows(rsp.tables[0]) if rsp.tables else []

        if rsp.status == LogsQueryStatus.PARTIAL:
            logger.warning("Partial success: %s", rsp.partial_error)
            return _rows(rsp.partial_data[0]) if rsp.partial_data else []

        logger.error("Query failure: %s", rsp.error)
        return []

    # ── One monitoring cycle ────────────────────────────────────────────
    async def run_cycle(self) -> None:
        """Runs one monitoring cycle: fetch, analyze, publish."""
        logger.info("[%s] monitoring cycle start", self.agent_id)

        logs = await self._fetch_logs(15)
        if not logs:
            logger.info("no logs to analyse this cycle")
            logger.info("[%s] monitoring cycle end", self.agent_id)
            return # Exit cycle early if no logs

        text = "\n".join(logs)[-4000:]  # truncate for token limits
        logger.info("Analysing %d log entries...", len(logs))

        try:
            result = await self.kernel.invoke(
                self.anomaly_detector, arguments={"input": text}
            )
            findings = str(result).strip()
            logger.info("Analysis result: %s", findings)
        except Exception as e:
             logger.error(f"Semantic Kernel invocation failed: {e}", exc_info=True)
             findings = "Analysis failed" # Set findings to indicate failure


        if "no anomalies found" not in findings.lower() and findings != "Analysis failed":
            payload = {
                "timestamp": datetime.utcnow().isoformat(),
                "severity": "Detected", # Could enhance later
                "description": findings,
                "sample": logs[:5], # Send smaller sample
            }
            await self.publish(
                AgentMessage(self.agent_id, self.publish_topic, payload)
            )
        else:
             logger.info("No actionable anomalies identified or analysis failed.")

        logger.info("[%s] monitoring cycle end", self.agent_id)

    # ── Simulated publish ───────────────────────────────────────────────
    async def publish(self, msg: AgentMessage) -> None:
        """Simulates publishing message to a bus/topic."""
        logger.info("--- ALERT PUBLISHED (Simulated) --------------------")
        logger.info("  topic   : %s", msg.topic)
        logger.info("  sender  : %s", msg.sender_id)
        # Pretty print payload dictionary for better readability
        import json
        logger.info("  payload : %s", json.dumps(msg.payload, indent=2))
        logger.info("----------------------------------------------------")
        await asyncio.sleep(0.05)

    # ── Cleanup ─────────────────────────────────────────────────────────
    async def close(self) -> None:
        """Closes async resources."""
        logger.info("Closing resources for %s...", self.agent_id)
        if hasattr(self, 'logs_client') and self.logs_client:
            await self.logs_client.close()
        if hasattr(self, 'async_cred') and self.async_cred:
            await self.async_cred.close()
        # Sync credential typically doesn't require async close
        logger.info("Resources closed for %s.", self.agent_id)


# ── Main ────────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("Starting Monitoring Agent simulation...")
    agent = None # Initialize agent variable
    try:
        # Instantiate the agent
        agent = MonitoringAgent()
        # Run the monitoring cycle once for testing
        await agent.run_cycle()

    except Exception as e:
        logger.error(f"An error occurred during agent execution: {e}", exc_info=True)
    finally:
        # Ensure resources are cleaned up even if errors occur
        if agent:
            await agent.close()

    logger.info("Monitoring Agent simulation finished.")


if __name__ == "__main__":
    # import nest_asyncio # Optional: uncomment if running in Jupyter/IPython
    # nest_asyncio.apply()
    asyncio.run(main())