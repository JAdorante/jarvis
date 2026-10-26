"""The tools the agent is allowed to call. There is no shell tool."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from jarvis.devices.roku import RokuError
from jarvis.memory.extract import extract_fact
from jarvis.tools.registry import Tool, ToolResult

USER_AGENT = "JarvisPi/1.0 (home assistant on a Raspberry Pi)"

# Longer names have to come first so "youtube tv" wins over "youtube".
APP_CATALOG = (
    ("youtube tv", "195316", "YouTube TV", ""),
    ("disney plus", "291097", "Disney Plus", ""),
    ("paramount plus", "31440", "Paramount Plus", ""),
    ("apple tv", "551012", "Apple TV", ""),
    ("discovery plus", "593290", "discovery+", ""),
    ("discovery+", "593290", "discovery+", ""),
    ("roku channel", "151908", "The Roku Channel", ""),
    ("peacock", "593099", "Peacock", ""),
    ("hbo max", "61322", "HBO Max", ""),
    ("hbo", "61322", "HBO Max", ""),
    ("max", "61322", "HBO Max", ""),
    ("netflix", "12", "Netflix", ""),
    ("prime video", "13", "Prime Video", ""),
    ("prime", "13", "Prime Video", ""),
    ("hulu", "2285", "Hulu", ""),
    ("youtube", "837", "YouTube", ""),
    ("spotify", "22297", "Spotify", ""),
    ("live tv", "tvinput.dtv", "Live TV", ""),
    ("the sopranos", "61322", "HBO Max", "I can open HBO Max. I cannot choose the episode."),
    ("sopranos", "61322", "HBO Max", "I can open HBO Max. I cannot choose the episode."),
)


def person_from_text(text):
    match = re.search(r"directed by ([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})", text)
    return match.group(1) if match else ""


def wikipedia_fetch(query):
    search_url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 1,
            "format": "json",
        }
    )
    found = _get_json(search_url)
    hits = found.get("query", {}).get("search", [])
    if not hits:
        return ""
    title = hits[0]["title"]
    extract_url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": title,
            "format": "json",
        }
    )
    page = _get_json(extract_url)
    extract = ""
    for item in page.get("query", {}).get("pages", {}).values():
        extract = item.get("extract", "")
    extract = re.sub(r"\s+", " ", extract).strip()
    if not extract:
        return ""
    return f"{title}: {extract[:900]}"


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def live_weather(place, day):
    """Forecast from Open-Meteo. Temperatures are Fahrenheit."""
    geo = _get_json(
        "https://geocoding-api.open-meteo.com/v1/search?"
        + urllib.parse.urlencode({"name": place, "count": 1, "language": "en", "format": "json"})
    )
    results = geo.get("results") or []
    if not results:
        return ""
    found = results[0]
    forecast = _get_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": found["latitude"],
                "longitude": found["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "forecast_days": 2,
                "timezone": "auto",
                "temperature_unit": "fahrenheit",
            }
        )
    )
    daily = forecast.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return ""
    index = 1 if day == "tomorrow" and len(dates) > 1 else 0
    high = round(daily["temperature_2m_max"][index])
    low = round(daily["temperature_2m_min"][index])
    label = found.get("name") or place
    return f"{label} {day}: {_sky(daily['weather_code'][index])}, high {high} degrees, low {low} degrees."


def _sky(code):
    code = int(code)
    if code == 0:
        return "clear"
    if code <= 3:
        return "partly cloudy"
    if code <= 48:
        return "foggy"
    if code <= 67 or 80 <= code <= 82:
        return "rainy"
    if code <= 77:
        return "snowy"
    return "stormy"


def build_tools(store, roku, wiki_fetch=None, legacy_user_file=None, weather_fetch=None):
    fetch = wiki_fetch or wikipedia_fetch
    forecast = weather_fetch or live_weather

    def wikipedia(arguments, context):
        query = (arguments.get("query") or "").strip()
        if arguments.get("follow") == "filmography":
            blob = " ".join(item.get("summary", "") for item in context.get("previous", []))
            name = person_from_text(blob)
            if not name:
                return ToolResult(
                    ok=False,
                    output={"summary": blob},
                    speech="I could not tell who that was, so I did not look up their other movies.",
                    error_code="missing_subject",
                    verified=False,
                )
            query = f"{name} filmography"
        try:
            summary = fetch(query)
        except (OSError, urllib.error.URLError, ValueError, KeyError) as exc:
            return ToolResult(
                ok=False,
                output={"query": query},
                speech=f"I could not reach Wikipedia. {exc}",
                error_code="lookup_failed",
                retryable=True,
                verified=False,
            )
        if not summary:
            return ToolResult(
                ok=False,
                output={"query": query, "summary": ""},
                speech="No Wikipedia page matched that search.",
                error_code="not_found",
                verified=False,
            )
        short = _sentences(summary, 2)
        return ToolResult(
            ok=True,
            output={"query": query, "summary": summary},
            speech=short,
            verified=True,
        )

    def remember(arguments, context):
        fact = (arguments.get("fact") or "").strip()
        if not fact:
            return ToolResult(ok=False, output={}, speech="I did not catch what to remember.", error_code="invalid_arguments")
        memory_id, created = store.add_memory(
            fact,
            "semantic",
            arguments.get("provenance") or "explicit_user_statement",
            kind="preference" if "like" in fact.lower() or "prefer" in fact.lower() else "fact",
            confidence=float(arguments.get("confidence") or 1),
        )
        if legacy_user_file is not None:
            store.append_legacy_user_line(legacy_user_file, fact)
        speech = "I'll remember that." if created else "I already know that."
        return ToolResult(ok=True, output={"id": memory_id, "fact": fact, "created": created}, speech=speech, verified=True)

    def recall(arguments, context):
        query = arguments.get("query") or ""
        rows = store.search(query, limit=5, category="semantic")
        if not rows:
            rows = store.list_memories(category="semantic", limit=8)
        if not rows:
            return ToolResult(
                ok=True,
                output={"memories": []},
                speech="I don't have anything saved about you yet.",
                verified=True,
            )
        lines = [row["content"] for row in rows]
        return ToolResult(
            ok=True,
            output={"memories": lines},
            speech=" ".join(lines),
            verified=True,
        )

    def forget(arguments, context):
        query = arguments.get("query") or ""
        count = store.forget_matching(query)
        if count == 0:
            speech = "I did not find a saved fact that matches that."
        else:
            speech = f"I forgot {count} saved fact{'s' if count != 1 else ''}."
        return ToolResult(ok=True, output={"forgotten": count}, speech=speech, verified=True)

    def remind(arguments, context):
        text = arguments.get("text") or ""
        due_at = arguments.get("due_at") or ""
        reminder_id = store.add_reminder(text, due_at)
        return ToolResult(
            ok=True,
            output={"id": reminder_id, "due_at": due_at, "text": text},
            speech=f"I'll remind you to {text}.",
            verified=True,
        )

    def power(arguments, context):
        turn_on = arguments.get("on") in (True, "on", "true", "True", 1)
        try:
            result = roku.power(turn_on)
        except RokuError as exc:
            return ToolResult(
                ok=False,
                output={},
                speech="I could not reach the TV.",
                error_code="roku_unreachable",
                retryable=exc.retryable,
                verified=False,
            )
        label = "on" if turn_on else "off"
        if result.get("verified"):
            speech = f"The TV is {label}."
        elif result.get("submitted"):
            speech = f"I sent the power command. The TV did not confirm it is {label}."
        else:
            speech = "I could not change the TV power."
        return ToolResult(
            ok=bool(result.get("submitted")),
            output=result,
            speech=speech,
            verified=bool(result.get("verified")),
            error_code="" if result.get("verified") else "unverified",
        )

    def launch(arguments, context):
        app_id = str(arguments.get("app_id") or "")
        label = arguments.get("label") or "that app"
        note = arguments.get("note") or ""
        try:
            result = roku.launch(app_id)
        except RokuError as exc:
            return ToolResult(
                ok=False,
                output={"label": label},
                speech="I could not reach the TV.",
                error_code="roku_unreachable",
                retryable=exc.retryable,
                verified=False,
            )
        active = result.get("active_name") or "something else"
        if result.get("verified"):
            speech = f"Opened {label}. The TV reports {active} is active."
        else:
            speech = f"I asked the TV to open {label}, but it still reports {active or 'a different app'}."
        if note:
            speech = f"{speech} {note}"
        return ToolResult(
            ok=bool(result.get("submitted")),
            output={**result, "label": label},
            speech=speech,
            verified=bool(result.get("verified")),
            error_code="" if result.get("verified") else "unverified",
        )

    def volume(arguments, context):
        key = arguments.get("key") or ""
        try:
            roku.keypress(key)
        except RokuError as exc:
            return ToolResult(
                ok=False,
                output={},
                speech="I could not reach the TV.",
                error_code="roku_unreachable",
                retryable=exc.retryable,
                verified=False,
            )
        spoken = {"VolumeUp": "volume up", "VolumeDown": "volume down", "VolumeMute": "mute"}.get(key, key)
        return ToolResult(
            ok=True,
            output={"key": key},
            speech=f"I sent {spoken}. This TV does not report the new volume back to me.",
            verified=None,
        )

    def weather(arguments, context):
        place = arguments.get("place") or ""
        day = arguments.get("day") or "today"
        try:
            summary = forecast(place, day)
        except (OSError, urllib.error.URLError, ValueError, KeyError, IndexError) as exc:
            return ToolResult(
                ok=False,
                output={"place": place},
                speech=f"I could not get the weather. {exc}",
                error_code="lookup_failed",
                retryable=True,
                verified=False,
            )
        if not summary:
            return ToolResult(
                ok=False,
                output={"place": place},
                speech=f"I could not find a forecast for {place}.",
                error_code="not_found",
                verified=False,
            )
        return ToolResult(ok=True, output={"summary": summary, "place": place}, speech=summary, verified=True)

    def schedule_routine(arguments, context):
        routine_id = store.add_routine(
            arguments.get("name") or "routine",
            arguments["action"],
            arguments.get("argument") or "",
            arguments["hour"],
            arguments["minute"],
        )
        return ToolResult(
            ok=True,
            output={"id": routine_id},
            speech=arguments.get("speech") or "I saved that schedule.",
            verified=True,
        )

    registry_tools = [
        Tool("web.wikipedia", "Look up a short Wikipedia extract.", "L0", "read", 20, True, (), wikipedia),
        Tool("memory.remember", "Store one fact the user stated.", "L0", "write", 5, True, ("fact",), remember),
        Tool("memory.recall", "Read saved facts about the user.", "L0", "read", 5, True, (), recall),
        Tool("memory.forget", "Forget saved facts that match a phrase.", "L1", "write", 5, False, ("query",), forget),
        Tool("time.remind", "Save a one-time reminder.", "L1", "write", 5, False, ("text", "due_at"), remind),
        Tool("roku.power", "Turn the Roku TV on or off and read power state.", "L1", "device", 8, True, ("on",), power),
        Tool("roku.launch", "Launch a Roku app and read the active app.", "L1", "device", 8, True, ("app_id",), launch),
        Tool("roku.volume", "Send a volume key. The TV does not confirm the level.", "L1", "device", 8, True, ("key",), volume),
        Tool("weather.forecast", "Look up today's or tomorrow's forecast for a city.", "L0", "read", 20, True, ("place",), weather),
        Tool("schedule.routine", "Save a daily local-time action.", "L1", "write", 5, True, ("action", "hour", "minute"), schedule_routine),
    ]
    from jarvis.tools.registry import Registry

    registry = Registry()
    for tool in registry_tools:
        registry.add(tool)
    return registry


def _sentences(text, count):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:count])[:500]
