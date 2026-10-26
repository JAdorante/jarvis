"""Reminders and saved daily actions. Speech waits out quiet hours. A TV-off schedule does not."""

from datetime import datetime, timezone


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
    def __init__(self, store, speaker, roku, quiet_start="", quiet_end=""):
        self.store = store
        self.speaker = speaker
        self.roku = roku
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end

    def tick(self, now_utc, now_local):
        events = []
        quiet = in_quiet_hours(now_local, self.quiet_start, self.quiet_end)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        stamp = now_utc.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        if not quiet:
            for reminder in self.store.due_reminders(stamp):
                message = f"Reminder: {reminder['text']}."
                self.speaker(message)
                self.store.complete_reminder(reminder["id"])
                events.append({"type": "reminder", "speech": message})
        today = now_local.date().isoformat()
        for routine in self.store.due_routines(now_local.hour, now_local.minute, today):
            if routine["action"] == "speak" and quiet:
                continue
            speech = self._run_routine(routine)
            self.store.mark_routine_ran(routine["id"], today)
            events.append({"type": "routine", "speech": speech})
        return events

    def _run_routine(self, routine):
        action = routine["action"]
        if action == "power_off" and self.roku is not None:
            try:
                result = self.roku.power(False)
            except Exception as exc:
                speech = f"I could not turn the TV off. {exc}"
            else:
                if result.get("verified"):
                    speech = "The TV is off."
                else:
                    speech = "I sent the scheduled power command. The TV did not confirm it is off."
            self.speaker(speech)
            return speech
        speech = routine.get("argument") or "The scheduled reminder is due."
        self.speaker(speech)
        return speech
