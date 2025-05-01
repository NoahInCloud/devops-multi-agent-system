# Monitoring Agent for Azure Logs

A lightweight Python agent that **pulls recent logs from Azure Monitor (Log Analytics), runs an Azure OpenAI prompt via [Semantic Kernel](https://github.com/microsoft/semantic-kernel) to detect anomalies, and publishes alerts.** Use it as a starting point for automated operational monitoring or plug it into a larger multi‑agent system.

---
## ✨ Features

| Capability | Details |
|------------|---------|
| **Log ingestion** | Asynchronous queries against Log Analytics via the Azure Monitor Query SDK (`LogsQueryClient`). |
| **AI‑powered anomaly detection** | Customisable prompt run through Semantic Kernel and your Azure OpenAI deployment. |
| **Pluggable alerting** | Current implementation prints to console; swap in Service Bus, Event Grid, Slack, etc. |
| **Minimal footprint** | Single file, pure Python ≥ 3.12, no web server required. |

---
## 🖼️ Architecture (high‑level)

```text
┌────────────┐      async Kusto query       ┌────────────────────┐
│ Azure Log  │  ─────────────────────────▶ │  Monitoring Agent  │
│ Analytics  │                              │  (Python)          │
└────────────┘                              ├─── Semantic‑Kernel ─┐
         ▲                                 │  │ Azure OpenAI      │
         │   alert / message (pluggable)   │  └───────────────────┘
         └─────────────────────────────────┘
```

---
## ⚙️ Prerequisites

* **Azure Subscription** with:
  * **Log Analytics workspace**<br>  `AZURE_MONITOR_WORKSPACE_ID = <GUID>`
  * **Azure OpenAI resource & model deployment**<br>  `AZURE_OPENAI_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`
  * Either a **Managed Identity / AAD app registration** _or_ an **API key**<br>  `AZURE_OPENAI_KEY` (omit when using MSI/AAD)
* **Python 3.12+** (tested on 3.12.9)

---
## 🚀 Quick Start (local)

```bash
# 1  Clone & enter repo
 git clone https://github.com/NoahInCloud/devops-multi-agent-system.git
 cd devops-agents

# 2  Create virtual‑env
 python -m venv .venv && source .venv/bin/activate

# 3  Install deps
 pip install -r requirements.txt   # or copy the list below

# 4  Create .env
 cp .env.sample .env                # then edit values

# 5  Run once
 python monitoring_agent.py
```

### `.env` template

```dotenv
# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_KEY=<sk-...>           # leave blank to use AAD/MSI\ nAZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o-monitor

# Log Analytics
AZURE_MONITOR_WORKSPACE_ID=<workspace-guid>
```

> **Note**  If your workspace is new and empty, push a quick test record using the HTTP Data Collector API—see `docs/push-test-log.sh` for a ready‑made script.

---
## 🏃 How it works

1. **Initialise** credentials (Managed Identity → Environment Credential fallback).
2. **Query** the workspace for the last *N* minutes (configurable) using a Kusto query.
3. **Aggregate** rows into a single string and **invoke** the `DetectAnomalies` SK function.
4. **Publish** an alert when the prompt response does **not** contain “no anomalies found”.

You can tune:
* **`QUERY_MINUTES`** – look‑back window.
* **Kusto table & filters** – modify the string inside `_fetch_logs()`.
* **Prompt** – edit the multiline string in `__init__` or load from a file.

---
## 🛠️ Extending / Integrating

| Task | Hint |
|------|------|
| **Continuous execution** | Wrap `run_cycle()` in a `while True` + `asyncio.sleep()`, or deploy as an Azure Function / Container App with a Timer trigger. |
| **Real alert transport** | Swap `publish()` implementation: Service Bus, Event Hubs, Slack webhook, Teams, PagerDuty, … |
| **Multiple prompts / skills** | Register additional SK functions and route logs by category. |
| **Custom memory / RAG** | Attach a vector store through SK’s memory APIs to give the agent historical context. |

---
## 🏗️ Deploying to Azure (Container Apps)

1. `az acr build -t monitoring-agent:latest .`  → push to ACR.
2. `az containerapp create … --image <acr>.azurecr.io/monitoring-agent:latest`  → set
   `--env-vars` to the same values as `.env`.
3. Use a **managed identity** with:
   * _Cognitive Services User_ role on your Azure OpenAI resource.
   * _Log Analytics Reader_ on the workspace.

See `deploy/containerapp.bicep` for an opinionated starter.

---
## 🧪 Testing

```bash
pytest -q tests/
```

Include tests for:
* `_build_string_to_sign()` util (if extracted)
* Prompt invocation (mock SK)
* Kusto query handling (mock SDK response)

---
## 📃 License

MIT © 2025 Noah Okorie.  See [LICENSE](LICENSE) for details.

---
## 🙋‍♂️ Contributing

1. Fork → Branch → PR.
2. Run `pre-commit install` (Black, Ruff, isort).
3. Write tests & keep coverage ≥ 95 %.

We welcome issues, suggestions, and PRs! Feel free to open a discussion ticket before large changes.

---
### Resources

* Azure Monitor HTTP Data Collector API docs  <https://aka.ms/la-dca>
* Semantic‑Kernel Python repo                <https://github.com/microsoft/semantic-kernel>
* Azure Monitor Query SDK for Python        <https://pypi.org/project/azure-monitor-query>

