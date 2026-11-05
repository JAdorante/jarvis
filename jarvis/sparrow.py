"""Sparrow client: ask (MCP), share (external capture), listen (phone outbox).

Everything Sparrow returns is data to be spoken. Nothing here turns a response
into a tool call, a plan step, or a permission decision.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jarvis.observe import log_event

USER_AGENT = "JarvisPi/1.0 (Sparrow client)"
DEFAULT_URL = "https://sparrow.ravenry.us"
SPEAKABLE_DISCLOSURE = {"", "offer", "allow", "disclose"}
LOCAL_ONLY = re.compile(
    r"\b(?:remember|forget|password|passcode|passphrase|door code|pin (?:code|number)|social security)\b",
    re.I,
)


@dataclass
class SparrowSettings:
    enabled: bool = False
    base_url: str = DEFAULT_URL
    mcp_token_file: Path = Path.home() / "jarvis" / "sparrow_mcp_token"
    device_token_file: Path = Path.home() / "jarvis" / "sparrow_device_token"
    device_id: str = "jarvis-pi"
    timeout_seconds: float = 10.0
    share_transcripts: bool = False
    poll_outbox: bool = False
    poll_seconds: float = 60.0


def load_sparrow_settings(raw):
    raw = raw or {}
    home = Path.home()

    def path(key, fallback):
        text = (raw.get(key) or "").strip()
        return Path(text).expanduser() if text else fallback

    url = os.environ.get("JARVIS_SPARROW_URL") or raw.get("base_url") or DEFAULT_URL
    return SparrowSettings(
        enabled=bool(raw.get("enabled", False)),
        base_url=url.rstrip("/"),
        mcp_token_file=path("mcp_token_file", home / "jarvis" / "sparrow_mcp_token"),
        device_token_file=path("device_token_file", home / "jarvis" / "sparrow_device_token"),
        device_id=raw.get("device_id") or "jarvis-pi",
        timeout_seconds=float(raw.get("timeout_seconds", 10)),
        share_transcripts=bool(raw.get("share_transcripts", False)),
        poll_outbox=bool(raw.get("poll_outbox", False)),
        poll_seconds=float(raw.get("poll_seconds", 60)),
    )


def read_token(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_token(path, token):
    """Create the file with mode 600, so the token is never readable by others."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would forward the bearer token to wherever a redirect points."""

    def redirect_request(self, *args, **kwargs):
        return None


