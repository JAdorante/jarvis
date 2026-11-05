"""Each test here holds Jarvis to a written rule that everyday sentences used to break."""

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_jarvis import FakeRoku

from jarvis.agent.orchestrator import Agent
from jarvis.agent.permissions import PermissionEngine, approval_key
from jarvis.agent.planning import plan_request, strip_wake
from jarvis.audio.listen import Ear, read_phrase, worth_asking
from jarvis.audio.lock import AudioLock
from jarvis.config import load_settings
from jarvis.devices.roku import RokuClient
from jarvis.memory.store import MemoryStore
from jarvis.model.gateway import ModelGateway, guard_reply
from jarvis.observe import redact
from jarvis.scheduler import Scheduler
from jarvis.tools.builtin import build_tools, summary_answers
from jarvis.tools.registry import Registry, Tool, ToolResult


class Model:
    def __init__(self, reply="Hello."):
        self.reply = reply
        self.questions = []

    def chat(self, question, memories):
        self.questions.append(question)
        return self.reply


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RuleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.queries = []
        self.roku = FakeRoku()
        self.model = Model()
        self.clock = Clock()
        self.settings = load_settings()
        self.settings.data_dir = self.folder
        self.settings.retry_wait_seconds = 0
        self.settings.sparrow = None
        self.store = MemoryStore(self.folder / "jarvis.sqlite")
        self.user_file = self.folder / "user.txt"
        self.existence_file = self.folder / "existence.txt"
        self.agent = self._agent()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _agent(self, standing=True, weather_fetch=None, fetch=None):
        registry = build_tools(
            self.store,
            self.roku,
            wiki_fetch=fetch or self._fetch,
            legacy_user_file=self.user_file,
            legacy_existence_file=self.existence_file,
            weather_fetch=weather_fetch or (lambda place, day: f"{place} {day}: clear."),
        )
        self.permissions = PermissionEngine(standing)
        return Agent(
            self.settings,
            self.store,
            registry,
            self.permissions,
            model=self.model,
            sleeper=lambda _seconds: None,
            clock=self.clock,
        )

    def _fetch(self, query):
        self.queries.append(query)
        return "Interstellar: Interstellar is a film directed by Christopher Nolan."

    def _call_tool(self):
        def call(name, arguments, trigger):
            return self.agent.registry.call(name, arguments, {}, self.permissions, self.store, trigger)

        return call


class CommandsAreCommands(RuleCase):
    def test_sentences_that_are_not_tv_commands_leave_the_tv_alone(self):
        for sentence in (
            "don't turn off the TV",
            "do not mute the TV",
            "don't open Netflix",
            "how do I turn off the TV",
            "how do I open Netflix",
            "turn off the lights",
            "turn on the lights",
            "mute my phone",
            "don't turn off the TV at midnight",
        ):
            self.roku.calls.clear()
            self.agent.handle(sentence)
            self.assertEqual(self.roku.calls, [], sentence)
        self.assertEqual(self.store.active_routines(), [])

    def test_real_tv_commands_still_work(self):
        cases = {
            "turn off the TV": ("power", False),
            "turn the TV off": ("power", False),
            "please turn it on": ("power", True),
            "turn on": ("power", True),
            "can you turn off the TV": ("power", False),
            "mute": ("key", "VolumeMute"),
            "mute the TV": ("key", "VolumeMute"),
            "volume up": ("key", "VolumeUp"),
            "make the TV louder": ("key", "VolumeUp"),
        }
        for sentence, expected in cases.items():
            self.roku.calls.clear()
            self.agent.handle(sentence)
            self.assertEqual(self.roku.calls, [expected], sentence)

    def test_two_commands_and_a_refused_clause(self):
        self.agent.handle("turn on the TV and open YouTube")
        self.assertEqual(self.roku.calls, [("power", True), ("launch", "837")])
        self.roku.calls.clear()
        self.agent.handle("open YouTube but don't turn off the TV")
        self.assertEqual(self.roku.calls[0][0], "launch")
        self.assertNotIn(("power", False), self.roku.calls)

    def test_other_device_says_it_did_nothing(self):
        result = self.agent.handle("turn off the lights")
        self.assertIn("only control the TV", result["speech"])
        self.assertEqual(self.model.questions, [])

    def test_wake_word_is_required_outside_a_followup(self):
        for sentence in ("turn off the lights", "open the door for him", "pass me the jar", "that jargon is annoying", "I told jarvis yesterday about it"):
            self.assertFalse(worth_asking(sentence), sentence)
        for sentence in ("Jarvis, open YouTube.", "hey jarvis what time is it", "turn off the TV, Jarvis."):
            self.assertTrue(worth_asking(sentence), sentence)

    def test_wake_word_comes_from_config(self):
        self.assertTrue(worth_asking("computer, open YouTube", wake_word="computer"))
        self.assertFalse(worth_asking("jarvis, open YouTube", wake_word="computer"))
        self.assertEqual(strip_wake("computer, open YouTube", "computer"), "open YouTube")
        self.assertEqual(strip_wake("Jar of pickles"), "Jar of pickles")
        self.assertEqual(strip_wake("turn off the TV, Jarvis."), "turn off the TV")

    def test_tv_command_is_accepted_during_a_followup(self):
        self.assertTrue(worth_asking("open YouTube", followup_open=True))


