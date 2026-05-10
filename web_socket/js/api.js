/* AGCL API client.
 *
 * Single global `API` object. Holds connection state (host, key) in
 * localStorage. Provides JSON helpers and a streaming SSE reader.
 *
 * The bearer key is sent on every request to /node/* (the local server
 * checks it in middleware). The base FastAPI app on /chat /pressure
 * /patterns /health /session is also served at the same host but
 * usually unauthenticated — we send the key anyway, harmless. */

(function () {
  "use strict";

  const LS_HOST = "agcl.host";
  const LS_KEY  = "agcl.key";

  const API = {
    host: "",
    key:  "",

    init() {
      this.host = (localStorage.getItem(LS_HOST) || "").replace(/\/+$/, "");
      this.key  = localStorage.getItem(LS_KEY) || "";
    },

    save(host, key) {
      this.host = (host || "").replace(/\/+$/, "");
      this.key  = key || "";
      localStorage.setItem(LS_HOST, this.host);
      localStorage.setItem(LS_KEY,  this.key);
    },

    clear() {
      localStorage.removeItem(LS_HOST);
      localStorage.removeItem(LS_KEY);
      this.host = ""; this.key = "";
    },

    isConfigured() { return !!(this.host && this.key); },

    url(path) {
      if (path.startsWith("http")) return path;
      if (!path.startsWith("/")) path = "/" + path;
      return this.host + path;
    },

    headers(extra) {
      const h = { "Authorization": "Bearer " + this.key };
      if (extra) Object.assign(h, extra);
      return h;
    },

    async req(method, path, body, opts) {
      opts = opts || {};
      const init = {
        method,
        headers: this.headers(body ? { "Content-Type": "application/json" } : null),
      };
      if (body !== undefined && body !== null) {
        init.body = (typeof body === "string") ? body : JSON.stringify(body);
      }
      let res;
      try {
        res = await fetch(this.url(path), init);
      } catch (e) {
        throw new Error("Network error: " + (e.message || e));
      }
      if (opts.raw) return res;
      const ct = res.headers.get("content-type") || "";
      const txt = await res.text();
      const data = ct.includes("application/json") && txt ? JSON.parse(txt) : txt;
      if (!res.ok) {
        const msg = (data && data.detail) || (data && data.error) || (typeof data === "string" ? data : "")
          || (res.status + " " + res.statusText);
        const err = new Error(msg);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    },

    get(path, opts)        { return this.req("GET",    path, null, opts); },
    post(path, body, opts) { return this.req("POST",   path, body, opts); },
    put(path, body, opts)  { return this.req("PUT",    path, body, opts); },
    patch(path, body, opts){ return this.req("PATCH",  path, body, opts); },
    del(path, opts)        { return this.req("DELETE", path, null, opts); },

    /* Verify the bearer key by hitting /node/auth/verify. */
    async verify() {
      return this.post("/node/auth/verify", {});
    },

    /* Stream a POST body as Server-Sent Events.
     *
     *   onEvent(parsedJson | rawText) — called per event.
     *   onError(err)                  — called on network/parse errors.
     *   onDone()                      — called when stream ends.
     *
     * Returns an AbortController so callers can cancel mid-stream. */
    sse(path, body, handlers) {
      const ctrl = new AbortController();
      const init = {
        method: "POST",
        headers: this.headers({ "Content-Type": "application/json", "Accept": "text/event-stream" }),
        body: JSON.stringify(body || {}),
        signal: ctrl.signal,
      };
      (async () => {
        try {
          const res = await fetch(this.url(path), init);
          if (!res.ok || !res.body) {
            const t = await res.text().catch(() => "");
            throw new Error("SSE failed " + res.status + ": " + t);
          }
          const reader = res.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            // Split by SSE separator (blank line)
            let idx;
            while ((idx = buf.indexOf("\n\n")) !== -1) {
              const block = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              const data = block
                .split("\n")
                .filter(l => l.startsWith("data:"))
                .map(l => l.slice(5).trim())
                .join("\n");
              if (!data) continue;
              if (data === "[DONE]") { handlers.onDone && handlers.onDone(); continue; }
              let parsed;
              try { parsed = JSON.parse(data); }
              catch { parsed = { _raw: data }; }
              try { handlers.onEvent && handlers.onEvent(parsed); }
              catch (e) { handlers.onError && handlers.onError(e); }
            }
          }
          handlers.onDone && handlers.onDone();
        } catch (e) {
          if (e.name === "AbortError") return;
          handlers.onError && handlers.onError(e);
        }
      })();
      return ctrl;
    },
  };

  API.init();
  window.API = API;
})();
