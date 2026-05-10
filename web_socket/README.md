# AGCL Console — start-to-finish guide for first-time setup

This is the friendly walkthrough. Follow it top to bottom and you'll
go from a fresh clone to a working chat — using **the web console for
almost everything**. You'll touch the terminal exactly twice: once to
start the local server, and once to paste your cloud API key into a
file. After that, every setting, model download, and feature lives
behind a button in the browser.

**You don't need to read the CLI docs.** The CLI exists; the GUI
reaches the same backend. Pick whichever feels easier — for this guide,
the GUI is the path.

---

## What you're about to set up

```
┌─────────────────────────────────────────────────────────┐
│   index.html  (this web console — runs in your browser) │
└────────────────────┬────────────────────────────────────┘
                     │  HTTP + bearer key
┌────────────────────▼────────────────────────────────────┐
│   AGCL node  (a small Python server on your machine)    │
│   ─ talks to OpenAI / Claude for the cloud reply         │
│   ─ runs a tiny local model for the instant prefix       │
│   ─ stores sessions, configs, downloads, training state  │
└─────────────────────────────────────────────────────────┘
```

You'll start the node once, open the web page once, and from then on
every interaction (download a model, add a custom provider, wire up
chat, run a multi-agent reasoning loop) is a click in the browser.

---

## What you need before you start

| Thing                            | Why                                                  |
|----------------------------------|------------------------------------------------------|
| **Python 3.10 or newer**         | The node server is Python                            |
| **A terminal**                   | You'll type two commands. That's it.                 |
| **An API key** (optional but recommended) | Either Anthropic (Claude) or OpenAI. Without one you can still install everything and run the local model — you just won't get cloud answers. |
| **Internet**                     | First-time install + the optional model downloads    |

Approx **2 GB free disk** is enough for a basic chat setup; **5–10 GB**
if you also want the recursive multi-agent feature with both HF models.

---

## Step 1 — Get the code

In a terminal:

```bash
git clone https://github.com/Agentra-Labs/AGCL.git
cd AGCL
```

That's the project folder. Everything else happens here.

---

## Step 2 — Install once

Pick **one** of these. They both end up at the same place; `uv` is
faster.

**Option A — uv (recommended):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # one-time uv install
uv sync --extra dev
```

**Option B — pip + venv:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Either way, the install pulls FastAPI, llama-cpp-python, and the rest.
Wait for it to finish (a few minutes the first time).

---

## Step 3 — (Optional) Add your cloud API key

This is the **only place** you'll touch a text file. The web console
intentionally never lets you paste a secret API key over HTTP — it's a
deliberate safety choice. So we put it in a file once.

In the project root, copy the example file and edit it:

```bash
cp .env.example .env
```

Now open `.env` in any text editor and uncomment **one** line:

```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
# or:
OPENAI_API_KEY=sk-xxxxxxxxxxxx
```

Save and close. **You never have to touch this file again.** Every
other setting can be changed from the browser.

> Skipping this step is fine for a first look. The chat tab will
> warn you about the missing key but won't crash.

---

## Step 4 — Start the node

This is the long-running server. Leave it running in this terminal
window for as long as you want to use AGCL.

```bash
python main.py node --port 9876 --cors "*"
```

You'll see a banner and one important line near the top:

```
[node] auth key: 7xKqV4...long-string...8mQ3
```

**Copy that key.** You'll paste it into the browser in a moment.

> Forgot to copy? Stop the server (Ctrl-C) and start it again — it
> prints a fresh key. Or pin a stable one with
> `export AGCL_NODE_AUTH=$(openssl rand -hex 32)` before starting.

---

## Step 5 — Open the web console

The console is a static page; you can open it any of these ways. Pick
the easiest:

**Easiest — open the file directly:**

Double-click `index.html` in your file manager, or:

```bash
xdg-open index.html      # Linux
open index.html          # macOS
start index.html         # Windows
```

**Or — serve it locally** (in a *second* terminal, leaving the node
running in the first):

```bash
python -m http.server 8088 --directory .
```

Then visit http://localhost:8088/.

**Or — host on GitHub Pages** for sharing across machines: see "Hosting
on GitHub Pages" at the bottom.

---

## Step 6 — Connect

You'll see a login pane that asks for two things:

| Field        | What to type                                                      |
|--------------|--------------------------------------------------------------------|
| **Host**     | `http://localhost:9876`  (the default — usually correct)           |
| **Bearer key** | The long string you copied from Step 4 (`7xKqV4…`)                |

Click **Connect**.