class ForgetMeansForget(RuleCase):
    def _remember(self, *facts):
        for fact in facts:
            self.agent.handle(f"Remember that {fact}")

    def test_dont_forget_never_deletes(self):
        self._remember("I need to call mom on Sunday")
        result = self.agent.handle("Don't forget to call mom")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertIn("When should I remind you", result["speech"])
        self.agent.handle("in twenty minutes")
        pending = self.store.pending_reminders()
        self.assertEqual([row["text"] for row in pending], ["call mom"])

    def test_passing_mentions_of_forget_do_not_delete(self):
        self._remember("my keys are on the hook")
        for sentence in ("I always forget my keys", "I don't remember my keys"):
            self.agent.handle(sentence)
            self.agent.pending = None
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_vague_forget_asks_instead_of_wiping_everything(self):
        self._remember("I like jazz", "door code is 1234")
        for sentence in ("forget it", "forget that", "forget everything"):
            result = self.agent.handle(sentence)
            self.assertIn("What should I forget", result["speech"])
            self.assertEqual(len(self.store.list_memories()), 2)
            self.agent.pending = None

    def test_forget_matches_whole_words(self):
        self._remember("my education was in Boston")
        self.assertEqual(self.store.forget_candidates("cat"), [])

    def test_several_matches_need_a_yes(self):
        self._remember("I like red wine", "I like white wine")
        asked = self.agent.handle("forget wine")
        self.assertEqual(asked["status"], "WAITING_FOR_APPROVAL")
        self.assertEqual(len(self.store.list_memories()), 2)
        self.assertIn("Say yes", asked["speech"])
        done = self.agent.handle("yes")
        self.assertIn("forgot 2", done["speech"])
        self.assertEqual(self.store.list_memories(), [])

    def test_several_matches_and_a_no_keeps_them(self):
        self._remember("I like red wine", "I like white wine")
        self.agent.handle("forget wine")
        result = self.agent.handle("no")
        self.assertIn("won't", result["speech"])
        self.assertEqual(len(self.store.list_memories()), 2)

    def test_a_forgotten_fact_does_not_come_back_after_a_restart(self):
        self._remember("door code is 1234")
        self.store.append_legacy_user_line(self.user_file, "Door code is 1234.")
        self.agent.handle("forget the door code")
        self.assertEqual(self.store.list_memories(), [])
        self.assertNotIn("1234", self.user_file.read_text(encoding="utf-8"))
        self.assertEqual(self.store.import_legacy(self.user_file, self.existence_file), 0)
        self.assertEqual(self.store.list_memories(), [])

    def test_a_line_left_in_a_legacy_file_stays_forgotten(self):
        self.user_file.write_text("Door code is 1234.\n", encoding="utf-8")
        self.store.import_legacy(self.user_file, self.existence_file)
        self.agent.handle("forget the door code")
        self.assertNotIn("1234", (self.folder / "user.txt.bak").read_text(encoding="utf-8"))
        self.user_file.write_text("Door code is 1234.\n", encoding="utf-8")
        self.assertEqual(self.store.import_legacy(self.user_file, self.existence_file), 0)

    def test_forgetting_clears_the_history_of_saying_it(self):
        self.agent.handle("Remember that door code is 1234")
        self.agent.handle("forget the door code")
        db = self.store.db
        for table, column in (("turns", "user_text"), ("tasks", "objective"), ("audit", "trigger_text"), ("audit", "arguments")):
            rows = db.execute(f"SELECT {column} FROM {table}").fetchall()
            self.assertFalse(any("1234" in row[0] for row in rows), f"{table}.{column}")
        self.assertEqual(db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)

    def test_old_hidden_rows_are_erased_on_upgrade(self):
        self.store.add_memory("Door code is 1234.", "semantic", "explicit_user_statement")
        self.store.db.execute("UPDATE memories SET superseded_by = -1")
        self.store.add_memory("Door code is 1234.", "semantic", "imported_user_txt")
        self.store.db.commit()
        self.store.close()
        self.store = MemoryStore(self.folder / "jarvis.sqlite")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        self.user_file.write_text("Door code is 1234.\n", encoding="utf-8")
        self.assertEqual(self.store.import_legacy(self.user_file, self.existence_file), 0)

    def test_saying_it_again_brings_it_back_on_purpose(self):
        self._remember("I like jazz")
        self.agent.handle("forget jazz")
        self.agent.handle("Remember that I like jazz")
        self.assertEqual(len(self.store.list_memories()), 1)


