"""Turn one utterance into a bounded plan. Simple commands skip the model."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from jarvis.memory.extract import extract_fact
from jarvis.tools.builtin import APP_CATALOG

NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "ten": 10,
    "fifteen": 15,
    "thirty": 30,
}


@dataclass
class Step:
    tool: str
    arguments: dict
    required: bool = True


@dataclass
class Plan:
    kind: str
    objective: str
    steps: list = field(default_factory=list)
    speech: str = ""
    approval: Optional[dict] = None


def strip_wake(text):
    cleaned = re.sub(r"^\s*jar\w*[,\s]+", "", text, count=1, flags=re.I).strip()
    return cleaned or text.strip()


def is_cancel(text):
    lowered = text.lower().strip(" .!")
    if lowered in {"stop", "cancel", "cancel that", "never mind", "nevermind"}:
        return True
    return bool(re.search(r"\b(stop what you.?re doing|cancel that)\b", lowered))


def match_app(text):
    lowered = text.lower()
    for phrase, app_id, label, note in APP_CATALOG:
        if phrase in lowered:
            return {"app_id": app_id, "label": label, "note": note, "phrase": phrase}
    return None


CAPABILITIES = (
    "I can open apps on the TV, turn it on or off, and move the volume up, down, or mute. "
    "I can look up a fact, check the weather for a city, remember what you tell me, and set a reminder. "
    "I cannot choose an episode or a profile, and I cannot see what is playing inside an app."
)


def plan_request(text, now=None):
    now = now or datetime.now(timezone.utc)
    cleaned = strip_wake(text)
    lowered = cleaned.lower().strip()
    objective = cleaned

    if is_cancel(cleaned):
        return Plan("cancel", objective, speech="Stopped.")

    if re.search(r"\b(send (an )?e-?mail|email)\b", lowered):
        has_detail = "@" in lowered or re.search(r"\b(saying|that says|about)\b", lowered)
        if not has_detail:
            return Plan(
                "clarify",
                objective,
                speech="Who should I email, and what should the message say?",
            )
        return Plan(
            "approval",
            objective,
            speech="I drafted that email. I will not send it unless you approve this exact message. Email sending is not connected, so nothing has been sent.",
            approval={
                "action": "send email",
                "system": "email",
                "reversible": False,
                "detail": cleaned,
            },
        )

    if re.search(r"\b(forget|don't remember|do not remember)\b", lowered):
        query = re.sub(r"^.*\bforget\b", "", cleaned, count=1, flags=re.I).strip(" .")
        query = re.sub(r"^(my|that|about)\b", "", query, count=1, flags=re.I).strip()
        return Plan("tools", objective, [Step("memory.forget", {"query": query or cleaned})])

    if re.search(r"\b(what do you remember|who am i|what do i like|what kinds? of movies|my preferences)\b", lowered):
        return Plan("tools", objective, [Step("memory.recall", {"query": cleaned})])

    if re.search(r"\b(what are you capable|what can you do|who are you|what are you)\b", lowered):
        return Plan("talk", objective, speech=CAPABILITIES)

    if re.search(r"\bvolume\b", lowered) and re.search(r"\b\d+\b", lowered):
        return Plan(
            "talk",
            objective,
            speech="I can turn the volume up, down, or mute it. I cannot set it to a specific number.",
        )

    weather = _weather(cleaned)
    if weather == "ask":
        return Plan("clarify", objective, speech="Which city should I check?")
    if weather is not None:
        return Plan("tools", objective, [weather])

    reminder = _reminder(cleaned, now)
    if reminder:
        return Plan("tools", objective, [reminder])

    routine = _routine(cleaned)
    if routine:
        return Plan("tools", objective, [routine])

    steps = []
    factual = bool(re.search(r"\b(who|what|when|where|why|how|find out|look up)\b", lowered) or "?" in cleaned)
    if re.search(r"\b(find|tell me about)\b", lowered) and not re.search(r"\b(movie|show)\b", lowered):
        factual = True
    filmography = bool(re.search(r"\b(other movies|filmography|else they directed|what else did they)\b", lowered))
    open_verb = bool(re.search(r"\b(open|launch|start|resume|play|watch|put on)\b", lowered))
    app = match_app(cleaned) if open_verb or re.search(r"\bsopranos\b", lowered) else None
    power = _power(lowered)
    volume = _volume(lowered)
    fact = extract_fact(cleaned)

    if factual and not re.search(r"\b(what do you remember|who am i|what do i like)\b", lowered):
        lookup = re.split(r"\b(?:and remember|remember that)\b", cleaned, maxsplit=1, flags=re.I)[0].strip(" .,")
        if _lookup_useful(lookup):
            steps.append(Step("web.wikipedia", {"query": lookup or cleaned, "follow": ""}))
            if filmography:
                steps.append(Step("web.wikipedia", {"query": cleaned, "follow": "filmography"}))

    if fact and re.search(r"\b(remember|my name is|i like|i love|i prefer|i hate|i usually)\b", lowered):
        confidence = 0.8 if "usually" in lowered else 1.0
        steps.append(
            Step(
                "memory.remember",
                {
                    "fact": fact,
                    "provenance": "explicit_user_statement",
                    "confidence": confidence,
                },
            )
        )

    if power:
        steps.append(Step("roku.power", {"on": power == "on"}))
    if volume:
        steps.append(Step("roku.volume", {"key": volume}))
    limitation = ""
    if app and (open_verb or "sopranos" in lowered):
        steps.append(
            Step(
                "roku.launch",
                {"app_id": app["app_id"], "label": app["label"], "note": app["note"]},
            )
        )
    elif open_verb and "video" in lowered and fact is not None:
        steps.append(
            Step(
                "roku.launch",
                {
                    "app_id": "837",
                    "label": "YouTube",
                    "note": "I cannot choose a specific video.",
                },
            )
        )
    elif open_verb and app is None and fact is not None:
        limitation = (
            "I can open the apps installed on the TV, such as Netflix, YouTube, and HBO Max. "
            "I cannot see profile icons or choose an episode."
        )
        if not steps:
            return Plan("talk", objective, speech=limitation)

    if fact is None and not steps:
        return Plan("talk", objective, speech="I won't store that, because it sounded uncertain.")

    movie_note = ""
    if re.search(r"\b(find|recommend)\b", lowered) and re.search(r"\b(movie|show)\b", lowered) and not steps:
        steps.append(Step("memory.recall", {"query": "movie show like prefer"}))
        movie_note = "I cannot browse the TV apps for titles, so this only uses what you have told me."

    if not steps:
        return Plan("talk", objective, speech=limitation)
    return Plan("tools", objective, steps[:4], speech=limitation or movie_note)


def _lookup_useful(lookup):
    """Skip a search when the leftover words are only 'you' or 'remember that'."""
    ignored = {
        "you", "your", "jarvis", "do", "does", "did", "please", "can", "could", "would",
        "hey", "ok", "so", "remember", "that", "my", "name", "is", "me", "i", "a", "the",
    }
    words = [word for word in re.findall(r"[a-z0-9']+", lookup.lower()) if word not in ignored]
    return len(words) >= 2


def _weather(text):
    lowered = text.lower()
    if "weather" not in lowered:
        return None
    match = re.search(r"\bin ([a-z][a-z .'-]{1,40}?)(?:\s+tomorrow|\s+today|[?.!]|$)", lowered)
    place = match.group(1).strip(" .") if match else ""
    if not place or place in {"the", "a", "my"}:
        return "ask"
    day = "tomorrow" if "tomorrow" in lowered else "today"
    return Step("weather.forecast", {"place": place, "day": day})


def _power(lowered):
    if re.search(r"\b(turn off|power off|shut off|switch off)\b", lowered):
        return "off"
    if re.search(r"\b(turn on|power on|switch on)\b", lowered):
        return "on"
    return ""


def _volume(lowered):
    if re.search(r"\b(mute|unmute)\b", lowered):
        return "VolumeMute"
    if re.search(r"\bvolume (up|higher|louder)\b", lowered):
        return "VolumeUp"
    if re.search(r"\bvolume (down|lower|quieter)\b", lowered):
        return "VolumeDown"
    return ""


def _reminder(text, now):
    match = re.search(
        r"\bremind me in ([a-z0-9]+) (second|minute|hour)s?\b(?: to)?\s*(.*)",
        text,
        flags=re.I,
    )
    if not match:
        return None
    raw_count, unit, rest = match.group(1).lower(), match.group(2).lower(), match.group(3).strip(" .")
    if raw_count.isdigit():
        count = int(raw_count)
    elif raw_count in NUMBER_WORDS:
        count = NUMBER_WORDS[raw_count]
    else:
        return None
    if count <= 0:
        return None
    delta = {"second": timedelta(seconds=count), "minute": timedelta(minutes=count), "hour": timedelta(hours=count)}[unit]
    due = (now + delta).replace(microsecond=0).isoformat()
    subject = rest or "check"
    return Step("time.remind", {"text": subject, "due_at": due})


def _routine(text):
    lowered = text.lower()
    if "turn off" in lowered and "midnight" in lowered:
        return Step(
            "schedule.routine",
            {
                "name": "Midnight TV off",
                "action": "power_off",
                "argument": "",
                "hour": 0,
                "minute": 0,
                "speech": "I'll turn the TV off at midnight.",
            },
        )
    if re.search(r"\bevery morning\b", lowered) and "weather" in lowered:
        return Step(
            "schedule.routine",
            {
                "name": "Morning briefing",
                "action": "speak",
                "argument": "No weather source is configured, so I have no forecast.",
                "hour": 8,
                "minute": 0,
                "speech": "I saved an 8 AM reminder. Say the city as well, for example weather in Boston, or I will not invent a forecast.",
            },
        )
    return None