The pane disappears, the sidebar appears on the left, and you land on
the **Overview** tab. **You're connected.** From this point everything
is a click.

> The key is stored in your browser's `localStorage` for next time.
> Click **Disconnect** in the sidebar to clear it.

---

## Step 7 — Set up the local model (one click)

Go to the **Setup** tab in the sidebar (second item).

You'll see four sections. We're going to use just two.

### 7a. Download a small local model

Scroll to **"Recommended models"**. The first row is **SmolLM2-135M
(Q2_K) — tiny + fast prefix model**. Click **Download** at the right.

A new card appears under **"Setup jobs"** showing a progress bar. Wait
~30 seconds. When it says `done`, the model is in `models/`.

> 75 MB. Tiny. Good for the "instant prefix" feature.

### 7b. Tell AGCL to use it for chat

Scroll to **"Auto-setup chat"** (second card from top). Fill in:

- **Local model path**: `models/SmolLM2-135M-Instruct-Q2_K.gguf`
- **Default cloud**: `claude` if you set `ANTHROPIC_API_KEY`, otherwise
  `openai`
- Leave the rest blank.

Click **Apply chat defaults**.

The status line below the button says `applied: LOCAL_MODEL_PATH,
DEFAULT_CLOUD`. **You're configured.**

> Behind the scenes, this writes to your `.env`. You could've edited
> the file by hand — that's exactly what `agcl setup-chat` does on
> the CLI — but the form is faster.

---

## Step 8 — Restart the node

Some `.env` keys (the local model path, the default provider) only
take effect when the node starts.

**Go back to the terminal running the node** (the one from Step 4).
Press **Ctrl-C** to stop it. Then start it again:

```bash
python main.py node --port 9876 --cors "*"
```

A *new* bearer key prints. Copy it.

In the browser, click **Disconnect** in the sidebar, paste the new key,
and Connect.

> Want to skip this dance every restart? Pin a static key:
> `export AGCL_NODE_AUTH=mysecretkey` before starting the node, and
> the same key works forever.

---

## Step 9 — Send your first message

Click the **Chat** tab. You'll see an empty session called `default`.

In the message box at the bottom, type:

```
What's a concise definition of "agentic AI"?
```

Press **Cmd/Ctrl+Enter** (or click **Send**).

You should see, in order:

1. A grey "**local prefix**" bubble — the local SmolLM model's first
   words, with a latency badge like `(120ms)`.
2. An "**assistant**" bubble that fills in the rest, streamed live
   from Claude or OpenAI, formatted as Markdown.

If both bubbles appear, **everything works.** Welcome to AGCL.

---

## What each tab does, in plain English

You don't need to learn them all today. Come back when you want a
feature.

| Tab               | Use it when…                                                                                                                            |
|-------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| **Overview**      | You want a snapshot — what cloud keys are configured, which model is loaded, GPU info, a small token-usage donut.                       |
| **Setup**         | You're installing or adding more models. The big "Run autoconfig" button sets up the full multi-agent feature in one click.             |
| **Chat**          | Day-to-day chatting. Multi-session, Markdown-rendered, copy-button on every reply.                                                       |
| **Recursive MAS** | You want the experimental multi-agent reasoning loop where two small models pass embeddings to each other. Comes with a live loss curve. |
| **Topics**        | You've used Recursive MAS for a while; this lists the topics it has learned and lets you delete them to retrain.                          |
| **Mini-Trainer**  | A tiny model that learns from your traffic in the background. Toggle it on / off; watch the loss curve.                                    |
| **Usage & Cost**  | "How many tokens did I burn this week?" — per-provider, per-session, with line + donut charts. Set hard quotas here.                       |
| **Configuration** | Edit any setting that lives in `.env`, upload a `mas.json`, export your full config bundle to share.                                       |
| **Toolkit**       | You're plugging in Ollama / vLLM / a LiteLLM proxy / Redis / S3. Discover button shows what's wired up; Ping checks each.                  |
| **Plugins**       | If someone gave you a `.py` plugin, drop it in `plugins/` and click Reload.                                                              |
| **Diagnostics**   | Real-time pressure gauge, latency curve, learned active-hours bar.                                                                        |
| **CLI Actions**   | One-click batch operations — run a full health snapshot, ping every adapter, smoke-test the cloud, export a config bundle.               |
| **Deploy**        | Generate Docker, Kubernetes, or GCP Cloud Run manifests for self-hosting.                                                                  |

---

## Going further (optional)

### Add the recursive multi-agent feature

Go to **Setup → Auto-setup recursive MAS**. Tick the **"Download both
HF models"** checkbox, then **Run autoconfig**.