class BackupsRestore(RuleCase):
    def test_backup_has_the_newest_fact_and_opens(self):
        self.store.add_memory("Saved just now.", "semantic", "x")
        dest = self.store.backup_to(self.folder / "backups" / "copy.sqlite")
        copy = sqlite3.connect(dest)
        try:
            rows = copy.execute("SELECT content FROM memories").fetchall()
            status = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
        self.assertEqual(rows, [("Saved just now.",)])
        self.assertEqual(status, "ok")
        self.assertFalse((self.folder / "backups" / "copy.sqlite.partial").exists())


class SilenceHoldsNothing(RuleCase):
    SETTINGS = SimpleNamespace(speech_level=0.045, silence_level=0.03, silence_seconds=0.1, max_phrase_seconds=12)
    QUIET = b"\x00\x00" * 800
    LOUD = b"\xff\x3f" * 800

    def _reader(self, blocks, on_block=None):
        queue = list(blocks)

        def read():
            if on_block:
                on_block(len(queue))
            return queue.pop(0) if queue else b""

        return read

    def test_waiting_in_silence_does_not_hold_the_lock(self):
        lock = AudioLock(self.folder / "audio.lock")
        free = []

        def check(_remaining):
            other = AudioLock(self.folder / "audio.lock")
            free.append(other.acquire(blocking=False))
            other.release()

        raw = read_phrase(self._reader([self.QUIET] * 5, check), 1600, self.SETTINGS, lock)
        self.assertEqual(raw, b"")
        self.assertTrue(all(free))

    def test_a_live_sentence_holds_it_then_lets_go(self):
        lock = AudioLock(self.folder / "audio.lock")
        states = []

        def check(remaining):
            other = AudioLock(self.folder / "audio.lock")
            got = other.acquire(blocking=False)
            states.append(got)
            other.release()

        blocks = [self.QUIET, self.LOUD, self.LOUD, self.QUIET, self.QUIET, self.QUIET]
        raw = read_phrase(self._reader(blocks, check), 1600, self.SETTINGS, lock)
        self.assertTrue(raw)
        self.assertEqual(states[:2], [True, True])
        self.assertFalse(states[2])
        again = AudioLock(self.folder / "audio.lock")
        self.assertTrue(again.acquire(blocking=False))
        again.release()

    def test_jarvis_own_voice_is_thrown_away(self):
        speaking = AudioLock(self.folder / "audio.lock")
        speaking.acquire()
        lock = AudioLock(self.folder / "audio.lock")
        try:
            raw = read_phrase(self._reader([self.LOUD] * 4), 1600, self.SETTINGS, lock)
        finally:
            speaking.release()
        self.assertEqual(raw, b"")

    def test_the_echo_tail_after_speech_is_thrown_away(self):
        speaking = AudioLock(self.folder / "audio.lock")
        speaking.acquire()
        lock = AudioLock(self.folder / "audio.lock")
        clock = Clock()

        def advance(remaining):
            clock.now += 0.05
            if remaining == 3:
                speaking.release()

        raw = read_phrase(self._reader([self.LOUD] * 4, advance), 1600, self.SETTINGS, lock, clock=clock)
        self.assertEqual(raw, b"")


