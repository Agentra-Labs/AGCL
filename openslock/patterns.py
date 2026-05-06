"""
Tracks when the user actually uses the agent (hour of day, day of week).
After MIN_PATTERN_DAYS of data, hours that show consistent use become
"active hours". Registered callbacks fire during active hours on a 1-min tick.

Patterns survive restarts because they're stored on disk.
"""

import json
import os
import time
import threading
from collections import defaultdict
from openslock.config import STATE_DIR, MIN_PATTERN_DAYS, PATTERN_LOOKBACK_DAYS

PATTERN_FILE = os.path.join(STATE_DIR, "patterns.json")

_data      = defaultdict(list)   # hour (int) -> [unix timestamps]
_callbacks = []                  # fn(hour, hit_count) called on active hour
_lock      = threading.Lock()


#  persistence 

def load():
    global _data
    if os.path.exists(PATTERN_FILE):
        with open(PATTERN_FILE) as f:
            raw = json.load(f)
            _data = defaultdict(list, {int(k): v for k, v in raw.items()})

def save():
    with _lock:
        tmp = PATTERN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(dict(_data), f)
        os.replace(tmp, PATTERN_FILE)


#  recording 

def record_usage():
    """Call once per user request."""
    hour   = time.localtime().tm_hour
    cutoff = time.time() - PATTERN_LOOKBACK_DAYS * 86400
    with _lock:
        _data[hour].append(time.time())
        _data[hour] = [t for t in _data[hour] if t > cutoff]   # prune old
    save()


#  analysis 

def active_hours():
    """
    Returns dict of hour -> hit_count for hours that have data across
    at least MIN_PATTERN_DAYS distinct days.
    """
    result = {}
    with _lock:
        for hour, timestamps in _data.items():
            distinct_days = len({time.strftime("%Y-%m-%d", time.localtime(t)) for t in timestamps})
            if distinct_days >= MIN_PATTERN_DAYS:
                result[hour] = len(timestamps)
    return result

def upcoming_active_hours(lookahead_hours=3):
    """Hours in the next lookahead_hours that are predicted active."""
    current = time.localtime().tm_hour
    active  = set(active_hours().keys())
    return [h % 24 for h in range(current, current + lookahead_hours) if h % 24 in active]


#  reactive triggers 

def register_trigger(fn):
    """fn(hour, hit_count) — called when current hour is an active hour."""
    _callbacks.append(fn)

def _fire_triggers():
    now_hour = time.localtime().tm_hour
    active   = active_hours()
    if now_hour in active:
        for fn in _callbacks:
            try:
                fn(now_hour, active[now_hour])
            except Exception as e:
                print(f"[patterns] trigger error: {e}")

def start_scheduler():
    """Background thread that ticks every minute and fires triggers."""
    def loop():
        while True:
            time.sleep(60)
            _fire_triggers()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
