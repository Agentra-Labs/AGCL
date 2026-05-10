#!/usr/bin/env node
/**
 * agcl-client — Node CLI for the @agcl/client package.
 *
 * Lets JS/TS users drive an AGCL node without writing any client code:
 *
 *   agcl-client status
 *   agcl-client run "summarize the diff"
 *   agcl-client stream "explain entanglement"
 *   agcl-client topics
 *   agcl-client usage
 *   agcl-client toolkit discover
 *   agcl-client toolkit ping ollama
 *   agcl-client dashboard            # opens browser
 *
 * Reads:
 *   AGCL_HOST   default localhost
 *   AGCL_PORT   default 9876
 *   AGCL_KEY    bearer token (or use --key)
 *   AGCL_BASE   full URL (overrides host/port)
 *
 * Zero deps; uses global fetch (Node 18+) and the published
 * @agcl/client when installed.  Falls back to raw fetch when run
 * out of the source checkout (so contributors don't need a build).
 */

const argv = process.argv.slice(2);
function flag(name, def) {
  const idx = argv.findIndex(a => a === name || a.startsWith(name + "="));
  if (idx < 0) return def;
  const v = argv[idx];
  if (v.includes("=")) { argv.splice(idx, 1); return v.split("=", 2)[1]; }
  argv.splice(idx, 2);
  return argv[idx] ?? def;       // value was at idx, splice already removed it
}

const host    = flag("--host",    process.env.AGCL_HOST || "localhost");
const port    = flag("--port",    process.env.AGCL_PORT || "9876");
const baseEnv = process.env.AGCL_BASE;
const key     = flag("--key",     process.env.AGCL_KEY);
const base    = flag("--base", baseEnv) || `http://${host}:${port}`;

if (!key) {
  process.stderr.write(
    "agcl-client: no auth key. Set $AGCL_KEY or pass --key=<bearer>.\n",
  );
  process.exit(2);
}