class OnlyToolsReportActions(RuleCase):
    def test_a_model_claim_is_not_spoken(self):
        self.model.reply = "Done, I set a reminder and turned the TV up."
        result = self.agent.handle("do my taxes")
        self.assertNotIn("reminder", result["speech"])
        self.assertIn("did not change anything", result["speech"])

    def test_ordinary_model_answers_are_left_alone(self):
        for reply in ("Hello.", "I remember you like jazz.", "I can't turn that on.", "I don't know."):
            self.assertEqual(guard_reply(reply), reply)
        for reply in ("I turned off the TV.", "I've set a timer.", "I'll remind you at five.", "The volume is now up.", "Your reminder is set."):
            self.assertNotEqual(guard_reply(reply), reply)

    def test_common_reminder_phrasings_create_a_reminder(self):
        start = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        for sentence, subject, minutes in (
            ("remind me in twenty minutes to call mom", "call mom", 20),
            ("remind me in twenty five minutes to call mom", "call mom", 25),
            ("remind me to call mom in 5 minutes", "call mom", 5),
            ("remind me in half an hour to call mom", "call mom", 30),
            ("remind me in an hour to call mom", "call mom", 60),
        ):
            plan = plan_request(sentence, now=start)
            self.assertEqual(plan.kind, "tools", sentence)
            step = plan.steps[0]
            self.assertEqual(step.arguments["text"], subject, sentence)
            expected = (start + timedelta(minutes=minutes)).isoformat()
            self.assertEqual(step.arguments["due_at"], expected, sentence)

    def test_timers_and_set_a_reminder(self):
        start = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
        step = plan_request("set a timer for 10 minutes", now=start).steps[0]
        self.assertEqual(step.arguments["text"], "check the timer")
        self.assertEqual(step.arguments["due_at"], (start + timedelta(minutes=10)).isoformat())
        step = plan_request("set a reminder for 5 to call mom", now=start).steps[0]
        self.assertEqual(step.arguments["text"], "call mom")
        self.assertIn("5:00", step.arguments["spoken_time"])

    def test_remember_without_that_is_stored(self):
        self.agent.handle("remember I parked on level 2")
        self.assertIn("level 2", self.store.list_memories()[0]["content"])
        self.assertEqual(self.model.questions, [])
        self.agent.handle("remember to breathe")
        self.assertEqual(len(self.store.list_memories()), 1)

    def test_clock_times_use_the_next_one_that_fits(self):
        local = datetime.now().astimezone().replace(hour=14, minute=0, second=0, microsecond=0)
        step = plan_request("remind me at 5 to call mom", now=local).steps[0]
        due = datetime.fromisoformat(step.arguments["due_at"]).astimezone()
        self.assertEqual((due.hour, due.minute), (17, 0))
        self.assertIn("5:00 PM", step.arguments["spoken_time"])
        step = plan_request("remind me at 9 a.m. to call mom", now=local).steps[0]
        due = datetime.fromisoformat(step.arguments["due_at"]).astimezone()
        self.assertEqual((due.hour, due.date()), (9, (local + timedelta(days=1)).date()))
        step = plan_request("remind me tomorrow at noon to call mom", now=local).steps[0]
        due = datetime.fromisoformat(step.arguments["due_at"]).astimezone()
        self.assertEqual((due.hour, due.date()), (12, (local + timedelta(days=1)).date()))

    def test_a_reminder_missing_a_detail_asks_for_it(self):
        result = self.agent.handle("remind me to call mom")
        self.assertIn("When", result["speech"])
        self.agent.handle("in ten minutes")
        self.assertEqual([row["text"] for row in self.store.pending_reminders()], ["call mom"])
        result = self.agent.handle("remind me in ten minutes")
        self.assertIn("What should I remind", result["speech"])
        self.agent.handle("check the oven")
        self.assertIn("check the oven", [row["text"] for row in self.store.pending_reminders()])
        result = self.agent.handle("remind me tomorrow to stretch")
        self.assertIn("What time tomorrow", result["speech"])

    def test_email_claims_nothing(self):
        result = self.agent.handle("email Bob about the deck")
        self.assertNotIn("drafted", result["speech"])
        self.assertIn("Nothing has been sent", result["speech"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM audit").fetchone()[0], 0)

    def test_the_word_email_is_not_an_email_request(self):
        self.assertNotEqual(plan_request("what is email").speech[:5], "Email")

    def test_cancel_with_nothing_running_says_so(self):
        self.assertIn("Nothing is running", self.agent.handle("stop")["speech"])


class SchedulerKeepsTheRules(RuleCase):
    NOON = datetime(2026, 10, 26, 12, 0)
    UTC = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)

    def test_scheduled_tv_off_goes_through_permissions_and_audit(self):
        self.agent = self._agent()
        self.store.add_routine("Midnight TV off", "power_off", "", 0, 0, now_local=datetime(2026, 10, 25, 12, 0))
        scheduler = Scheduler(self.store, lambda text: None, self._call_tool())
        scheduler.tick(datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc), datetime(2026, 10, 26, 0, 0))
        self.assertEqual(self.roku.calls, [("power", False)])
        row = self.store.db.execute("SELECT trigger_text, tool, result FROM audit").fetchone()
        self.assertEqual(tuple(row), ("schedule: Midnight TV off", "roku.power", "ok"))

    def test_standing_authorization_off_stops_a_scheduled_tv_off(self):
        self.agent = self._agent(standing=False)
        self.store.add_routine("Midnight TV off", "power_off", "", 0, 0, now_local=datetime(2026, 10, 25, 12, 0))
        spoken = []
        scheduler = Scheduler(self.store, spoken.append, self._call_tool())
        scheduler.tick(datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc), datetime(2026, 10, 26, 0, 0))
        self.assertEqual(self.roku.calls, [])
        self.assertIn("did not finish", spoken[0])

    def test_saving_a_tv_off_is_refused_when_standing_authorization_is_off(self):
        self.agent = self._agent(standing=False)
        result = self.agent.handle("Turn off the TV at midnight.")
        self.assertEqual(self.store.active_routines(), [])
        self.assertIn("standing", result["speech"])
        self.assertEqual(result["status"], "FAILED")

    def test_spoken_routine_waits_out_quiet_hours(self):
        self.store.add_routine("Brief", "speak", "Morning brief.", 6, 30, now_local=datetime(2026, 10, 25, 5, 0))
        spoken = []
        scheduler = Scheduler(self.store, spoken.append, None, "22:00", "07:00")
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 6, 30))
        self.assertEqual(spoken, [])
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 7, 0))
        self.assertEqual(spoken, ["Morning brief."])
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 7, 1))
        self.assertEqual(spoken, ["Morning brief."])

    def test_tv_off_in_quiet_hours_is_silent_and_a_failure_waits(self):
        self.agent = self._agent()
        self.store.add_routine("Off", "power_off", "", 23, 30, now_local=datetime(2026, 10, 25, 12, 0))
        spoken = []
        scheduler = Scheduler(self.store, spoken.append, self._call_tool(), "22:00", "07:00")
        self.roku.fail = True
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 23, 30))
        self.assertEqual(spoken, [])
        self.assertIn("did not finish", self.store.pending_reminders()[0]["text"])

    def test_a_routine_that_starts_a_little_late_still_runs(self):
        self.agent = self._agent()
        self.store.add_routine("Off", "power_off", "", 0, 0, now_local=datetime(2026, 10, 25, 12, 0))
        scheduler = Scheduler(self.store, lambda text: None, self._call_tool())
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 0, 3))
        self.assertEqual(self.roku.calls, [("power", False)])
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 0, 4))
        self.assertEqual(self.roku.calls, [("power", False)])

    def test_a_routine_long_past_does_not_run_when_the_pi_boots(self):
        self.agent = self._agent()
        self.store.add_routine("Off", "power_off", "", 0, 0, now_local=datetime(2026, 10, 25, 12, 0))
        scheduler = Scheduler(self.store, lambda text: None, self._call_tool())
        scheduler.tick(self.UTC, datetime(2026, 10, 26, 8, 0))
        self.assertEqual(self.roku.calls, [])

    def test_a_late_run_across_midnight_counts_for_the_right_day(self):
        self.agent = self._agent()
        self.store.add_routine("Off", "power_off", "", 23, 58, now_local=datetime(2026, 10, 25, 12, 0))
        scheduler = Scheduler(self.store, lambda text: None, self._call_tool())
        scheduler.tick(self.UTC, datetime(2026, 10, 27, 0, 2))
        self.assertEqual(len(self.roku.calls), 1)
        self.assertEqual(self.store.active_routines()[0]["last_run"], "2026-10-26")

    def test_saving_inside_the_grace_window_does_not_fire_immediately(self):
        self.agent = self._agent()
        now = datetime(2026, 10, 26, 0, 3)
        self.store.add_routine("Off", "power_off", "", 0, 0, now_local=now)
        Scheduler(self.store, lambda text: None, self._call_tool()).tick(self.UTC, now)
        self.assertEqual(self.roku.calls, [])

    def test_saving_twice_keeps_one(self):
        self.agent.handle("Turn off the TV at midnight.")
        self.agent.handle("Turn off the TV at midnight.")
        self.assertEqual(len(self.store.active_routines()), 1)

    def test_morning_weather_is_reachable_and_uses_a_real_forecast(self):
        self.agent = self._agent()
        asked = self.agent.handle("every morning tell me the weather")
        self.assertIn("Which city", asked["speech"])
        saved = self.agent.handle("Boston")
        self.assertEqual(saved["status"], "COMPLETED")
        routine = self.store.active_routines()[0]
        self.assertEqual((routine["action"], routine["argument"], routine["hour"]), ("weather", "boston", 8))
        self.store.db.execute("UPDATE routines SET last_run = ''")
        self.store.db.commit()
        spoken = []
        Scheduler(self.store, spoken.append, self._call_tool()).tick(self.UTC, datetime(2026, 10, 26, 8, 0))
        self.assertEqual(spoken, ["boston today: clear."])

    def test_a_reminder_nobody_heard_stays_pending(self):
        self.store.add_reminder("oven", "2020-01-01T00:00:00+00:00")
        results = [False, True]
        said = []

        def speaker(text):
            said.append(text)
            return results.pop(0)

        scheduler = Scheduler(self.store, speaker, None)
        self.assertEqual(scheduler.tick(self.UTC, self.NOON), [])
        self.assertEqual(len(self.store.pending_reminders()), 1)
        scheduler.tick(self.UTC + timedelta(seconds=1), self.NOON)
        self.assertEqual(len(said), 1, "waits before trying the speaker again")
        scheduler.tick(self.UTC + timedelta(seconds=61), self.NOON)
        self.assertEqual(len(said), 2)
        self.assertEqual(self.store.pending_reminders(), [])

    def test_a_reminder_waits_while_the_tv_shows_something_else(self):
        self.store.add_reminder("oven", "2020-01-01T00:00:00+00:00")
        audible = [False, True]
        spoken = []
        scheduler = Scheduler(self.store, spoken.append, None, audible=lambda: audible.pop(0))
        scheduler.tick(self.UTC, self.NOON)
        self.assertEqual(spoken, [])
        scheduler.tick(self.UTC + timedelta(seconds=1), self.NOON)
        self.assertEqual(audible, [True], "the TV is not asked every second")
        scheduler.tick(self.UTC + timedelta(seconds=30), self.NOON)
        self.assertEqual(spoken, ["Reminder: oven."])

    def test_one_broken_part_does_not_stop_the_rest(self):
        self.agent = self._agent()
        self.store.add_routine("Off", "power_off", "", 12, 0, now_local=datetime(2026, 10, 25, 11, 0))
        original = self.store.due_reminders

        def broken(_stamp):
            raise sqlite3.OperationalError("database is locked")

        self.store.due_reminders = broken
        try:
            Scheduler(self.store, lambda text: None, self._call_tool()).tick(self.UTC, self.NOON)
        finally:
            self.store.due_reminders = original
        self.assertEqual(self.roku.calls, [("power", False)])


