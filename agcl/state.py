"""
Handles all in-memory session state with disk fallback.
Evicts idle sessions to disk so RAM stays lean.
The local model unloads separately (see local_llm.py) but this
module is the single source of truth for whether the system is idle.
"""

import json
import os
import time
import threading
from agcl.config import STATE_DIR, IDLE_FLUSH_SEC, SESSION_TTL_SEC

_sessions = {}       # session_id -> {messages, created_at, last_active}
_lock     = threading.Lock()
_last_active = time.time()


#  disk helpers 

def _session_path(sid):
    return os.path.join(STATE_DIR, f"sess_{sid}.json")

def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)   # atomic on POSIX

def _read(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


#  public API 

def touch():
    """Call on any user activity."""
    global _last_active
    _last_active = time.time()

def idle_seconds():
    return time.time() - _last_active

def is_idle():
    return idle_seconds() > IDLE_FLUSH_SEC

def get_session(sid):
    with _lock:
        if sid not in _sessions:
            saved = _read(_session_path(sid))
            _sessions[sid] = saved if saved else {
                "messages":   [],
                "created_at": time.time(),
                "last_active": time.time(),
            }
        return _sessions[sid]

def put_session(sid, sess):
    with _lock:
        sess["last_active"] = time.time()
        _sessions[sid] = sess

def flush_session(sid):
    with _lock:
        if sid in _sessions:
            _write(_session_path(sid), _sessions[sid])

def evict_idle_sessions():
    """Flush then drop sessions older than SESSION_TTL_SEC. Returns count evicted."""
    now = time.time()
    evicted = []
    with _lock:
        for sid, sess in list(_sessions.items()):
            if now - sess.get("last_active", 0) > SESSION_TTL_SEC:
                _write(_session_path(sid), sess)
                evicted.append(sid)
        for sid in evicted:
            del _sessions[sid]
    return len(evicted)

def flush_all():
    with _lock:
        for sid, sess in _sessions.items():
            _write(_session_path(sid), sess)

def session_list():
    with _lock:
        return list(_sessions.keys())
