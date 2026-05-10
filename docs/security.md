# Security — auth, limitations, and what AGCL does (and does not) protect against

This is an honest, code-anchored description of AGCL's security posture.
It documents the **current authentication mechanism**, the **safety
features** that ship in the box, the **known limitations**, and the
**threat model** the project assumes (localhost-first, single-user).

If you are deploying AGCL outside that envelope — exposed to the
internet, multi-tenant, or holding sensitive secrets — read the
"breaking the limits" sections carefully. Several gaps are deliberate;
fixing them is a deployment-layer concern (TLS proxy, network
isolation, audit logging) rather than something AGCL does internally.

> Sister docs:
> - [docs/web-console.md](web-console.md) — using the bundled web UI
> - [docs/configuration.md](configuration.md) — every config knob
> - [docs/troubleshooting.md](troubleshooting.md) — common breakage

---

## TL;DR

| Area                | Status                                                                                       |
|---------------------|-----------------------------------------------------------------------------------------------|
| Bearer auth         | **Enforced** on every `/node/*` route via global middleware; one key per process              |
| HTTPS               | **Not built-in** — bring your own reverse proxy (Caddy, nginx, Cloudflare)                    |
| CORS                | Permissive by default (`*`) — tighten with `--cors "https://your-origin"`                     |
| Multi-user          | **Not modeled** — bearer key is a single root credential                                      |
| Audit log           | **Not built-in** — auth failures and config changes are not persisted                         |
| Rate limiting       | **Not built-in** — `/pressure` is a meter, not a throttle                                     |
| Quotas              | **Hard caps** (request blocked when exceeded) but with a small race window                    |
| Plugin sandboxing   | **None** — `plugins/*.py` runs with the host process's privileges                             |
| Secrets in API      | Masked in `GET /node/config/env`; **cannot be patched via API** unless allowlist is bypassed  |
| Telemetry           | **None** — no analytics, no phone-home                                                        |
| Cloud output exec   | **No `eval`/`exec`** — cloud-teacher reply is parsed as data only                             |

---

## 1. Authentication

### How the bearer key is created

On `python main.py node` startup, [agcl/node.py:46-50](../agcl/node.py)
calls `secrets.token_urlsafe(32)` to generate a 43-character URL-safe
random key, prints it once on stdout, and keeps it in an in-memory
dict (`_NODE_AUTH["key"]`).

To pin a static key (for instance, when a process supervisor restarts
the node and you want clients to keep working without re-pasting):

```bash
export AGCL_NODE_AUTH=$(openssl rand -hex 32)
python main.py node --port 9876
# or:
python main.py node --auth-key "$(cat ~/.agcl-node-key)"
```

The key has **no expiration**, **no rotation**, and is **valid for the
process lifetime**. Killing the node invalidates it instantly.

### How requests are validated

A FastAPI middleware ([agcl/node.py:67-81](../agcl/node.py)) intercepts
every request. It extracts the `Authorization: Bearer <key>` header
and compares it byte-for-byte to `_NODE_AUTH["key"]`. Mismatch returns
HTTP **401** with `{"error": "invalid token"}`; missing header returns
401 with `{"error": "missing bearer token"}`.

### Endpoints that are NOT auth-gated

These routes are intentionally public so clients can probe before
authenticating:

| Path                  | Why public                                                       |
|-----------------------|------------------------------------------------------------------|
| `/node/health`        | Liveness probe (used by orchestration / load balancers)          |
| `/node/auth/verify`   | The endpoint that *checks* a bearer key — auth happens inside    |
| `/node/dashboard`     | Static SPA HTML; the JS inside still calls auth-gated APIs       |
| `/docs`, `/openapi.json`, `/redoc` | FastAPI built-in schema docs                          |
| Any `OPTIONS` request | CORS preflight bypasses auth (correct per CORS spec)             |

Everything else under `/chat`, `/session`, `/node/*` (including
`/node/toolkit/*` and `/node/usage/*`) is auth-gated.

### Token storage on the client

The web console ([web_socket/js/api.js](../web_socket/js/api.js)) saves
the host and bearer key in browser `localStorage` under `agcl.host`
and `agcl.key`. The key is sent only to the configured host and never
to any third party. Clearing localStorage or clicking **Disconnect** in
the sidebar removes both.