class RokuTransport:
    def __init__(self, modes=(), apps=()):
        self.modes = list(modes)
        self.apps = list(apps)
        self.posts = []

    def post(self, path):
        self.posts.append(path)
        return ""

    def get(self, path):
        if "device-info" in path:
            mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
            return f"<device-info><power-mode>{mode}</power-mode></device-info>"
        app = self.apps.pop(0) if len(self.apps) > 1 else self.apps[0]
        return f'<active-app><app id="{app}">App</app></active-app>'


class RokuIsPatient(unittest.TestCase):
    def _client(self, transport, settle=2):
        clock = Clock()

        def sleeper(seconds):
            clock.now += seconds

        return RokuClient("tv", transport=transport, settle_seconds=settle, sleeper=sleeper, clock=clock)

    def test_any_mode_but_power_on_means_off(self):
        for mode in ("DisplayOff", "Ready", "Headless", "PowerOff"):
            result = self._client(RokuTransport(modes=[mode])).power(False)
            self.assertTrue(result["verified"], mode)

    def test_off_is_not_confirmed_while_it_still_says_on(self):
        self.assertFalse(self._client(RokuTransport(modes=["PowerOn"])).power(False)["verified"])

    def test_a_slow_app_is_confirmed_without_launching_twice(self):
        transport = RokuTransport(apps=["0", "0", "12"])
        result = self._client(transport).launch("12")
        self.assertTrue(result["verified"])
        self.assertEqual(transport.posts, ["/launch/12"])

    def test_an_app_that_never_opens_is_unconfirmed_after_one_launch(self):
        transport = RokuTransport(apps=["0"])
        result = self._client(transport).launch("12")
        self.assertFalse(result["verified"])
        self.assertEqual(transport.posts, ["/launch/12"])


