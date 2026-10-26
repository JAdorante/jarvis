"""Tell the hologram what Jarvis is doing."""

import json
import urllib.error
import urllib.request

from jarvis.visual.board import publish


def set_face(state, activity="", url=""):
    try:
        publish(state, activity)
    except ValueError:
        return
    if not url:
        return
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps({"state": state, "activity": activity}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=0.4).read()
    except (OSError, urllib.error.URLError):
        pass