---

## 2. Transport security

**The node itself only speaks HTTP.** There is no TLS termination,
certificate handling, or HTTPS listener inside the codebase. This is a
deliberate localhost-first design.

For any deployment beyond `127.0.0.1` you must front the node with a
reverse proxy:

```
# Caddy example
agcl.your-domain.tld {
    reverse_proxy 127.0.0.1:9876
}

# nginx example
location / {
    proxy_pass http://127.0.0.1:9876;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

Without TLS, the bearer key travels in cleartext on every request.
That's fine for localhost; it's not fine for any other context.

### Mixed-content note

If you host the web console on GitHub Pages (HTTPS) and point it at a
plain `http://localhost:9876`, browsers treat it as mixed content.
Chrome/Edge/Safari allow it (localhost is "potentially trustworthy");
Firefox is stricter. See
[docs/web-console.md → mixed-content note](web-console.md#mixed-content-note-https-site--http-localhost).

---

## 3. CORS

CORS middleware is configured at [agcl/node.py:993-1000](../agcl/node.py):

| Setting              | Default               | Override                                 |
|----------------------|-----------------------|------------------------------------------|
| `allow_origins`      | `["*"]`               | `--cors "https://a,https://b"`           |
| `allow_credentials`  | `False`               | (not exposed; cookies are not used)      |
| `allow_methods`      | `GET POST PUT PATCH DELETE OPTIONS` | (not exposed)              |
| `allow_headers`      | `Authorization, Content-Type` | (not exposed)                    |

Middleware order is auth-first, CORS-last (CORS responses still get
served on auth rejections, and OPTIONS preflight passes through CORS
without hitting auth — both correct).

**No additional origin checks** beyond CORS — there is no `Host` /
`Referer` validation. With `--cors "*"`, any browser tab on any site
can talk to the node provided it has the bearer key.

---

## 4. Secrets handling

### How `.env` is loaded

[agcl/secrets.py](../agcl/secrets.py) parses `KEY=VALUE` lines from
`.env` at the repo root, strips surrounding quotes, supports
`# comments`, and merges them into `os.environ` at import time.
Process environment wins over `.env`.

`.env` is in `.gitignore` (verified at [.gitignore:34-36](../.gitignore));
do not commit it.

### Secrets shown via the API

`GET /node/config/env` ([agcl/node.py:622-636](../agcl/node.py))
returns the env dict but **masks** any variable whose name contains
`KEY`, `SECRET`, `TOKEN`, or `PASSWORD`. Mask format:
`***(<length> chars)`. Other variables are returned in full.

### Secrets you CAN'T patch via the API (by design)

`PATCH /node/config/env` defaults `allowlist_only: true`. The
allowlist is the export bundle defined in
[agcl/config_bundle.py:47-68](../agcl/config_bundle.py) — it includes
runtime knobs (`DEFAULT_CLOUD`, `OPENAI_MODEL`, `LOCAL_MODEL_PATH`,
…) but **excludes every secret-looking key**. So:

- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, custom-provider keys —
  cannot be set via the API; must live in `.env` or process env.
- The web console's Configuration tab respects this — it submits
  patches with `allowlist_only: true`. Toggling that off is allowed
  but is an explicit, deliberate action.

### Custom OpenAI-compatible providers

`POST /node/usage/providers/custom` stores a record like
`{name, base_url, api_key_env, model, kind}`. The
**`api_key_env` field holds the NAME of an env var, not the value**.
The actual key has to be set in `.env` / process env separately. This
keeps secrets out of the registry file
([agcl/usage.py:492-514](../agcl/usage.py)).

### `.env` write semantics on PATCH

When `persist: true`, `_persist_env()` ([agcl/node.py:113-131](../agcl/node.py))
reads the entire `.env`, replaces matching keys in place, appends new
keys, and writes the file atomically with `Path.write_text()`. **No
backup is created.** Manage that at the OS layer (etckeeper, git,
periodic snapshots).

---

## 5. Filesystem writes — boundaries and gaps

| Endpoint                                      | What it writes                                  | Bounded? |
|-----------------------------------------------|-------------------------------------------------|----------|
| `PATCH /node/config/env` (`persist: true`)    | `.env` at repo root                             | Yes — only env keys, atomic write |
| `PUT /node/config/mas`                        | `mas.json` (with `mas.json.bak` backup)         | Yes — fixed path                  |
| `POST /node/toolkit/manifest/{docker,k8s,gcp}`| Files under `out_dir`                           | **No** — `out_dir` is unclamped   |
| `PUT /node/toolkit/checkpoint`                | `<STATE_DIR>/topics/<topic_id>/...`             | **No** — `topic_id` not validated |
| Session flush (idle / TTL)                    | `<STATE_DIR>/sess_{sid}.json`                   | **No** — `sid` not sanitized      |

The unclamped paths are usability decisions for trusted-input
contexts. In practice:

- `out_dir` is supplied by the caller (you, or your tooling); the
  node makes no attempt to keep writes inside `STATE_DIR`. If the node
  process has broad filesystem permissions, an attacker with the
  bearer key can write files anywhere the process can.
- `topic_id` and `sid` are never user-supplied in the normal flow —
  they're generated internally — but no code rejects path-traversal
  characters (`../`, leading `/`). If you build something on top of
  AGCL that takes `topic_id` from untrusted input, validate it
  yourself.

**Mitigation**: run the node as an unprivileged user, in a container
with a read-only root filesystem and a writable `STATE_DIR` mount, or
behind a chroot.

---

## 6. Code execution & supply-chain

### Plugin loader — explicitly NOT sandboxed

[agcl/plugins.py](../agcl/plugins.py) discovers `*.py` files in
`plugins/` (or `AGCL_PLUGINS_DIR`) and imports them with
`importlib.util.exec_module`. Each must export a `register(ctx)`
function that gets full access to the FastAPI app, MAS singleton,
session store, and config.

**No signature check. No allowlist. No sandboxing.** The plugin docstring
itself flags this ([agcl/plugins.py:14-16](../agcl/plugins.py)):
"Plugins are *not* sandboxed — they run with the same privileges as
the host process. Only load plugins you trust."

If a plugin fails, others still load (try/except wrapper), but a
malicious plugin can read API keys, exfiltrate sessions, mutate
state, and execute arbitrary system commands.

### Cloud output is parsed as data, never executed

[agcl/recursive/auto_train.py:149-164](../agcl/recursive/auto_train.py)
parses the cloud teacher's bootstrap reply with
`json.loads`, falling back to a non-greedy regex
(`re.search(r"\{.*\}", raw, re.DOTALL)`) if the model added prose
around the JSON. Extracted fields are `answer: str` and
`reformulations: List[str]`. **No `eval`, no `exec`, no shell.**
The values feed the training loop as text and tensor inputs only.

### Toolkit imports are lazy and caught

`/node/toolkit/discover` reports importability without crashing the
process if a library is missing (`litellm`, `ollama`, `redis`, etc.).
If you call an endpoint that depends on a missing lib, you get a
structured error instead of a stack trace.

---

## 7. Outbound data flows — what leaves the box

| Destination                              | When                                        | Sent payload                                     |
|------------------------------------------|---------------------------------------------|--------------------------------------------------|
| OpenAI / Anthropic                       | Default cloud path                          | Full message history + system prompt + local prefix |
| LiteLLM / vLLM / Ollama gateway          | When `AGCL_LLM_BASE_URL` / `VLLM_HOST` / `OLLAMA_HOST` set | Full message history (gateway routes from there) |
| Custom OpenAI-compatible provider        | When the user routes a session/tool to it  | Full message history                              |
| Cloudflare relay (`AGCL_RELAY_URL`)      | Only when `/node/toolkit/relay/publish` is called explicitly | Structured event dicts (not raw prompts)         |
| Telemetry / analytics service            | **Never** — no such code exists              | n/a                                               |

The node does NOT send anything outbound at startup, idle, or on a
heartbeat. Outbound traffic is exclusively in response to a turn
being processed against a configured provider.

If you want to stop all outbound traffic to a particular provider:
delete its key from `.env`, or remove the custom-provider entry via
`DELETE /node/usage/providers/custom/{name}`.

---

## 8. Quotas — the hard cap, and its limit

[agcl/usage.py:282-302](../agcl/usage.py) defines `check_quota()`,
which is called **before** the cloud request is sent
([agcl/cloud.py:149](../agcl/cloud.py)). If the configured cap is
hit, it raises `QuotaExceeded`, the call is never made, and the
client gets a 5xx with the cap message
(`"provider 'claude' exceeded daily token cap (8500 >= 8000)"`).

Caps support `lifetime`, `daily`, `monthly` periods, and either
`max_tokens` or `max_credit_usd`.

**Limit**: the check-then-call sequence is not atomic. Two concurrent
requests can both pass `check_quota`, both then send, and both then
record — briefly exceeding the cap. The error window scales with
concurrency. For single-user / low-concurrency setups this is fine;
for strict billing isolation, set the cap below the actual ceiling
or front the node with a single-flight gateway.

Quotas are enforced **per provider name**, including custom
providers. Quotas survive process restart (persisted to
`<STATE_DIR>/usage_quotas.json`).

---

## 9. Strict mode — anti-hallucination, not a security boundary

`AGCL_STRICT=1` (or `STRICT_MODE=1`) is a tuning flag. When set
([agcl/recursive/session.py:87-160](../agcl/recursive/session.py)):

- Local MAS output is *always* fed to the cloud continuator — never
  served raw.
- Prefix length cap is reduced (4 tokens vs the default 12).
- The degeneracy detector becomes more aggressive — short, repetitive,
  or pure-punctuation outputs trigger an automatic cloud re-roll.

It is **not** a privilege boundary, content filter, or rate limit.
It cannot prevent a determined caller from getting raw output by
calling a different endpoint, and it does not alter what the cloud
provider returns.

---

## 10. Pressure / rate limiting

[agcl/pressure.py](../agcl/pressure.py) is **a meter, not a
throttle.** It records latencies and request times in a sliding
window (`PRESSURE_WINDOW_SEC`, default 60s) and exposes a `rate_ratio`
through `/pressure`. Nothing in the request path uses this number to
drop or delay calls — it's there so dashboards (and the agent itself,
when deciding to pre-warm) can react.