class ToolsAreBounded(RuleCase):
    def test_a_stuck_tool_is_reported_as_unconfirmed(self):
        release = threading.Event()

        def stuck(arguments, context):
            release.wait(5)
            return ToolResult(ok=True, output={}, speech="done", verified=True)

        registry = Registry()
        registry.add(Tool("slow.tool", "Stuck", "L0", "read", 0.05, True, (), stuck))
        started = time.monotonic()
        result = registry.call("slow.tool", {}, {}, PermissionEngine(), self.store, "test")
        release.set()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result.error_code, "timeout")
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)

    def test_tool_errors_still_surface(self):
        def boom(arguments, context):
            raise RuntimeError("broken")

        registry = Registry()
        registry.add(Tool("bad.tool", "Bad", "L0", "read", 1, True, (), boom))
        result = registry.call("bad.tool", {}, {}, PermissionEngine(), self.store, "test")
        self.assertEqual(result.error_code, "tool_error")


class Approvals(RuleCase):
    def test_yes_runs_the_exact_action_once(self):
        self.agent = self._agent(standing=False)
        asked = self.agent.handle("Open YouTube.")
        self.assertEqual(asked["status"], "WAITING_FOR_APPROVAL")
        self.assertIn("Say yes", asked["speech"])
        self.assertTrue(self.agent.expecting_reply())
        done = self.agent.handle("yes")
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(self.roku.calls, [("launch", "837")])
        self.assertEqual(done["task_id"], asked["task_id"])
        self.assertFalse(self.agent.expecting_reply())
        again = self.agent.handle("yes")
        self.assertEqual(self.roku.calls, [("launch", "837")])
        self.assertNotEqual(again["status"], "WAITING_FOR_APPROVAL")

    def test_approval_does_not_outlive_its_action(self):
        self.agent = self._agent(standing=False)
        self.agent.handle("Open YouTube.")
        self.agent.handle("yes")
        self.roku.calls.clear()
        asked = self.agent.handle("Open YouTube.")
        self.assertEqual(asked["status"], "WAITING_FOR_APPROVAL")
        self.assertEqual(self.roku.calls, [])

    def test_each_step_of_a_plan_needs_its_own_yes(self):
        self.agent = self._agent(standing=False)
        self.agent.handle("turn on the TV and open YouTube")
        self.agent.handle("yes")
        self.assertEqual(self.roku.calls, [("power", True)])
        self.assertTrue(self.agent.expecting_reply())
        self.agent.handle("yes")
        self.assertEqual(self.roku.calls, [("power", True), ("launch", "837")])

    def test_no_cancels(self):
        self.agent = self._agent(standing=False)
        asked = self.agent.handle("Open YouTube.")
        result = self.agent.handle("no")
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(self.roku.calls, [])
        row = self.store.db.execute("SELECT status FROM tasks WHERE id = ?", (asked["task_id"],)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")

    def test_a_different_sentence_drops_the_approval(self):
        self.agent = self._agent(standing=False)
        self.agent.handle("Open YouTube.")
        self.agent.handle("what time is it")
        self.assertFalse(self.agent.expecting_reply())
        self.agent.handle("yes")
        self.assertEqual(self.roku.calls, [])

    def test_a_late_yes_does_nothing(self):
        self.agent = self._agent(standing=False)
        asked = self.agent.handle("Open YouTube.")
        self.clock.now += self.settings.pending_seconds + 1
        self.assertFalse(self.agent.expecting_reply())
        self.agent.handle("yes")
        self.assertEqual(self.roku.calls, [])
        row = self.store.db.execute("SELECT status FROM tasks WHERE id = ?", (asked["task_id"],)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")

    def test_a_grant_covers_only_the_exact_arguments(self):
        tool = self.agent.registry.tools["roku.launch"]
        engine = PermissionEngine(False)
        engine.grant("roku.launch", {"app_id": "837"})
        self.assertTrue(engine.decide(tool, {"app_id": "837"}).allowed)
        self.assertFalse(engine.decide(tool, {"app_id": "12"}).allowed)
        engine.clear_grants()
        self.assertFalse(engine.decide(tool, {"app_id": "837"}).allowed)
        self.assertNotEqual(approval_key("a", {"x": 1}), approval_key("a", {"x": 2}))

    def test_prohibited_tools_cannot_be_approved(self):
        def danger(arguments, context):
            raise AssertionError("must not run")

        tool = Tool("danger.run", "No", "L3", "write", 1, False, (), danger)
        engine = PermissionEngine(True)
        engine.grant("danger.run", {})
        self.assertFalse(engine.decide(tool, {}).allowed)

    def test_l2_needs_a_grant(self):
        tool = Tool("mail.send", "Send", "L2", "write", 1, False, (), None)
        engine = PermissionEngine(True)
        self.assertEqual(engine.decide(tool, {"to": "a"}).reason, "needs_approval")
        engine.grant("mail.send", {"to": "a"})
        self.assertTrue(engine.decide(tool, {"to": "a"}).allowed)

    def test_a_restart_does_not_leave_tasks_waiting(self):
        from jarvis.runtime import recover_interrupted

        self.agent = self._agent(standing=False)
        asked = self.agent.handle("Open YouTube.")
        self.assertEqual(recover_interrupted(self.store), [asked["task_id"]])


class FollowUps(RuleCase):
    def test_answering_a_question_needs_no_wake_word(self):
        ear = Ear(self.settings)
        self.agent = self._agent()
        asked = self.agent.handle("what's the weather")
        self.assertIn("Which city", asked["speech"])
        ear.mark(100.0)
        self.assertTrue(ear.accepts("Boston", 105.0, expecting_reply=self.agent.expecting_reply()))
        self.assertFalse(ear.accepts("Boston", 105.0, expecting_reply=False))
        self.assertFalse(ear.accepts("Boston", 500.0, expecting_reply=True))
        answer = self.agent.handle("Boston.")
        self.assertEqual(answer["status"], "COMPLETED")
        self.assertIn("boston today", answer["speech"].lower())

    def test_a_new_command_replaces_an_open_question(self):
        self.agent.handle("what's the weather")
        result = self.agent.handle("open YouTube")
        self.assertEqual(self.roku.calls, [("launch", "837")])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertFalse(self.agent.expecting_reply())

    def test_a_bare_ok_is_not_an_answer(self):
        self.agent.handle("what's the weather")
        self.assertEqual(self.agent.handle("ok")["speech"], "Okay.")

    def test_never_mind_closes_an_open_question(self):
        self.agent.handle("what's the weather")
        self.assertEqual(self.agent.handle("never mind")["speech"], "Okay.")
        self.assertFalse(self.agent.expecting_reply())

    def test_the_followup_window_starts_when_the_answer_ends(self):
        ear = Ear(self.settings)
        self.assertFalse(ear.accepts("what about Friday", 1.0))
        ear.mark(100.0)
        self.assertTrue(ear.accepts("what about Friday", 100.0 + self.settings.followup_seconds))
        self.assertFalse(ear.accepts("what about Friday", 100.1 + self.settings.followup_seconds))

    def test_what_about_follows_the_weather(self):
        seen = []

        def forecast(place, day):
            seen.append((place, day))
            return f"{place} {day}: clear."

        self.agent = self._agent(weather_fetch=forecast)
        self.agent.handle("what's the weather in Boston")
        self.agent.handle("what about Friday?")
        self.agent.handle("what about tomorrow?")
        self.agent.handle("what about Chicago?")
        self.assertEqual(seen[1:], [("boston", "friday"), ("boston", "tomorrow"), ("chicago", "tomorrow")])
        self.assertEqual(self.queries, [])

    def test_lookup_followups_do_not_grow(self):
        self.agent.handle("Who directed Interstellar?")
        self.agent.handle("what about Friday?")
        self.agent.handle("what about Saturday?")
        self.assertEqual(self.queries[-1], "Who directed Interstellar? Saturday?")
        self.assertNotIn("Friday", self.queries[-1])

    def test_followups_expire(self):
        self.agent.handle("what's the weather in Boston")
        self.clock.now += self.settings.pending_seconds + 1
        self.agent.handle("what about Friday?")
        self.assertEqual(self.queries, ["what about Friday?"])


class PrivateStaysLocal(RuleCase):
    def test_personal_questions_do_not_reach_wikipedia(self):
        self.store.add_memory("My wifi password is hunter2.", "semantic", "x")
        for sentence in ("what's my sister's name", "what do I owe Hugh", "where did I park"):
            result = self.agent.handle(sentence)
            self.assertEqual(self.queries, [], sentence)
            self.assertIn("don't have that saved", result["speech"], sentence)
            self.assertNotIn("hunter2", result["speech"], sentence)
        self.assertIn("hunter2", self.agent.handle("what's my wifi password")["speech"])
        self.assertEqual(self.queries, [])

    def test_clock_questions_answer_locally(self):
        now = datetime.now().astimezone().replace(hour=15, minute=5)
        plan = plan_request("what time is it", now=now)
        self.assertEqual((plan.kind, plan.speech), ("talk", "It is 3:05 PM."))
        self.assertIn(str(now.day), plan_request("what's the date", now=now).speech)

    def test_time_elsewhere_is_not_answered_with_the_local_clock(self):
        self.assertNotIn("It is", plan_request("what is the time in Tokyo").speech)

    def test_small_talk_goes_to_the_model(self):
        for sentence in ("how are you doing today", "what's your favorite movie", "what do you think about jazz"):
            self.agent.handle(sentence)
        self.assertEqual(self.queries, [])
        self.assertEqual(len(self.model.questions), 3)

    def test_a_page_that_does_not_answer_is_not_an_answer(self):
        self.agent = self._agent(fetch=lambda query: "How Are You: An album by a band. It has songs.")
        result = self.agent.handle("who invented the telephone")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("did not find", result["speech"])
        self.assertTrue(summary_answers("capital of France", "Paris: Paris is the capital of France."))
        self.assertFalse(summary_answers("capital of France", "Lyon: A river town."))

    def test_answers_name_their_source(self):
        self.assertIn("According to Wikipedia", self.agent.handle("who directed Interstellar")["speech"])

    def test_remote_filter_keeps_the_question(self):
        settings = SimpleNamespace(remote_enabled=True, remote_url="http://remote", allow_private_memory_remote=False)
        gateway = ModelGateway(settings)
        sent = {}

        def remote(messages):
            sent["messages"] = messages
            return "ok"

        gateway._remote = remote
        gateway.chat("what should I cook tonight", ["Door code is 1234."])
        text = sent["messages"][1]["content"]
        self.assertIn("what should I cook tonight", text)
        self.assertNotIn("1234", text)

    def test_every_token_field_is_redacted(self):
        cleaned = redact({"token": "a", "device_token": "b", "mcp_token": "c", "auth_token": "d", "query": "e"})
        self.assertEqual(cleaned["query"], "e")
        self.assertEqual({cleaned[key] for key in ("token", "device_token", "mcp_token", "auth_token")}, {"[redacted]"})

    def test_face_stays_on_this_machine_by_default(self):
        self.assertEqual(self.settings.face_host, "127.0.0.1")


class ScheduleIsManageable(RuleCase):
    def test_list_and_cancel(self):
        self.assertIn("Nothing is scheduled", self.agent.handle("what reminders do I have")["speech"])
        self.agent.handle("Turn off the TV at midnight.")
        self.agent.handle("remind me in ten minutes to check the oven")
        listed = self.agent.handle("what reminders do I have")["speech"]
        self.assertIn("check the oven", listed)
        self.assertIn("Midnight TV off", listed)
        self.assertIn("cancelled 1", self.agent.handle("cancel my midnight TV off")["speech"])
        self.assertEqual(self.store.active_routines(), [])
        self.assertEqual(len(self.store.pending_reminders()), 1)
        self.assertIn("cancelled 1", self.agent.handle("cancel the oven reminder")["speech"])
        self.assertEqual(self.store.pending_reminders(), [])

    def test_saying_dont_cancels_an_existing_routine(self):
        self.agent.handle("Turn off the TV at midnight.")
        self.agent.handle("don't turn off the TV at midnight")
        self.assertEqual(self.store.active_routines(), [])

    def test_cancelling_several_needs_a_yes(self):
        self.agent.handle("remind me in ten minutes to check the oven")
        self.agent.handle("remind me in twenty minutes to check the mail")
        asked = self.agent.handle("cancel my reminders")
        self.assertEqual(asked["status"], "WAITING_FOR_APPROVAL")
        self.assertEqual(len(self.store.pending_reminders()), 2)
        self.agent.handle("yes")
        self.assertEqual(self.store.pending_reminders(), [])


if __name__ == "__main__":
    unittest.main()
