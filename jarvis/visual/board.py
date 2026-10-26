"""Shared hologram state. The face page and the agent use the same object."""

import queue
import threading

ALLOWED = {
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "acting",
    "speaking",
    "responding",
    "waiting",
    "error",
    "offline",
}

_LOCK = threading.Lock()
STATE = {"state": "idle", "activity": ""}
SUBSCRIBERS = []


def snapshot():
    with _LOCK:
        return dict(STATE)


def publish(state, activity=None):
    if state not in ALLOWED:
        raise ValueError(state)
    with _LOCK:
        STATE["state"] = state
        if activity is not None:
            STATE["activity"] = activity
        payload = dict(STATE)
        subscribers = list(SUBSCRIBERS)
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            pass
    return payload


def subscribe():
    subscriber = queue.Queue(maxsize=8)
    with _LOCK:
        SUBSCRIBERS.append(subscriber)
    return subscriber


def unsubscribe(subscriber):
    with _LOCK:
        if subscriber in SUBSCRIBERS:
            SUBSCRIBERS.remove(subscriber)
