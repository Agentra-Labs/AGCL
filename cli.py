#!/usr/bin/env python3
"""
Minimal CLI to talk to the agent.
Usage:
  python cli.py                         # uses session "default"
  python cli.py --session work          # named session
  python cli.py --provider openai       # force provider
  python cli.py --recovery humor        # recovery mode
"""

import argparse
import json
import sys
import httpx

BASE = "http://localhost:8000"

def stream_chat(session_id, message, provider, recovery):
    url  = f"{BASE}/chat/{session_id}"
    data = {"message": message, "provider": provider, "recovery_mode": recovery}

    prefix_shown = False
    with httpx.stream("POST", url, json=data, timeout=60) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])

            t = payload.get("type")
            if t == "prefix":
                ms  = payload.get("local_ms", 0)
                txt = payload.get("text", "")
                print(f"\033[90m[local {ms}ms]\033[0m ", end="", flush=True)
                print(txt, end="", flush=True)
                prefix_shown = True
            elif t == "chunk":
                print(payload.get("text", ""), end="", flush=True)
            elif t == "done":
                p = payload.get("pressure", {})
                print(f"\n\033[90m[pressure rate={p.get('rate_ratio')} lat={p.get('avg_latency_sec')}s]\033[0m")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session",  default="default")
    ap.add_argument("--provider", default=None, choices=["openai", "claude"])
    ap.add_argument("--recovery", default="natural", choices=["natural", "humor", "explicit"])
    args = ap.parse_args()

    print(f"Agent CLI — session={args.session}  (ctrl+c to quit)\n")
    while True:
        try:
            msg = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not msg:
            continue
        print("agent: ", end="", flush=True)
        stream_chat(args.session, msg, args.provider, args.recovery)

if __name__ == "__main__":
    main()