There is **no per-IP rate limit, no per-session throttle, no global
concurrency cap**. A misbehaving caller can:

- Open arbitrarily many MAS sessions (each holds memory for trained
  links until eviction).
- Issue requests as fast as the cloud provider accepts them — until a
  quota or the provider's own rate limit kicks in.

If you need rate limiting, put it in the reverse proxy: nginx
`limit_req`, Caddy `rate_limit`, Cloudflare WAF.

---

## 11. WebRTC signaling

`/node/toolkit/webrtc/*` ([agcl/toolkit/endpoints.py:248-285](../agcl/toolkit/endpoints.py))
sits behind the same bearer auth as the rest of `/node/*`. There is
**no session-ownership check** — once authenticated, any caller who
guesses or learns a `session_id` can read the SDP offer/answer and ICE
candidates for it. That's acceptable for the single-user case the
node is designed for; if you ever expose AGCL to multiple users,
add a session-membership table at the application layer.

WebRTC sessions carry a 300-second TTL and are garbage-collected; no
DoS protection beyond that.

---

## 12. Web console — what the browser holds

[web_socket/js/api.js](../web_socket/js/api.js) and
[web_socket/js/app.js](../web_socket/js/app.js):

- Bearer key is in `localStorage` under `agcl.key`. Sent only as
  `Authorization: Bearer …` to the configured host.
