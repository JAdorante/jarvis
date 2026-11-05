"""Reminders and saved daily actions. Speech waits out quiet hours. A TV-off schedule does not."""

from datetime import timedelta, timezone

from jarvis.observe import log_event

# A routine can start this many minutes late. Past that it waits for tomorrow,
# so a Pi that boots at 8 AM does not turn the TV off for last midnight.
GRACE_MINUTES = 10
SPEECH_RETRY_SECONDS = 60
AUDIBLE_CHECK_SECONDS = 30


def in_quiet_hours(now_local, start, end):
    if not start or not end:
        return False
    try:
        start_hour, start_minute = [int(part) for part in start.split(":", 1)]
        end_hour, end_minute = [int(part) for part in end.split(":", 1)]
    except ValueError:
        return False
    current = now_local.hour * 60 + now_local.minute
    start_at = start_hour * 60 + start_minute
    end_at = end_hour * 60 + end_minute
    if start_at == end_at:
        return False
    if start_at < end_at:
        return start_at <= current < end_at
    return current >= start_at or current < end_at


class Scheduler:
    """`call_tool(name, arguments, trigger)` runs a tool through permissions and audit."""

    def __init__(self, store, speaker, call_tool=None, quiet_start="", quiet_end="", outbox=None, audible=None, logger=None):
        self.store = store
        self.speaker = speaker
        self.call_tool = call_tool
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        self.outbox = outbox
        self.audible = audible
        self.logger = logger
        self._silent_until = None
        self._audible_at = None
        self._audible_value = True

    def tick(self, now_utc, now_local):
        events = []
        quiet = in_quiet_hours(now_local, self.quiet_start, self.quiet_end)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_utc = now_utc.astimezone(timezone.utc).replace(microsecond=0)
        # One broken part must not stop the others.
        for name, part in (
            ("reminders", lambda: self._reminders(now_utc, quiet)),
            ("routines", lambda: self._routines(now_utc, now_local, quiet)),
            ("outbox", lambda: self._outbox(now_utc, quiet)),
        ):
            try:
                events.extend(part())
            except Exception as exc:
                log_event(self.logger, event="scheduler_error", part=name, error=str(exc))
        return events

    def _reminders(self, now_utc, quiet):
        events = []
        if quiet:
            return events
        for reminder in self.store.due_reminders(now_utc.isoformat()):
            if not self._can_speak(now_utc):
                break
            message = f"Reminder: {reminder['text']}."
            if not self._say(message, now_utc):
                break
            self.store.complete_reminder(reminder["id"])
            events.append({"type": "reminder", "speech": message})
        return events

    def _routines(self, now_utc, now_local, quiet):
        events = []
        for routine in self.store.active_routines():
            occurrence = self._due_occurrence(routine, now_local)
            if occurrence is None:
                continue
            if routine["action"] == "power_off":
                speech = self._power_off(routine, now_utc, quiet)
            else:
                if quiet or not self._can_speak(now_utc):
                    continue
                speech = self._spoken_routine(routine)
                if not self._say(speech, now_utc):
                    continue
            self.store.mark_routine_ran(routine["id"], occurrence)
            events.append({"type": "routine", "speech": speech})
        return events

    def _outbox(self, now_utc, quiet):
        if self.outbox is None:
            return []
        held = quiet or not self._can_speak(now_utc)
        return [{"type": "sparrow", "speech": speech} for speech in self.outbox.tick(quiet=held)]

    def _due_occurrence(self, routine, now_local):
        """Return the date of the run that is due now, or None."""
        scheduled = routine["hour"] * 60 + routine["minute"]
        effective = scheduled
        if routine["action"] != "power_off":
            at_time = now_local.replace(hour=routine["hour"], minute=routine["minute"])
            if in_quiet_hours(at_time, self.quiet_start, self.quiet_end):
                effective = self._quiet_end_minutes()
        current = now_local.hour * 60 + now_local.minute
        late = (current - effective) % (24 * 60)
        if late > GRACE_MINUTES:
            return None
        occurrence = (now_local - timedelta(minutes=late)).date().isoformat()
        if routine.get("last_run") == occurrence:
            return None
        return occurrence

    def _quiet_end_minutes(self):
        hour, minute = [int(part) for part in self.quiet_end.split(":", 1)]
        return hour * 60 + minute

    def _power_off(self, routine, now_utc, quiet):
        trigger = f"schedule: {routine['name']}"
        if self.call_tool is None:
            log_event(self.logger, event="routine_skipped", routine=routine["name"], reason="no_tool_runner")
            return "The scheduled TV-off did not run."
        result = self.call_tool("roku.power", {"on": False}, trigger)
        if result.ok and result.verified:
            return result.speech
        speech = f"The scheduled TV-off did not finish. {result.speech}"
        if quiet:
            self.store.add_reminder(speech.rstrip("."), now_utc.isoformat())
        else:
            self._say(speech, now_utc)
        return speech

    def _spoken_routine(self, routine):
        if routine["action"] == "weather" and self.call_tool is not None:
            result = self.call_tool(
                "weather.forecast",
                {"place": routine["argument"], "day": "today"},
                f"schedule: {routine['name']}",
            )
            return result.speech
        return routine.get("argument") or "The scheduled reminder is due."

    def _say(self, message, now_utc):
        """Speak once. False means nobody heard it, so the caller keeps it for later."""
        try:
            delivered = self.speaker(message)
        except Exception as exc:
            log_event(self.logger, event="speech_failed", error=str(exc))
            delivered = False
        if delivered is False:
            self._silent_until = now_utc + timedelta(seconds=SPEECH_RETRY_SECONDS)
            return False
        return True

    def _can_speak(self, now_utc):
        if self._silent_until is not None and now_utc < self._silent_until:
            return False
        if self.audible is None:
            return True
        stale = self._audible_at is None or (now_utc - self._audible_at).total_seconds() >= AUDIBLE_CHECK_SECONDS
        if stale:
            try:
                self._audible_value = bool(self.audible())
            except Exception as exc:
                log_event(self.logger, event="audible_check_failed", error=str(exc))
                self._audible_value = True
            self._audible_at = now_utc
        return self._audible_value