async function api(path, init = {}) {
  const url = `${base.replace(/\/$/, "")}${path}`;
  const r = await fetch(url, {
    ...init,
    headers: {
      "Authorization": `Bearer ${key}`,
      "Content-Type":  "application/json",
      ...(init.headers || {}),
    },
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${url}\n${text.slice(0, 300)}`);
  }
  if (r.headers.get("content-type")?.includes("application/json")) {
    return r.json();
  }
  return r.text();
}

async function* sse(path, init = {}) {
  const r = await fetch(`${base.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: {
      "Authorization": `Bearer ${key}`,
      "Content-Type":  "application/json",
      "Accept":        "text/event-stream",
      ...(init.headers || {}),
    },
  });
  if (!r.ok) throw new Error(`${r.status} ${path}: ${await r.text()}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n"); buf = parts.pop() ?? "";
    for (const p of parts) {
      for (const ln of p.split("\n")) {
        if (!ln.startsWith("data:")) continue;
        const payload = ln.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try { yield JSON.parse(payload); } catch { /* skip */ }
      }
    }
  }
}

const cmd = argv.shift();
const args = argv;

function help() {
  process.stdout.write(`agcl-client — Node CLI for the AGCL node server

Usage:
  agcl-client <command> [args]   --base http://host:port  --key <bearer>

Commands:
  status                     /node/health + /node/info
  verify                     POST /node/auth/verify
  run "<msg>" [--sid X]      POST /node/mas/run
  stream "<msg>" [--sid X]   POST /node/mas/stream  (live JSONL)
  chat "<msg>" [--sid X]     POST /node/chat/{sid}  (SSE)
  topics                     GET  /node/topics
  sessions                   GET  /node/mas/sessions
  pause <sid> | resume <sid> | halt <sid> | latent <sid>
  usage                      GET  /node/usage/summary
  quotas                     GET  /node/usage/quotas
  toolkit discover           GET  /node/toolkit/discover
  toolkit ping <adapter>     GET  /node/toolkit/ping/<adapter>
  toolkit chat "<msg>"       POST /node/toolkit/chat
  dashboard                  open the dashboard URL in a browser
  help                       this message

Examples:
  AGCL_KEY=$(< /tmp/agcl.key) agcl-client status
  agcl-client stream "describe the change" --sid pr-123
  agcl-client toolkit ping ollama
`);
}

async function open(url) {
  const platform = process.platform;
  const opener = platform === "darwin" ? "open"
              : platform === "win32"  ? "explorer"
              : "xdg-open";
  const { spawn } = await import("node:child_process");
  spawn(opener, [url], { stdio: "ignore", detached: true }).unref();
  process.stdout.write(`opened ${url}\n`);
}

async function main() {
  if (!cmd || cmd === "help" || cmd === "--help" || cmd === "-h") {
    help(); return;
  }

  if (cmd === "status") {
    const h = await api("/node/health");
    const i = await api("/node/info");
    process.stdout.write(JSON.stringify({ health: h, info: i }, null, 2) + "\n");
    return;
  }

  if (cmd === "verify") {
    process.stdout.write(JSON.stringify(await api("/node/auth/verify", { method: "POST" }), null, 2) + "\n");
    return;
  }

  if (cmd === "run") {
    const msg = args.shift();
    const sidIdx = args.indexOf("--sid");
    const sid = sidIdx >= 0 ? args[sidIdx + 1] : "default";
    if (!msg) { help(); process.exit(2); }
    const r = await api("/node/mas/run", {
      method: "POST",
      body: JSON.stringify({ message: msg, session_id: sid }),
    });
    process.stdout.write(JSON.stringify(r, null, 2) + "\n");
    return;
  }

  if (cmd === "stream") {
    const msg = args.shift();
    const sidIdx = args.indexOf("--sid");
    const sid = sidIdx >= 0 ? args[sidIdx + 1] : "default";
    if (!msg) { help(); process.exit(2); }
    for await (const ev of sse("/node/mas/stream", {
      method: "POST",
      body: JSON.stringify({ message: msg, session_id: sid }),
    })) {
      process.stdout.write(JSON.stringify(ev) + "\n");
      if (ev.event === "done") break;
    }
    return;
  }

  if (cmd === "chat") {
    const msg = args.shift();
    const sidIdx = args.indexOf("--sid");
    const sid = sidIdx >= 0 ? args[sidIdx + 1] : "default";
    if (!msg) { help(); process.exit(2); }
    for await (const ev of sse(`/node/chat/${sid}`, {
      method: "POST",
      body: JSON.stringify({ session_id: sid, message: msg }),
    })) {
      if (ev.type === "prefix" || ev.type === "chunk") {
        process.stdout.write(ev.text || "");
      } else if (ev.type === "done") {
        process.stdout.write("\n");
        break;
      }
    }
    return;
  }

  if (cmd === "topics") {
    process.stdout.write(JSON.stringify(await api("/node/topics"), null, 2) + "\n");
    return;
  }

  if (cmd === "sessions") {
    process.stdout.write(JSON.stringify(await api("/node/mas/sessions"), null, 2) + "\n");
    return;
  }

  if (["pause", "resume", "halt", "latent"].includes(cmd)) {
    const sid = args.shift();
    if (!sid) { process.stderr.write(`usage: agcl-client ${cmd} <sid>\n`); process.exit(2); }
    const sub = cmd === "latent" ? "latent" : cmd;
    const method = cmd === "latent" ? "GET" : "POST";
    const r = await api(`/node/mas/sessions/${sid}/${sub}`, { method });
    process.stdout.write(JSON.stringify(r, null, 2) + "\n");
    return;
  }

  if (cmd === "usage") {
    process.stdout.write(JSON.stringify(await api("/node/usage/summary"), null, 2) + "\n");
    return;
  }

  if (cmd === "quotas") {
    process.stdout.write(JSON.stringify(await api("/node/usage/quotas"), null, 2) + "\n");
    return;
  }

  if (cmd === "toolkit") {
    const sub = args.shift();
    if (sub === "discover") {
      process.stdout.write(JSON.stringify(await api("/node/toolkit/discover"), null, 2) + "\n");
      return;
    }
    if (sub === "ping") {
      const adapter = args.shift();
      if (!adapter) { process.stderr.write("usage: toolkit ping <adapter>\n"); process.exit(2); }
      process.stdout.write(JSON.stringify(await api(`/node/toolkit/ping/${adapter}`), null, 2) + "\n");
      return;
    }
    if (sub === "chat") {
      const msg = args.shift();
      if (!msg) { process.stderr.write("usage: toolkit chat \"<msg>\"\n"); process.exit(2); }
      const r = await api("/node/toolkit/chat", {
        method: "POST",
        body: JSON.stringify({ messages: [{ role: "user", content: msg }] }),
      });
      const text = r?.choices?.[0]?.message?.content ?? JSON.stringify(r);
      process.stdout.write(text + "\n");
      return;
    }
    process.stderr.write(`unknown toolkit subcommand: ${sub}\n`);
    process.exit(2);
  }

  if (cmd === "dashboard") {
    await open(`${base}/node/dashboard`);
    return;
  }

  process.stderr.write(`unknown command: ${cmd}\n`);
  help();
  process.exit(2);
}

main().catch(e => {
  process.stderr.write(`agcl-client: ${e.message}\n`);
  process.exit(1);
});