- No third-party requests. No CDN, no analytics, no fonts loaded
  from the network. The whole console is static files in the repo.
- Markdown rendering ([web_socket/js/lib/markdown.js](../web_socket/js/lib/markdown.js))
  HTML-escapes every input character before re-introducing the small
  set of tags it emits (`<strong>`, `<em>`, `<code>`, `<pre>`,
  `<a target="_blank" rel="noopener noreferrer">`, headings, lists).
  `javascript:` URLs are rejected. There is no inline-HTML
  pass-through.
- Chat input is sent verbatim as `message` to `/node/chat/{sid}` and
  `/node/mas/{run,stream}`. The server does not interpret it as
  anything other than the user turn.

---

## 13. Limitations — things AGCL does NOT do

| Limitation                | Impact                                                                                              | Mitigation                                                  |
|---------------------------|------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| **No multi-user model**   | Bearer key = root. Whoever has it sees every session, config, quota, and token.                     | Run a separate node per user; isolate via OS or container.   |
| **No HTTPS**              | Bearer key + prompts travel in cleartext.                                                            | Always front with a TLS reverse proxy beyond localhost.      |
| **No rate limiting**      | Misbehaving callers can exhaust quota / open unlimited MAS sessions / fill `STATE_DIR`.              | Reverse proxy with rate-limit module.                        |
| **No audit log**          | Auth attempts, config patches, quota changes — none are persisted.                                   | Front with a logging proxy; or build a plugin that hooks routes. |
| **No key rotation**       | Bearer key is static for process lifetime.                                                           | Set `AGCL_NODE_AUTH` to a value rotated by your supervisor.  |
| **No plugin sandboxing**  | A `plugins/*.py` file has full access to secrets, MAS state, and the host system.                    | Vet every plugin you load. Load none unless needed.          |
| **No `out_dir` clamp**    | Manifest emitters can write outside `STATE_DIR`.                                                     | Run as unprivileged user / in a container with restricted FS.|
| **No path-traversal check** for `topic_id` / `sid` | Internal IDs only — but if you wire user input to them, they'd traverse.                | Validate any externally-derived ID before passing it in.     |
| **Quota race window**     | Concurrent calls can briefly exceed cap.                                                             | Set caps below the strict ceiling.                           |
| **No provider whitelist** | A user with the bearer key can register a custom provider pointing at any URL.                       | Restrict the bearer key to trusted users.                    |
| **No DoS budget** for MAS | Sessions hold trained-link state in memory until evicted. Many sessions = much memory.               | Cap concurrent sessions at the proxy; restart node periodically. |