def _secure_url(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" or parts.hostname in ("localhost", "127.0.0.1", "::1")


class SparrowError(Exception):
    def __init__(self, message, status=0, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class SparrowClient:
    """Thin HTTPS client. `opener` is injectable so tests use a stand-in server."""

    def __init__(self, settings, opener=None, logger=None):
        self.settings = settings
        self.opener = opener or urllib.request.build_opener(_NoRedirect).open
        self.logger = logger

    @property
    def base_url(self):
        return self.settings.base_url

    def mcp_token(self):
        return read_token(self.settings.mcp_token_file)

    def device_token(self):
        return read_token(self.settings.device_token_file)

    def _request(self, method, path, token, body=None):
        if not _secure_url(self.base_url):
            raise SparrowError("the Sparrow address must use https")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("error") or ""
            except Exception:
                detail = ""
            raise SparrowError(detail or f"HTTP {exc.code}", status=exc.code, retryable=exc.code >= 500) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise SparrowError(str(reason), retryable=True) from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise SparrowError("Sparrow sent a reply I could not read.") from None

    # ASK -----------------------------------------------------------------
    def call_tool(self, name, arguments):
        token = self.mcp_token()
        if not token:
            raise SparrowError("no MCP token is configured", status=401)
        return self._request("POST", "/mcp/tool", token, {"name": name, "arguments": arguments or {}})

    def list_tools(self):
        return self._request("GET", "/mcp/tools", self.mcp_token())

    # SHARE ---------------------------------------------------------------
    def share(self, text, started_at=None, ended_at=None):
        token = self.device_token()
        if not token or not text.strip():
            return False
        body = {
            "text": text.strip(),
            "kind": "external",
            "device_id": self.settings.device_id,
            "confidence": 0.8,
        }
        if started_at is not None:
            body["started_at"] = int(started_at)
        if ended_at is not None:
            body["ended_at"] = int(ended_at)
        self._request("POST", "/capture/external", token, body)
        return True

    def share_later(self, text, started_at=None, ended_at=None):
        """Background send. Never raises into the caller. No token means no request."""
        if not self.device_token():
            return None

        def run():
            try:
                self.share(text, started_at, ended_at)
            except Exception as exc:
                log_event(self.logger, event="sparrow_share_failed", error=str(exc))

        thread = threading.Thread(target=run, name="jarvis-sparrow-share", daemon=True)
        thread.start()
        return thread

    # LISTEN --------------------------------------------------------------
    def outbox(self, peek=False):
        token = self.device_token()
        if not token:
            raise SparrowError("no device token is configured", status=401)
        path = "/phone/outbox?peek=1" if peek else "/phone/outbox"
        data = self._request("GET", path, token)
        items = data.get("items") if isinstance(data, dict) else data
        return [item for item in (items or []) if isinstance(item, dict)]

    # PAIRING -------------------------------------------------------------
    def pair(self, code):
        data = self._request("POST", "/phone/pair/claim", "", {"code": code, "name": "Jarvis", "platform": "other"})
        token = data.get("token") or data.get("device_token") or ""
        if not token:
            raise SparrowError(data.get("error") or "Sparrow did not return a device token.")
        save_token(self.settings.device_token_file, token)
        return data.get("device_id") or data.get("id") or self.settings.device_id


# --------------------------------------------------------------------------
# Intent matching for ASK. Only called when Sparrow is enabled.


def _norm(text):
    lowered = text.lower().replace("’", "'")
    lowered = re.sub(r"\b(whats|what's)\b", "what is", lowered)
    lowered = re.sub(r"\b(wheres|where's)\b", "where is", lowered)
    lowered = re.sub(r"\b(hows|how's)\b", "how is", lowered)
    return lowered.strip(" .?!")


def match_sparrow_intent(cleaned):
    """Return (tool, arguments) or None. `cleaned` already has the wake word stripped."""
    lowered = _norm(cleaned)
    if re.search(r"\bwhat do you remember\b", lowered):
        return None

    match = re.search(r"\b(?:ask sparrow|search (?:my )?(?:notes|sparrow)(?: for)?)\s+(.+)$", lowered)
    if match:
        return ("sparrow.search", {"query": match.group(1).strip(" .?!")})

    match = re.search(r"\b(?:what do i owe|what is (?:still )?open with|open items with|anything open with)\s+(.+)$", lowered)
    if match:
        return ("sparrow.open_loops", {"person": _subject(match.group(1))})

    if re.search(r"\b(what is on my plate|my open commitments|my open loops|what do i owe)\b|^what is open$", lowered):
        return ("sparrow.open_loops", {})

    match = re.search(r"\b(?:brief me on|what do we know about|catch me up on)\s+(.+)$", lowered)
    if match:
        return ("sparrow.brief", {"subject": _subject(match.group(1))})

    if re.search(r"\bwhat did (?:we|i|[a-z]+) (?:decide|say|agree|promise)\b", lowered):
        return ("sparrow.search", {"query": cleaned.strip(" .?!")})
    return None


def _subject(text):
    text = text.strip(" .?!")
    text = re.sub(r"^(the|about|with)\s+", "", text, flags=re.I)
    return text


# --------------------------------------------------------------------------
# Speech templates. The model never touches these.


def _clip(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _item_text(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("text", "title", "summary", "content", "description", "name"):
            if item.get(key):
                return str(item[key])
    return str(item)


def _items(result, *keys):
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def _may_speak(item):
    """Sparrow labels what it may disclose. Only say what is cleared for offering."""
    if not isinstance(item, dict):
        return True
    action = str(item.get("disclosure_action") or "").lower()
    return action in SPEAKABLE_DISCLOSURE


def _withheld_note(items):
    held = sum(1 for item in items if not _may_speak(item))
    if not held:
        return ""
    return f" I held back {held} item{'s' if held != 1 else ''} Sparrow marked as private."


def speak_open_loops(result, person=""):
    items = _items(result, "items", "loops", "open_loops", "results")
    subject = f" with {person}" if person else ""
    if not items:
        return f"Sparrow has nothing open{subject}."
    count = len(items)
    head = f"{count} open item{'s' if count != 1 else ''}{subject}."
    lines = [_clip(_item_text(item), 120) for item in items if _may_speak(item)][:3]
    return " ".join([head] + [line for line in lines if line]) + _withheld_note(items)


def speak_search(result, query=""):
    items = _items(result, "results", "items", "matches", "episodes")
    if not items:
        return f"Sparrow found nothing about {query or 'that'}."
    lines = [_clip(_item_text(item), 160) for item in items if _may_speak(item)][:2]
    if not lines:
        return f"Sparrow found something about {query or 'that'}, but it is marked private, so I won't read it aloud."
    return "From Sparrow: " + " ".join(line for line in lines if line) + _withheld_note(items)


def speak_person(result, subject):
    person = result.get("person") if isinstance(result.get("person"), dict) else result
    name = person.get("name") or subject
    if not _may_speak(person):
        return f"Sparrow has {name} marked private, so I won't read that aloud."
    parts = [f"{name}."]
    affiliations = _items(person, "affiliations", "organizations", "orgs")
    if affiliations:
        parts.append("Affiliated with " + ", ".join(_item_text(item) for item in affiliations[:3]) + ".")
    loops = [item for item in (_items(result, "open_loops") or _items(person, "open_loops")) if _may_speak(item)]
    if loops:
        parts.append("First open item: " + _clip(_item_text(loops[0]), 120))
    with_whom = _items(result, "co_occurs_with", "related_people") or _items(person, "co_occurs_with", "related_people")
    if with_whom:
        parts.append("Usually comes up with " + ", ".join(_item_text(item) for item in with_whom[:3]) + ".")
    return " ".join(parts)


def speak_org(result, subject):
    org = result.get("org") if isinstance(result.get("org"), dict) else result
    name = org.get("name") or subject
    if not _may_speak(org):
        return f"Sparrow has {name} marked private, so I won't read that aloud."
    linked = _items(org, "linked_items", "items") or _items(result, "items")
    count = org.get("item_count") if isinstance(org.get("item_count"), int) else len(linked)
    return f"{name}: {count} linked item{'s' if count != 1 else ''} in Sparrow."


def _person_found(result):
    if not result or result.get("found") is False:
        return False
    if "person" in result and not result.get("person"):
        return False
    person = result.get("person") if isinstance(result.get("person"), dict) else result
    return bool(person.get("name") or _items(person, "affiliations") or _items(result, "open_loops"))


def _org_found(result):
    if not result or result.get("found") is False:
        return False
    org = result.get("org") if isinstance(result.get("org"), dict) else result
    return bool(org.get("name"))


# --------------------------------------------------------------------------
# Tool registration


def _failure(exc):
    from jarvis.tools.registry import ToolResult

    if exc.status == 401:
        return ToolResult(ok=False, output={}, speech="Sparrow rejected the token.", error_code="sparrow_unauthorized", retryable=False, verified=False)
    if exc.status:
        return ToolResult(ok=False, output={"status": exc.status}, speech=f"Sparrow answered {exc.status}.", error_code="sparrow_http", retryable=True, verified=False)
    return ToolResult(ok=False, output={}, speech=f"I could not reach Sparrow. {exc}", error_code="sparrow_unreachable", retryable=True, verified=False)


def _unwrap(data):
    """Sparrow wraps tool output as {ok, result} or {ok: false, error}."""
    if not isinstance(data, dict):
        return {}, ""
    if data.get("ok") is False:
        return {}, data.get("error") or "Sparrow reported an error."
    inner = data.get("result", data)
    return (inner if isinstance(inner, dict) else {"items": inner}), ""


def build_sparrow_tools(client):
    from jarvis.tools.registry import Tool, ToolResult

    def run(name, arguments):
        try:
            data = client.call_tool(name, arguments)
        except SparrowError as exc:
            return None, _failure(exc)
        inner, error = _unwrap(data)
        if error:
            return None, ToolResult(ok=False, output={"error": error}, speech=error, error_code="sparrow_tool_error", retryable=True, verified=False)
        return inner, None

    def open_loops(arguments, context):
        person = (arguments.get("person") or "").strip()
        inner, failure = run("open_loops", {"person": person} if person else {})
        if failure:
            return failure
        return ToolResult(ok=True, output={"result": inner}, speech=speak_open_loops(inner, person), verified=True)

    def search(arguments, context):
        query = (arguments.get("query") or "").strip()
        inner, failure = run("memory_search", {"query": query, "limit": 5})
        if failure:
            return failure
        return ToolResult(ok=True, output={"result": inner}, speech=speak_search(inner, query), verified=True)

    def brief(arguments, context):
        subject = (arguments.get("subject") or "").strip()
        inner, person_failure = run("person_context", {"name": subject})
        if person_failure is None and _person_found(inner):
            return ToolResult(ok=True, output={"result": inner, "kind": "person"}, speech=speak_person(inner, subject), verified=True)
        if person_failure is not None and person_failure.error_code != "sparrow_tool_error":
            return person_failure
        inner, org_failure = run("org_brief", {"name": subject})
        if org_failure is None and _org_found(inner):
            return ToolResult(ok=True, output={"result": inner, "kind": "org"}, speech=speak_org(inner, subject), verified=True)
        # An error is not an empty answer. Say "nothing" only when both lookups really came back empty.
        if org_failure is not None or person_failure is not None:
            return org_failure or person_failure
        return ToolResult(ok=True, output={"result": inner or {}}, speech=f"Sparrow has nothing on {subject}.", verified=True)

    return [
        Tool("sparrow.open_loops", "Read open commitments from Sparrow.", "L0", "read", 15, True, (), open_loops),
        Tool("sparrow.search", "Search Sparrow memory.", "L0", "read", 15, True, ("query",), search),
        Tool("sparrow.brief", "Brief on a person or company from Sparrow.", "L0", "read", 15, True, ("subject",), brief),
    ]


# --------------------------------------------------------------------------
# LISTEN


def speak_outbox_item(item):
    text = _clip(_item_text(item), 300)
    if not text:
        return ""
    kind = str(item.get("kind") or "notify").lower()
    if kind == "reminder":
        return f"Sparrow reminder: {text}"
    return f"From Sparrow: {text}"


def stays_local(cleaned, result=None):
    """Sentences SHARE must not send: local memory, replies to Jarvis, and likely secrets."""
    from jarvis.agent.planning import is_no, is_yes
    from jarvis.memory.extract import extract_fact

    if is_yes(cleaned) or is_no(cleaned):
        return True
    if LOCAL_ONLY.search(cleaned) or extract_fact(cleaned) != "":
        return True
    tools = [item.tool for item in (result or {}).get("results", [])]
    return any(name.startswith("memory.") for name in tools)


class OutboxPoller:
    """Polls on the scheduler thread. Items are spoken, never executed.

    Sparrow marks items delivered when it returns them, so one Jarvis could not
    say is held here and tried again on the next poll.
    """

    def __init__(self, client, speaker, poll_seconds=60, logger=None, clock=None):
        self.client = client
        self.speaker = speaker
        self.poll_seconds = float(poll_seconds)
        self.logger = logger
        self.clock = clock or time.monotonic
        self.last_poll = None
        self.unspoken = []

    def tick(self, quiet=False):
        if quiet:
            return []
        now = self.clock()
        if self.last_poll is not None and now - self.last_poll < self.poll_seconds:
            return []
        self.last_poll = now
        try:
            items = self.client.outbox()
        except Exception as exc:
            log_event(self.logger, event="sparrow_outbox_failed", error=str(exc))
            items = []
        spoken = []
        waiting = self.unspoken + [speech for speech in map(speak_outbox_item, items) if speech]
        self.unspoken = []
        for speech in waiting:
            try:
                delivered = self.speaker(speech)
            except Exception as exc:
                log_event(self.logger, event="sparrow_outbox_unspoken", error=str(exc))
                delivered = False
            if delivered is False:
                self.unspoken.append(speech)
                continue
            spoken.append(speech)
        return spoken
