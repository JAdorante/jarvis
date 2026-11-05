"""Turn one utterance into a bounded plan. Simple commands skip the model."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from jarvis.memory.extract import extract_fact
from jarvis.tools.builtin import APP_CATALOG

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60,
}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TV_TARGET = re.compile(r"(?:(?:the|my|this|that)\s+)?(?:tv|television|roku|screen|it)\b")
TARGET_FOLLOWERS = re.compile(r"(?:please|now|for me|at)\b")
DEVICE_VERB = re.compile(
    r"\b(?:turn|power|shut|switch)\b.*\b(?:on|off)\b|\b(?:un)?mute\b|\bvolume\b|\b(?:open|launch|start|resume|play|watch|put on)\b"
)
NEGATED = re.compile(r"\b(?:don.?t|do not|never|not)\b")
INFORMATIONAL = re.compile(
    r"^(?:how|why|what|what.?s|when|where|who|which|does|did|is|are|was|were)\b"
)
HOW_TO = re.compile(r"^how (?:do|can|to|would|should)\b")
SMALL_TALK = re.compile(
    r"^(?:how are you|how.?s it going|what.?s up|what are you doing|are you\b|do you (?:like|think|feel)\b"
    r"|what.?s your\b|what is your\b|what do you think\b)"
)
EMAIL_COMMAND = re.compile(
    r"\bsend\b.*\be-?mail\b|^(?:(?:please|can you|could you|would you)\s+)*e-?mail\b"
)
FORGET_COMMAND = re.compile(
    r"^(?:(?:please|just|can you|could you|would you|you can|you should)\s+)*"
    r"(?:forget|(?:don.?t|do not) remember)\b(.*)$",
    re.I,
)
VAGUE_FORGET = {"", "it", "that", "this", "everything", "all", "anything", "something"}
EMAIL_REFUSAL = "Email is not connected, so I cannot send anything. Nothing has been sent or saved."


@dataclass
class Step:
    tool: str
    arguments: dict


@dataclass
class Plan:
    kind: str
    objective: str
    steps: list = field(default_factory=list)
    speech: str = ""
    reply_template: str = ""


def strip_wake(text, wake_word="jarvis"):
    word = re.escape(wake_word)
    cleaned = re.sub(rf"^\s*(?:(?:hey|ok|okay|hi|yo)\s+)?{word}\b[,.!?:;\s]*", "", text, count=1, flags=re.I)
    cleaned = re.sub(rf"[,\s]+{word}[.!?\s]*$", "", cleaned, count=1, flags=re.I).strip()
    return cleaned or text.strip()


def is_cancel(text):
    lowered = text.lower().strip(" .!")
    if lowered in {"stop", "cancel", "cancel that", "never mind", "nevermind"}:
        return True
    return bool(re.search(r"\b(stop what you.?re doing|cancel that)\b", lowered))


def is_yes(text):
    lowered = text.lower().strip(" .,!")
    return lowered in {
        "yes", "yes please", "yeah", "yep", "sure", "ok", "okay", "approve", "approved",
        "do it", "go ahead", "confirm", "confirmed",
    }


def is_no(text):
    lowered = text.lower().strip(" .,!")
    return lowered in {"no", "nope", "no thanks", "no thank you", "don't", "do not", "dont"} or is_cancel(text)


def match_app(text):
    lowered = text.lower()
    for phrase, app_id, label, note in APP_CATALOG:
        if phrase in lowered:
            return {"app_id": app_id, "label": label, "note": note, "phrase": phrase}
    return None


CAPABILITIES = (
    "I can open apps on the TV, turn it on or off, and move the volume up, down, or mute. "
    "I can look up a fact, check the weather for a city, remember what you tell me, and set, list, or cancel a reminder. "
    "I cannot choose an episode or a profile, and I cannot see what is playing inside an app."
)


def plan_request(text, now=None, sparrow=False):
    """`sparrow=True` adds the Sparrow ASK intents; otherwise behavior is unchanged."""
    now = now or datetime.now(timezone.utc)
    cleaned = strip_wake(text)
    lowered = cleaned.lower().strip()
    objective = cleaned

    if is_cancel(cleaned):
        return Plan("cancel", objective, speech="Nothing is running.")

    keep_in_mind = re.search(r"\b(?:don.?t|do not) forget\b(?:\s+to\b)?\s*(.*)", cleaned, flags=re.I)
    if keep_in_mind:
        subject = keep_in_mind.group(1).strip(" .,!?")
        if not subject:
            return Plan("clarify", objective, speech="What should I remind you about, and when?")
        return Plan(
            "clarify",
            objective,
            speech="When should I remind you?",
            reply_template=f"remind me {{answer}} to {subject}",
        )

    forget = _forget(cleaned)
    if forget:
        return forget

    if re.search(r"\b(what do you remember|who am i|what do i like|what kinds? of movies|my preferences)\b", lowered):
        return Plan("tools", objective, [Step("memory.recall", {"query": cleaned})])

    if re.search(r"\byour name\b", lowered):
        return Plan("talk", objective, speech="I'm Jarvis.")

    if re.search(r"\b(what are you capable|what can you do|who are you|what are you)\b", lowered):
        return Plan("talk", objective, speech=CAPABILITIES)

    if sparrow:
        from jarvis.sparrow import match_sparrow_intent

        matched = match_sparrow_intent(cleaned)
        if matched:
            return Plan("tools", objective, [Step(matched[0], matched[1])])

    if EMAIL_COMMAND.search(lowered):
        return Plan("talk", objective, speech=EMAIL_REFUSAL)

    clock = _clock_question(lowered, now)
    if clock:
        return Plan("talk", objective, speech=clock)

    schedule = _schedule_request(lowered)
    if schedule:
        return Plan("tools", objective, [schedule])

    if re.search(r"\bvolume\b", lowered) and re.search(r"\b\d+\b", lowered):
        return Plan(
            "talk",
            objective,
            speech="I can turn the volume up, down, or mute it. I cannot set it to a specific number.",
        )

    routine = _routine(cleaned, objective)
    if isinstance(routine, Plan):
        return routine
    if routine:
        return Plan("tools", objective, [routine])

    weather = _weather(cleaned, objective)
    if isinstance(weather, Plan):
        return weather
    if weather is not None:
        return Plan("tools", objective, [weather])

    reminder = _reminder(cleaned, now, objective)
    if isinstance(reminder, Plan):
        return reminder
    if reminder:
        return Plan("tools", objective, [reminder])

    commands, refused = _command_clauses(lowered)
    command_text = " . ".join(commands)
    open_verb = bool(re.search(r"\b(open|launch|start|resume|play|watch|put on)\b", command_text))
    app = match_app(command_text) if open_verb or re.search(r"\bsopranos\b", command_text) else None
    power = _power(commands)
    volume = _volume(commands)
    device_command = bool(power or volume or app)

    steps = []
    factual = bool(re.search(r"\b(who|what|when|where|why|how|find out|look up)\b", lowered))
    factual = factual or ("?" in cleaned and not device_command)
    if re.search(r"\b(find|tell me about)\b", lowered) and not re.search(r"\b(movie|show)\b", lowered):
        factual = True
    filmography = bool(re.search(r"\b(other movies|filmography|else they directed|what else did they)\b", lowered))
    fact = extract_fact(cleaned)

    if factual and not re.search(r"\b(what do you remember|who am i|what do i like)\b", lowered):
        lookup = re.split(r"\b(?:and remember|remember that)\b", cleaned, maxsplit=1, flags=re.I)[0].strip(" .,")
        if _personal(lookup):
            steps.append(Step("memory.recall", {"query": lookup, "strict": True}))
        elif not SMALL_TALK.search(lowered) and _lookup_useful(lookup):
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
    if app:
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
        if refused:
            return Plan("talk", objective, speech="Okay, I won't.")
        if _other_device(commands):
            return Plan("talk", objective, speech="I can only control the TV, so I did not change anything.")
        return Plan("talk", objective, speech=limitation)
    return Plan("tools", objective, steps[:4], speech=limitation or movie_note)


def _forget(cleaned):
    match = FORGET_COMMAND.match(cleaned.strip())
    if not match:
        return None
    query = match.group(1).strip(" .,!?")
    query = re.sub(r"^(?:about|that|my|the)\b\s*", "", query, count=1, flags=re.I).strip()
    if query.lower() in VAGUE_FORGET:
        return Plan("clarify", cleaned, speech="What should I forget?", reply_template="forget {answer}")
    return Plan("tools", cleaned, [Step("memory.forget", {"query": query})])


def _command_clauses(lowered):
    """Split a sentence into clauses and keep the ones that actually command the TV.

    Returns (commands, refused). A question is not a command, and neither is
    "don't turn off the TV". `refused` is true when the sentence opens by saying not to.
    """
    if HOW_TO.search(lowered):
        return [], False
    commands = []
    refused = False
    for clause in re.split(r"\s*(?:\band\b|\bthen\b|\bbut\b|[,;.!?])\s*", lowered):
        clause = clause.strip()
        if not clause or INFORMATIONAL.search(clause):
            continue
        if NEGATED.search(clause):
            if DEVICE_VERB.search(clause) and re.match(r"(?:please\s+)?(?:don.?t|do not|never)\b", clause):
                refused = True
            continue
        commands.append(clause)
    return commands, refused


def _target_is_tv(rest):
    rest = rest.strip(" .,!?")
    return not rest or bool(TV_TARGET.match(rest) or TARGET_FOLLOWERS.match(rest))


def _power(commands):
    for clause in commands:
        match = re.search(r"\b(?:turn|power|shut|switch) (on|off)\b(.*)$", clause)
        if match and _target_is_tv(match.group(2)):
            return match.group(1)
        match = re.search(r"\b(?:turn|power|shut|switch) (?:(?:the|my)\s+)?(?:tv|television|roku|screen|it) (on|off)\b", clause)
        if match:
            return match.group(1)
    return ""


def _other_device(commands):
    for clause in commands:
        match = re.search(r"\b(?:turn|power|shut|switch) (?:on|off)\b(.*)$", clause)
        if match and not _target_is_tv(match.group(1)):
            return True
        match = re.search(r"\b(?:un)?mute\b(.*)$", clause)
        if match and not _target_is_tv(match.group(1)):
            return True
    return False


def _volume(commands):
    for clause in commands:
        match = re.search(r"\b(?:un)?mute\b(.*)$", clause)
        if match and _target_is_tv(match.group(1)):
            return "VolumeMute"
        if re.search(r"\bvolume (?:up|higher|louder)\b|\bturn (?:it|the tv|the volume) up\b|\b(?:tv|volume|it) louder\b", clause):
            return "VolumeUp"
        if re.search(r"\bvolume (?:down|lower|quieter)\b|\bturn (?:it|the tv|the volume) down\b|\b(?:tv|volume|it) quieter\b", clause):
            return "VolumeDown"
    return ""


def _lookup_useful(lookup):
    """Skip a search when the leftover words are only 'you' or 'remember that'."""
    ignored = {
        "you", "your", "jarvis", "do", "does", "did", "please", "can", "could", "would",
        "hey", "ok", "so", "remember", "that", "my", "name", "is", "me", "i", "a", "the",
    }
    words = [word for word in re.findall(r"[a-z0-9']+", lookup.lower()) if word not in ignored]
    return len(words) >= 2


def _personal(lookup):
    """A question about the user stays on the Pi. It is never sent to Wikipedia."""
    lowered = lookup.lower()
    if HOW_TO.search(lowered):
        return False
    return bool(re.search(r"\b(?:my|mine|our|ours)\b|\b(?:did|do|am|was|have|should) (?:i|we)\b", lowered))


def _clock_question(lowered, now):
    local = now.astimezone()
    if re.search(r"\bwhat time is it\b|\bwhat.?s the time\b|\bwhat is the time\b|\bdo you have the time\b", lowered):
        # This clock only knows the time here, not in Tokyo.
        if re.search(r"\b(?:in|at) [a-z]", lowered):
            return ""
        return f"It is {_spoken_clock(local)}."
    if re.search(r"\bwhat day is it\b|\bwhat.?s the date\b|\bwhat is the date\b|\bwhat.?s today.?s date\b|\bwhat is today.?s date\b", lowered):
        return f"Today is {local.strftime('%A, %B')} {local.day}."
    return ""


def _spoken_clock(moment):
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _schedule_request(lowered):
    if re.search(r"\b(?:what|which|list|any|do i have)\b.*\b(?:reminders?|routines?|scheduled?)\b", lowered):
        return Step("schedule.list", {})
    match = re.match(
        r"(?:please\s+)?(?:(?:can|could|would) you\s+)?"
        r"(?:cancel|delete|remove|stop|don.?t|do not)\b(.*\b(?:reminders?|routines?|schedule|midnight|every)\b.*)$",
        lowered,
    )
    if match:
        query = re.sub(r"\b(?:my|the|a|that|reminders?|routines?|schedule|please)\b", " ", match.group(1))
        return Step("schedule.cancel", {"query": " ".join(query.split()).strip(" .,!?")})
    return None


def _weather_day(lowered):
    for day in ("tomorrow", "today") + WEEKDAYS:
        if re.search(rf"\b{day}\b", lowered):
            return day
    return "today"


def _weather_place(lowered):
    days = "|".join(WEEKDAYS)
    match = re.search(
        rf"\bin ([a-z][a-z .'-]{{1,40}}?)(?:\s+(?:tomorrow|today|this \w+|on \w+|{days})\b|[?.!]|$)",
        lowered,
    )
    place = match.group(1).strip(" .") if match else ""
    if not place or re.match(r"(?:the|a|an|my)\b", place):
        return ""
    return place


def _weather(text, objective):
    lowered = text.lower()
    if "weather" not in lowered:
        return None
    place = _weather_place(lowered)
    day = _weather_day(lowered)
    if not place:
        return Plan(
            "clarify",
            objective,
            speech="Which city should I check?",
            reply_template=f"what's the weather in {{answer}} {day}",
        )
    return Step("weather.forecast", {"place": place, "day": day})


def _number(text):
    words = text.strip().replace("-", " ").split()
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    if len(words) > 2 or any(word not in NUMBER_WORDS for word in words):
        return None
    return sum(NUMBER_WORDS[word] for word in words)


def _clock_due(hour, minute, meridiem, now, tomorrow):
    """The next local time that fits. Without AM or PM, take whichever comes first."""
    local = now.astimezone()
    if hour > 23 or minute > 59:
        return None
    if meridiem in ("am", "pm"):
        if hour == 0 or hour > 12:
            return None
        hours = [hour % 12 + (12 if meridiem == "pm" else 0)]
    elif hour > 12 or hour == 0:
        hours = [hour]
    elif tomorrow:
        hours = [hour % 12 + 12 if hour <= 6 or hour == 12 else hour]
    else:
        hours = [hour % 12, hour % 12 + 12]
    day = local.replace(second=0, microsecond=0)
    if tomorrow:
        day = day + timedelta(days=1)
    choices = [day.replace(hour=value, minute=minute) for value in hours]
    upcoming = [choice for choice in choices if choice > local]
    if upcoming:
        return min(upcoming)
    return min(choices) + timedelta(days=1)


def _reminder_time(body, now):
    """Return (due, spoken time, text with the time removed, raw time phrase)."""
    lowered = body.lower()
    half = re.search(r"(?:^\s*(?:in\s+|for\s+)?|\b(?:in|for)\s+)half an hour\b", lowered)
    if half:
        return now + timedelta(minutes=30), "in half an hour", body[: half.start()] + body[half.end() :], half.group(0).strip()
    relative = re.search(r"(?:^\s*(?:in\s+|for\s+)?|\b(?:in|for)\s+)((?:[a-z0-9-]+\s+){1,2}?)(second|minute|hour)s?\b", lowered)
    if relative:
        count = _number(relative.group(1))
        if count:
            unit = relative.group(2)
            due = now + timedelta(**{f"{unit}s": count})
            label = f"in {count} {unit}{'s' if count != 1 else ''}"
            return due, label, body[: relative.start()] + body[relative.end() :], relative.group(0).strip()
    tomorrow = bool(re.search(r"\btomorrow\b", lowered))
    named = re.search(r"\bat (noon|midnight)\b", lowered)
    if named:
        due = _clock_due(12, 0, "pm" if named.group(1) == "noon" else "am", now, tomorrow)
        rest = body[: named.start()] + body[named.end() :]
        return due, named.group(1), re.sub(r"\btomorrow\b", "", rest, flags=re.I), named.group(0)
    number_names = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    clock = re.search(
        rf"\bat (\d{{1,2}}|{number_names})(?::(\d{{2}}))?\s*(a\.?\s?m\b\.?|p\.?\s?m\b\.?|o.?clock)?"
        rf"|\b(\d{{1,2}})(?::(\d{{2}}))?\s*(a\.?\s?m\b\.?|p\.?\s?m\b\.?)",
        lowered,
    )
    if clock:
        raw_hour = clock.group(1) or clock.group(4)
        minute = int(clock.group(2) or clock.group(5) or 0)
        suffix = (clock.group(3) or clock.group(6) or "").replace(".", "").replace(" ", "")
        hour = _number(raw_hour)
        meridiem = suffix if suffix in ("am", "pm") else ""
        due = _clock_due(hour, minute, meridiem, now, tomorrow) if hour is not None else None
        if due is not None:
            rest = body[: clock.start()] + body[clock.end() :]
            rest = re.sub(r"\btomorrow\b", "", rest, flags=re.I)
            today = now.astimezone().date()
            label = f"at {_spoken_clock(due)}" + (" tomorrow" if due.date() != today else "")
            return due, label, rest, clock.group(0)
    return None, "", body, "tomorrow" if tomorrow else ""


def _reminder(text, now, objective):
    match = re.search(r"\bremind me\b|\bset (?:a |an |me a )?(reminder|timer|alarm)\b", text, flags=re.I)
    if not match:
        return None
    body = text[match.end() :]
    if match.group(1):
        # "set a reminder for 5" means the clock time.
        body = re.sub(r"^\s*for\s+(?=(?:\d|one\b|two\b|three\b|four\b|five\b|six\b|seven\b|eight\b|nine\b|ten\b|eleven\b|twelve\b)(?!.*\b(?:second|minute|hour)))", " at ", body, count=1, flags=re.I)
    due, spoken, rest, raw_time = _reminder_time(body, now)
    subject = re.sub(r"^\s*(?:to|that|about)\b", "", rest.strip(" .,!?"), count=1, flags=re.I).strip(" .,!?")
    if not subject and match.group(1) and match.group(1).lower() != "reminder":
        subject = f"check the {match.group(1).lower()}"
    if due is not None and subject:
        due_at = due.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        return Step("time.remind", {"text": subject, "due_at": due_at, "spoken_time": spoken})
    if due is not None:
        return Plan(
            "clarify",
            objective,
            speech="What should I remind you about?",
            reply_template=f"remind me {raw_time} to {{answer}}",
        )
    if raw_time == "tomorrow":
        subject = re.sub(r"\btomorrow\b", "", subject, flags=re.I).strip(" .,!?")
        subject = re.sub(r"^(?:to|that|about)\b\s*", "", subject, flags=re.I)
        return Plan(
            "clarify",
            objective,
            speech="What time tomorrow?",
            reply_template=f"remind me tomorrow {{answer}} to {subject}",
        )
    if subject:
        return Plan(
            "clarify",
            objective,
            speech="When should I remind you?",
            reply_template=f"remind me {{answer}} to {subject}",
        )
    return Plan("clarify", objective, speech="What should I remind you about, and when?")


def _routine(text, objective):
    lowered = text.lower()
    commands, _refused = _command_clauses(lowered)
    if "midnight" in lowered and _power(commands) == "off":
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
        place = _weather_place(lowered)
        if not place:
            return Plan(
                "clarify",
                objective,
                speech="Which city should I check every morning?",
                reply_template="every morning tell me the weather in {answer}",
            )
        at_hour = re.search(r"\bat (\d{1,2})\b", lowered)
        hour = int(at_hour.group(1)) if at_hour and 1 <= int(at_hour.group(1)) <= 11 else 8
        return Step(
            "schedule.routine",
            {
                "name": f"Morning weather in {place}",
                "action": "weather",
                "argument": place,
                "hour": hour,
                "minute": 0,
                "speech": f"I'll tell you the weather in {place} every morning at {hour} AM.",
            },
        )
    return None