---

## 14. Recommended deployments

### Single-user, localhost (the default)

No further hardening required beyond:

```bash
python main.py node --bind 127.0.0.1 --port 9876 --cors "*"
```

The bind to `127.0.0.1` means no LAN device can reach the node;
combined with the bearer key, you're as safe as your operating
system account.

### LAN / home-lab

```bash
# generate a stable key once
echo "AGCL_NODE_AUTH=$(openssl rand -hex 32)" >> ~/.agcl-env

# bind to LAN IP, restrict CORS
source ~/.agcl-env
python main.py node --bind 192.168.1.10 --port 9876 \
    --cors "https://your-pages-site.github.io,http://192.168.1.10"
```

If the LAN includes machines you don't trust, also put the node
behind a reverse proxy with TLS and rate limits.

### Internet-exposed (production)

Required: TLS termination, rate limiting, audit logging, and a
deployment topology that doesn't put `/node/*` directly on the
public internet.

```
[Internet] --HTTPS--> [Caddy/nginx with rate-limit + access log]
                              --HTTP--> [127.0.0.1:9876] (AGCL node)
```

Additional:
- Set `AGCL_NODE_AUTH` to a value rotated by your secret manager.
- `--cors "https://your-domain"` (no wildcards).
- Run the node as an unprivileged user, in a container with a
  read-only root filesystem and a writable mount for `STATE_DIR`.
- Forward proxy / firewall the outbound side so the node can only
  reach the cloud providers you intend (api.openai.com, api.anthropic.com,
  your gateway).
- Don't load plugins from outside your own audited tree.
- Periodically restart the node — it doesn't leak per se, but a
  restart re-issues the bearer key (if you don't pin one) and
  evicts stale MAS state.

---

## 15. Reporting a security issue

If you find a vulnerability, please **don't open a public issue.**
Email the maintainers (see [pyproject.toml](../pyproject.toml) →
`[project] authors`) with:

- A description of the issue.
- A minimal reproduction.
- The affected file:line if known.

Patches that come with a regression test will land much faster.
