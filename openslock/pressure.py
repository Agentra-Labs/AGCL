"""
Tracks API pressure as a combined score of request rate and avg latency.
Nothing fancy: a sliding window deque + a rolling latency buffer.
Call record() after each cloud call; read pressure() anywhere.
"""

import time
from collections import deque
from openslock.config import PRESSURE_WINDOW_SEC, PRESSURE_LIMIT

_timestamps = deque()       # request finish times inside window
_latencies  = deque(maxlen=50)  # rolling last-50 latencies (seconds)


def _prune():
    cutoff = time.time() - PRESSURE_WINDOW_SEC
    while _timestamps and _timestamps[0] < cutoff:
        _timestamps.popleft()

def record(latency_sec):
    _timestamps.append(time.time())
    _latencies.append(latency_sec)

def pressure():
    _prune()
    req_count   = len(_timestamps)
    rate_ratio  = req_count / max(PRESSURE_LIMIT, 1)
    avg_latency = sum(_latencies) / len(_latencies) if _latencies else 0.0
    score       = min(rate_ratio, 2.0)   # cap at 2x limit
    return {
        "requests_in_window": req_count,
        "rate_ratio":         round(score, 3),
        "avg_latency_sec":    round(avg_latency, 3),
        "high":               score > 0.8,
    }

def is_high():
    return pressure()["high"]