Pulls Qwen2.5-0.5B + TinyLlama-1.1B from HuggingFace (~3 GB, several
minutes). The job card shows live progress. When it's done, head to
**Recursive MAS** in the sidebar and try a prompt — you'll see
training-step events stream in real time and a loss-curve chart.

### Add a custom OpenAI-compatible provider

If you have a Together / Fireworks / Groq / local LiteLLM endpoint
that speaks the OpenAI API: **Usage & Cost → Custom OpenAI-compatible
providers**. Fill the four fields (name, base URL, the env var that
holds the key, model id). Click **Add**. The provider is now selectable
from the chat dropdown.

### Set a spending cap

**Usage & Cost → Quotas**. Pick a provider, set "Max USD" to e.g. `5`,
period `monthly`, click **Apply quota**. The node will refuse to call
that provider once you've hit `$5` of estimated spend that month.

### Share your full setup with a friend

**Configuration → Config bundle**. Click **Download bundle**. Send
the JSON to a teammate. They click **Validate (dry-run)** to see what's
missing on their machine, then **Apply import** to take the same setup.
API keys aren't in the bundle (by design); everything else is.

---

## Common problems

| Symptom                                                | Try this                                                                                              |
|---------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Login pane says "Connect failed: Network error"        | The node isn't running. Check the terminal window from Step 4 — it should still be live.              |
| Login pane says "Saved key rejected"                   | You restarted the node and the bearer key changed. **Disconnect** and paste the new key.              |
| The chat replies are blank or 5xx                      | Check **Overview** — does it say "openai key: set" or "claude key: set"? If both say "missing", you skipped Step 3 or didn't restart the node after editing `.env`. |
| Local prefix bubble never appears                      | The local model file is missing or wrong. **Setup → Locally cached models** should list your `.gguf`. If it doesn't, repeat 7a. |
| Browser console shows `Mixed content blocked`         | You're running the page on `https://` and pointing at `http://localhost`. Either run the page over HTTP too (Step 5 option 2), or front the node with a TLS reverse proxy. |
| Setup → "Auto-setup recursive MAS" hangs at 5%        | The HF download started but is slow. Look at the job card — the log will tell you which file it's pulling. Cancel and retry on a faster network if needed. |
| You hit Anthropic / OpenAI billing limits             | **Usage & Cost → Quotas** lets you set a cap so it can never happen again.                                 |

For the longer troubleshooting tree, see
[../docs/troubleshooting.md](../docs/troubleshooting.md).

---

## Hosting on GitHub Pages

Once everything works locally, you can host the **page itself** on
GitHub Pages so any machine you visit gets the same dashboard, all
talking to **its own local AGCL node**:

1. Push the repo to GitHub.
2. Repository **Settings → Pages → Source** = `Deploy from a branch`,
   branch `main`, folder `/ (root)`.
3. Wait a minute, then visit `https://<your-user>.github.io/<repo>/`.
4. On every machine you use the page from, you still need the node
   running locally and the bearer key — exactly the same Steps 4 + 6
   as before.

The page is a static set of files (`index.html` + `web_socket/*`); no
secrets are baked in.

---

## Wait, where's everything actually stored?

Curious people want to know:

| Thing                          | Where it lives                                              |
|--------------------------------|-------------------------------------------------------------|
| API keys                       | `.env` at the project root (gitignored)                     |
| Bearer key for the GUI         | Your browser's `localStorage`                               |
| Chat sessions                  | `.agent_state/sess_*.json`                                  |
| Trained MAS topics             | `.agent_state/mas_topics/<id>/`                              |
| Cached HF models               | `models/hf/hub/` and `models/hf_local/`                      |
| GGUF files                     | `models/`                                                   |
| Usage history & quotas         | `.agent_state/usage.jsonl` and `.agent_state/usage_quotas.json` |
| Custom-provider registry       | `.agent_state/custom_providers.json`                        |

Delete `.agent_state/` to wipe all your sessions and learned state
(API keys and downloaded models survive). Delete `models/` to reclaim
disk; the next chat-tab use will need you to re-download.

---

## Where to read next

- The full set of features and what they do: [../README.md](../README.md)
- Every setting, what it changes, what its default is: [../docs/configuration.md](../docs/configuration.md)
- What's safe and what isn't: [../docs/security.md](../docs/security.md)
- A specific tab's endpoints: [../docs/web-console.md](../docs/web-console.md)
- Things broke: [../docs/troubleshooting.md](../docs/troubleshooting.md)

You're all set. Go ahead and open the **Chat** tab.
