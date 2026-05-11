<p align="center">
  <img src="icon.png" alt="AGCL - Agentic CLI" width="220" />
</p>

<h1 align="center">AGCL — Agentic CLI</h1>

<p align="center">
  <em>Local-first agent runtime + recursive cognition layer + orchestrator for self-hosted endpoints.</em>
</p>

<p align="center">
  <a href="https://github.com/Agentra-Labs/AGCL"><img alt="GitHub" src="https://img.shields.io/badge/github-Agentra--Labs%2FAGCL-181717?logo=github&logoColor=white"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="https://fastapi.tiangolo.com/"><img alt="FastAPI" src="https://img.shields.io/badge/fastapi-0.111%2B-009688?logo=fastapi&logoColor=white"></a>
  <a href="https://github.com/astral-sh/uv"><img alt="uv" src="https://img.shields.io/badge/uv-supported-DE5FE9"></a>
  <a href="docs/web-console.md"><img alt="Web Console" src="https://img.shields.io/badge/web%20console-%2Fweb__socket%2F-blue"></a>
  <a href="docs/dashboard.md"><img alt="Dashboard" src="https://img.shields.io/badge/dashboard-%2Fnode%2Fdashboard-blue"></a>
  <a href="https://github.com/Agentra-Labs/AGCL/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-see%20repo-blue"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey">
  <img alt="Interface" src="https://img.shields.io/badge/interface-Web%20%7C%20TUI%20%7C%20CLI%20%7C%20HTTP%2BSSE-purple">
</p>

---

AGCL is a local-first agent runtime. A small llama.cpp model starts the
response in ~50–300 ms; the cloud model (Claude / OpenAI / any
LiteLLM-routed provider) continues from the same open assistant turn
instead of restarting it. On top of that sits a recursive multi-agent
runtime that passes latent embeddings between HuggingFace / GGUF
agents and trains itself online from a one-shot cloud-teacher answer.
Idle behavior: sessions flush to disk, the local model unloads,
everything reloads on the next message.

There are **three ways to use it**: a [browser console](#web-console),
a [terminal app](#tui), and a [headless CLI](#cli--orchestrator). They
all drive the same engine.

---

## What's in the box

| Component | What it does | Source |
|---|---|---|
| **Web Console** | Static HTML/CSS/JS UI in [`/web_socket/`](web_socket/), hostable on GitHub Pages — drives every endpoint | [docs/web-console.md](docs/web-console.md) |
| **TUI shell** | `python main.py` — long-running terminal app, arrow-key menus, slash commands | [agcl/tui.py](agcl/tui.py) |
| **Node server** | `python main.py node` — HTTP + SSE API, bearer auth, full `/node/*` surface | [agcl/node.py](agcl/node.py) |
| **In-server dashboard** | Lightweight token-usage + quota page at `/node/dashboard` | [agcl/dashboard.py](agcl/dashboard.py) |
| **Recursive MAS** | InnerLink / OuterLink projections, online cloud-teacher training | [agcl/recursive/](agcl/recursive/) |
| **Mini-trainer** | Optional toggleable transformer / MLP that trains from captured latents | [agcl/mini/](agcl/mini/) |
| **Toolkit** | LiteLLM / Ollama / vLLM / TGI / Redis / S3 / Cloudflare / WebRTC / Docker / K8s / GCP adapters | [agcl/toolkit/](agcl/toolkit/) |
| **Headless runner** | `agcl run --output jsonl` — Multica / Cloud Run / Actions contract | [agcl/runner.py](agcl/runner.py) |
| **Orchestrator** | `agcl orchestrate <playbook>` — drive remote AGCL nodes via playbook | [agcl/orchestrator.py](agcl/orchestrator.py) |
| **Plugin system** | Drop a `.py` in `plugins/`, get HTTP routes + CLI commands + tools | [agcl/plugins.py](agcl/plugins.py) |
| **Usage tracker + quotas** | Per-provider token + cost recording, hard caps, custom OpenAI-compatible providers | [agcl/usage.py](agcl/usage.py) |
| **MCP / Slack / Discord / OpenAgents / OpenAPI** | Adapters around the same tool registry | [agcl/integrations/](agcl/integrations/) |
| **npm client** (`@agcl/client`) | TypeScript bindings for every node + toolkit route | [clients/npm/](clients/npm/) |

---

## Install

Pick `pip` or [`uv`](https://github.com/astral-sh/uv) — `uv` resolves +
installs ~10× faster.

```bash
git clone https://github.com/Agentra-Labs/AGCL.git
cd AGCL

# uv (recommended)
uv sync --extra dev
uv run agcl --version       # -> 0.1.0
uv run pytest

# or plain pip
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
agcl --version
```

`uv run agcl …` and `python main.py …` are equivalent — same dispatcher.
For details (GPU acceleration, optional extras, autoconfig for the
RecursiveMAS), see [docs/guide.md](docs/guide.md).

### Setup

API keys live in a gitignored `.env` at the project root:

```bash
cp .env.example .env
# edit .env and add OPENAI_API_KEY or ANTHROPIC_API_KEY
export LOCAL_MODEL_PATH=models/your-model.gguf
```

Every config knob is documented in [docs/configuration.md](docs/configuration.md).

---

## Three ways to use AGCL

### Web Console

A static HTML/CSS/JS UI that lives in [`/web_socket/`](web_socket/) and
talks to your local node over HTTP+SSE. Hostable on GitHub Pages or any
static file host — your bearer key never leaves the browser.

```bash
# 1. start the node on your machine
python main.py node --port 9876 --cors "*"

# 2. host the console (any of these)
python -m http.server 8088 --directory web_socket    # local
# or just open web_socket/index.html
# or push to GitHub Pages -> https://<user>.github.io/<repo>/web_socket/

# 3. open it, paste host + bearer key, click Connect
```

Tabs: Overview · Chat · Recursive MAS · Topics · Mini-Trainer · Usage
& Cost · Configuration · Toolkit · Plugins · Diagnostics · Deploy
Manifests. Full guide in [docs/web-console.md](docs/web-console.md).

### TUI

The default `python main.py` opens a terminal app with arrow-key menus
and slash commands. Same engine as everything else, no HTTP needed.

```bash
python main.py
python main.py --session work          # named, persists across restarts
python main.py --provider openai       # force a specific cloud provider
python main.py --recovery humor        # recovery style when local prefix is off
```

Beginner walkthrough in [docs/guide.md](docs/guide.md).

### CLI / Orchestrator

Drive AGCL from any other tool. Same engine, different output shape.

```bash
# one task, structured JSONL events on stdout
agcl run --task "summarize the latest commit" --output jsonl --session-id job-42

# fan a playbook across remote AGCL nodes
agcl orchestrate playbook.json

# share a config (export-import never crashes; missing pieces become hints)
agcl config export agcl-config.json
agcl config hint agcl-config.json
agcl config import agcl-config.json --apply

# emit deployment artifacts (Docker, K8s, GCP Cloud Run)
agcl toolkit emit-docker --gpu
agcl toolkit emit-k8s
agcl toolkit emit-gcp

# MCP server (stdio / SSE / FastMCP)
agcl mcp                       # stdio (default)
agcl mcp --fast                # FastMCP
agcl mcp --transport sse --port 8765
```

Headless contract / Multica wiring → [docs/integrations/headless-run.md](docs/integrations/headless-run.md).
MCP details → [docs/integrations/mcp.md](docs/integrations/mcp.md).

---

## Documentation

| If you want to… | Read |
|---|---|
| Set up the project for the first time | **[docs/guide.md](docs/guide.md)** |
| Use the bundled web console (`/web_socket/`) | **[docs/web-console.md](docs/web-console.md)** |
| Build your own GUI on top of the node API | **[docs/gui.md](docs/gui.md)** + [docs/gui/endpoints.md](docs/gui/endpoints.md) |
| Understand every config knob | **[docs/configuration.md](docs/configuration.md)** |
| Set up the recursive multi-agent feature | **[docs/recursive.md](docs/recursive.md)** + [recursive/advanced.md](docs/recursive/advanced.md) |
| Understand auto-training (`[stage A] / [stage B]` lines) | **[docs/recursive/training.md](docs/recursive/training.md)** |
| Deploy AGCL anywhere (Docker, K8s, GCP, Cloudflare, Discord, vLLM, Ollama, Redis, S3, …) — step-by-step recipes | **[docs/deploy.md](docs/deploy.md)** |
| Run AGCL in Docker, K8s, GCP Cloud Run, or behind LiteLLM | **[docs/integrations/cloud.md](docs/integrations/cloud.md)** |
| Plug AGCL into MCP / Slack / Discord / OpenAgents / Multica | **[docs/integrations.md](docs/integrations.md)** |
| Use AGCL from JavaScript / TypeScript | **[docs/integrations/npm.md](docs/integrations/npm.md)** |
| Add your own routes / commands / tools via plugins | **[docs/plugins.md](docs/plugins.md)** |
| Set per-provider quotas, register custom APIs, watch usage | **[docs/dashboard.md](docs/dashboard.md)** |
| Toggle / configure the optional mini-model trainer | **[docs/minimodel.md](docs/minimodel.md)** |
| Diagnose a problem | **[docs/troubleshooting.md](docs/troubleshooting.md)** |
| Understand auth, threat model, and deployment hardening | **[docs/security.md](docs/security.md)** |
| See what every code file does | **[docs/code-map.md](docs/code-map.md)** |
| Read the full technical spec | **[docs/SPEC.md](docs/SPEC.md)** |

---

## License

See [LICENSE](LICENSE).
