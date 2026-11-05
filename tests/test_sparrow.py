"""The acceptance list from the Jarvis x Sparrow spec. A stand-in server, no network."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_jarvis import FakeRoku

from jarvis.agent.orchestrator import Agent
from jarvis.agent.permissions import PermissionEngine
from jarvis.agent.planning import plan_request
from jarvis.config import load_settings
from jarvis.memory.store import MemoryStore
from jarvis.runtime import share_with_sparrow
from jarvis.scheduler import Scheduler
from jarvis.sparrow import (
    OutboxPoller,
    SparrowClient,
    SparrowError,
    SparrowSettings,
    build_sparrow_tools,
    load_sparrow_settings,
    match_sparrow_intent,
    speak_open_loops,
    speak_org,
    speak_outbox_item,
    speak_person,
    speak_search,
    stays_local,
)
from jarvis.tools.builtin import build_tools

NOON = datetime(2026, 10, 26, 12, 0)
UTC = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)


class Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Server:
    """Answers by path. A callable answer can raise, or vary between calls."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        self.requests.append(
            SimpleNamespace(
                method=request.get_method(),
                url=request.full_url,
                auth=request.get_header("Authorization"),
                body=body,
                timeout=timeout,
            )
        )
        key = request.full_url.split("/", 3)[3]
        if key == "mcp/tool":
            key = f"tool:{body['name']}"
        answer = self.routes.get(key, {})
        if callable(answer):
            answer = answer(request)
        return Reply(json.dumps(answer).encode())


def http_error(code, payload=None):
    def fail(request):
        raise urllib.error.HTTPError(request.full_url, code, "error", {}, io.BytesIO(json.dumps(payload or {}).encode()))

    return fail


class SparrowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.mcp_file = self.folder / "mcp"
        self.device_file = self.folder / "device"
        self.mcp_file.write_text("mcp-secret\n", encoding="utf-8")
        self.device_file.write_text("device-secret\n", encoding="utf-8")
        self.cfg = SparrowSettings(
            enabled=True,
            base_url="https://sparrow.example",
            mcp_token_file=self.mcp_file,
            device_token_file=self.device_file,
        )
        self.server = Server()
        self.client = SparrowClient(self.cfg, opener=self.server)
        self.settings = load_settings()
        self.settings.data_dir = self.folder
        self.settings.retry_wait_seconds = 0
        self.settings.sparrow = self.cfg
        self.store = MemoryStore(self.folder / "jarvis.sqlite")
        self.roku = FakeRoku()
        self.registry = build_tools(self.store, self.roku, wiki_fetch=lambda query: "")
        for tool in build_sparrow_tools(self.client):
            self.registry.add(tool)
        self.agent = Agent(
            self.settings,
            self.store,
            self.registry,
            PermissionEngine(True),
            sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class Intents(unittest.TestCase):
    def test_routes_only_when_configured(self):
        self.assertEqual(plan_request("What's on my plate?", sparrow=True).steps[0].tool, "sparrow.open_loops")
        plain = plan_request("What's on my plate?", sparrow=False)
        self.assertFalse(any(step.tool.startswith("sparrow") for step in plain.steps))

    def test_spec_examples(self):
        for sentence, tool, arguments in (
            ("What do I owe Hugh?", "sparrow.open_loops", {"person": "hugh"}),
            ("What's still open with Andy?", "sparrow.open_loops", {"person": "andy"}),
            ("Open items with Boost Run", "sparrow.open_loops", {"person": "boost run"}),
            ("whats on my plate", "sparrow.open_loops", {}),
            ("My open commitments", "sparrow.open_loops", {}),
            ("Brief me on Boost Run", "sparrow.brief", {"subject": "boost run"}),
            ("What do we know about Hugh?", "sparrow.brief", {"subject": "hugh"}),
            ("Catch me up on Point72", "sparrow.brief", {"subject": "point72"}),
            ("What did we decide about pricing?", "sparrow.search", {"query": "What did we decide about pricing"}),
            ("What did Hugh say about the deck?", "sparrow.search", {"query": "What did Hugh say about the deck"}),
            ("Ask Sparrow when the demo is", "sparrow.search", {"query": "when the demo is"}),
            ("Search my notes for the Archer offer", "sparrow.search", {"query": "the archer offer"}),
        ):
            self.assertEqual(match_sparrow_intent(sentence), (tool, arguments), sentence)

    def test_general_questions_do_not_route_to_sparrow(self):
        self.assertIsNone(match_sparrow_intent("what is open source"))
        self.assertEqual(match_sparrow_intent("what is open"), ("sparrow.open_loops", {}))

    def test_local_intents_still_win(self):
        self.assertEqual(plan_request("what do you remember", sparrow=True).steps[0].tool, "memory.recall")
        self.assertEqual(plan_request("forget my name", sparrow=True).steps[0].tool, "memory.forget")
        self.assertEqual(plan_request("stop", sparrow=True).kind, "cancel")

    def test_existing_intents_are_unchanged(self):
        self.assertEqual(plan_request("open YouTube", sparrow=True).steps[0].tool, "roku.launch")
        self.assertEqual(plan_request("who directed Interstellar", sparrow=True).steps[0].tool, "web.wikipedia")

    def test_the_word_email_does_not_block_a_sparrow_question(self):
        self.assertEqual(plan_request("what did Hugh say about the email", sparrow=True).steps[0].tool, "sparrow.search")
        self.assertEqual(plan_request("search my notes for the email from Hugh", sparrow=True).steps[0].tool, "sparrow.search")

    def test_dont_forget_does_not_become_a_delete(self):
        self.assertEqual(plan_request("don't forget what I owe Hugh", sparrow=True).kind, "clarify")


class SpokenForms(unittest.TestCase):
    def test_open_loops(self):
        speech = speak_open_loops({"items": [{"text": f"item {i}"} for i in range(5)]}, "Hugh")
        self.assertTrue(speech.startswith("5 open items with Hugh."))
        self.assertIn("item 2", speech)
        self.assertNotIn("item 3", speech)
        long = speak_open_loops({"items": [{"text": "x" * 400}]})
        self.assertLess(len(long), 150)
        self.assertEqual(speak_open_loops({"items": []}, "Hugh"), "Sparrow has nothing open with Hugh.")

    def test_search(self):
        speech = speak_search({"results": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}, "demo")
        self.assertEqual(speech, "From Sparrow: a b")
        self.assertLess(len(speak_search({"results": [{"text": "x" * 400}]})), 180)
        self.assertEqual(speak_search({"results": []}, "demo"), "Sparrow found nothing about demo.")

    def test_person(self):
        speech = speak_person(
            {
                "person": {"name": "Hugh", "affiliations": ["Boost Run"]},
                "open_loops": [{"text": "send the deck"}],
                "co_occurs_with": ["Andy"],
            },
            "hugh",
        )
        for expected in ("Hugh.", "Boost Run", "send the deck", "Andy"):
            self.assertIn(expected, speech)

    def test_company(self):
        self.assertEqual(speak_org({"org": {"name": "Point72", "item_count": 2}}, "x"), "Point72: 2 linked items in Sparrow.")

    def test_outbox_kinds(self):
        self.assertEqual(speak_outbox_item({"kind": "reminder", "text": "call"}), "Sparrow reminder: call")
        for kind in ("notify", "query", "url", "other"):
            self.assertEqual(speak_outbox_item({"kind": kind, "text": "call"}), "From Sparrow: call")
        self.assertEqual(speak_outbox_item({"kind": "notify", "text": "  a\n  b "}), "From Sparrow: a b")
        self.assertEqual(speak_outbox_item({"kind": "notify", "text": " "}), "")
        self.assertLessEqual(len(speak_outbox_item({"text": "x" * 500})), 320)

    def test_private_items_are_not_read_aloud(self):
        allowed = {"text": "public note", "disclosure_action": "offer"}
        denied = {"text": "private note", "disclosure_action": "deny"}
        speech = speak_search({"results": [denied, allowed]}, "x")
        self.assertIn("public note", speech)
        self.assertNotIn("private note", speech)
        self.assertIn("held back 1", speech)
        alone = speak_search({"results": [denied]}, "x")
        self.assertNotIn("private note", alone)
        self.assertIn("won't read it aloud", alone)
        loops = speak_open_loops({"items": [denied]})
        self.assertNotIn("private note", loops)
        self.assertNotIn("held", speak_search({"results": [{"text": "unlabeled"}]}, "x"))


class Ask(SparrowCase):
    def test_sends_the_mcp_token_and_is_audited_at_l0(self):
        self.server.routes["tool:open_loops"] = {"ok": True, "result": {"items": [{"text": "send the deck"}]}}
        result = self.agent.handle("What's on my plate?")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("send the deck", result["speech"])
        request = self.server.requests[0]
        self.assertEqual(request.auth, "Bearer mcp-secret")
        self.assertEqual(request.method, "POST")
        self.assertTrue(request.url.endswith("/mcp/tool"))
        row = self.store.db.execute("SELECT tool, permission, result FROM audit").fetchone()
        self.assertEqual(tuple(row), ("sparrow.open_loops", "L0", "ok"))

    def test_search_asks_for_five(self):
        self.server.routes["tool:memory_search"] = {"ok": True, "result": {"results": [{"text": "demo is Friday"}]}}
        self.agent.handle("Ask Sparrow when the demo is")
        self.assertEqual(self.server.requests[0].body["arguments"], {"query": "when the demo is", "limit": 5})

    def test_brief_falls_back_from_person_to_company(self):
        self.server.routes["tool:person_context"] = {"ok": True, "result": {"found": False}}
        self.server.routes["tool:org_brief"] = {"ok": True, "result": {"org": {"name": "Boost Run", "item_count": 3}}}
        result = self.agent.handle("Brief me on Boost Run")
        self.assertIn("Boost Run: 3 linked items", result["speech"])

    def test_brief_with_nothing_found_says_so(self):
        self.server.routes["tool:person_context"] = {"ok": True, "result": {"found": False}}
        self.server.routes["tool:org_brief"] = {"ok": True, "result": {"found": False}}
        self.assertEqual(self.agent.handle("Brief me on Zed")["speech"], "Sparrow has nothing on zed.")

    def test_a_sparrow_error_is_not_reported_as_nothing_found(self):
        error = {"ok": False, "error": "database is locked"}
        self.server.routes["tool:person_context"] = error
        self.server.routes["tool:org_brief"] = error
        result = self.agent.handle("Brief me on Hugh")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("database is locked", result["speech"])
        self.assertNotIn("nothing", result["speech"])

    def test_tool_error_retries_once_and_speaks_the_text(self):
        self.server.routes["tool:open_loops"] = {"ok": False, "error": "try later"}
        result = self.agent.handle("What's on my plate?")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["speech"], "try later")
        self.assertEqual(len(self.server.requests), 2)

    def test_unreachable_retries_once_and_other_tools_still_work(self):
        def down(request):
            raise urllib.error.URLError("no route to host")

        self.server.routes["tool:open_loops"] = down
        result = self.agent.handle("What's on my plate?")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("could not reach Sparrow", result["speech"])
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.agent.handle("open YouTube")["status"], "COMPLETED")

    def test_bad_token_is_not_retried(self):
        self.server.routes["tool:open_loops"] = http_error(401)
        result = self.agent.handle("What's on my plate?")
        self.assertEqual(result["speech"], "Sparrow rejected the token.")
        self.assertEqual(len(self.server.requests), 1)

    def test_other_http_error_retries_once(self):
        self.server.routes["tool:open_loops"] = http_error(500)
        result = self.agent.handle("What's on my plate?")
        self.assertEqual(result["speech"], "Sparrow answered 500.")
        self.assertEqual(len(self.server.requests), 2)

    def test_a_returned_command_is_only_spoken(self):
        self.server.routes["tool:memory_search"] = {"ok": True, "result": {"results": [{"text": "turn off the TV"}]}}
        result = self.agent.handle("Ask Sparrow what to do")
        self.assertEqual(self.roku.calls, [])
        self.assertEqual([item.tool for item in result["results"]], ["sparrow.search"])

    def test_tools_time_out_at_fifteen_seconds(self):
        for name in ("sparrow.open_loops", "sparrow.search", "sparrow.brief"):
            tool = self.registry.tools[name]
            self.assertEqual((tool.permission, tool.side_effect, tool.timeout, tool.idempotent), ("L0", "read", 15, True))


class Transport(SparrowCase):
    def test_refuses_to_send_a_token_over_plain_http(self):
        self.cfg.base_url = "http://sparrow.example"
        with self.assertRaises(SparrowError):
            self.client.call_tool("open_loops", {})
        self.assertEqual(self.server.requests, [])

    def test_loopback_is_allowed_for_development(self):
        self.cfg.base_url = "http://127.0.0.1:8000"
        self.client.call_tool("open_loops", {})
        self.assertEqual(len(self.server.requests), 1)

    def test_redirects_are_not_followed(self):
        from jarvis.sparrow import _NoRedirect

        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example"))


class Share(SparrowCase):
    def _runtime(self, share=True):
        self.cfg.share_transcripts = share
        return {"sparrow": self.client, "settings": self.settings}

    def test_sends_the_device_token_and_strips_the_wake_word(self):
        thread = share_with_sparrow(self._runtime(), "Jarvis, remind Hugh about the deck tomorrow.", 100.0)
        thread.join(5)
        request = self.server.requests[0]
        self.assertEqual(request.auth, "Bearer device-secret")
        self.assertTrue(request.url.endswith("/capture/external"))
        self.assertEqual(request.body["text"], "remind Hugh about the deck tomorrow.")
        self.assertEqual(request.body["kind"], "external")
        self.assertEqual(request.body["device_id"], "jarvis-pi")
        self.assertEqual(request.body["confidence"], 0.8)
        self.assertEqual(request.body["started_at"], 100)
        self.assertIn("ended_at", request.body)

    def test_no_device_token_means_no_request(self):
        self.device_file.unlink()
        self.assertIsNone(share_with_sparrow(self._runtime(), "Jarvis, hello there"))
        self.assertEqual(self.server.requests, [])

    def test_off_by_default_means_no_request(self):
        self.assertIsNone(share_with_sparrow(self._runtime(share=False), "Jarvis, hello there"))
        self.assertEqual(self.server.requests, [])

    def test_local_memory_never_leaves_the_pi(self):
        for sentence in (
            "Jarvis, remember that my door code is 1234",
            "Jarvis, my name is Justin",
            "Jarvis, I prefer science fiction",
            "Jarvis, forget the door code",
            "Jarvis, my password is hunter2",
            "yes",
            "no",
        ):
            self.assertIsNone(share_with_sparrow(self._runtime(), sentence), sentence)
        self.assertEqual(self.server.requests, [])

    def test_speech_without_the_wake_word_is_not_sent(self):
        for sentence in ("what about Friday", "pass the salt", "open YouTube"):
            self.assertIsNone(share_with_sparrow(self._runtime(), sentence), sentence)
        self.assertEqual(self.server.requests, [])

    def test_a_turn_that_used_memory_stays_local(self):
        result = {"results": [SimpleNamespace(tool="memory.recall")]}
        self.assertTrue(stays_local("what kinds of movies are good", result))
        self.assertFalse(stays_local("remind Hugh about the deck", {"results": []}))

    def test_a_failed_send_does_not_raise(self):
        def down(request):
            raise urllib.error.URLError("down")

        self.server.routes["capture/external"] = down
        thread = share_with_sparrow(self._runtime(), "Jarvis, remind Hugh about the deck")
        thread.join(5)
        self.assertFalse(thread.is_alive())


class Pairing(SparrowCase):
    def test_saves_the_token_on_success(self):
        self.device_file.unlink()
        self.server.routes["phone/pair/claim"] = {"token": "new-device-token", "device_id": "abc"}
        self.assertEqual(self.client.pair("123456"), "abc")
        self.assertEqual(self.device_file.read_text(encoding="utf-8").strip(), "new-device-token")
        self.assertEqual(self.server.requests[0].body, {"code": "123456", "name": "Jarvis", "platform": "other"})

    def test_a_wrong_code_saves_nothing(self):
        self.device_file.unlink()
        self.server.routes["phone/pair/claim"] = http_error(400, {"error": "bad code"})
        with self.assertRaises(SparrowError) as caught:
            self.client.pair("000000")
        self.assertIn("bad code", str(caught.exception))
        self.assertFalse(self.device_file.exists())


class Listen(SparrowCase):
    def _clock(self):
        self.now = 1000.0
        return lambda: self.now

    def test_spoken_once_with_the_device_token(self):
        self.server.routes["phone/outbox"] = {"items": [{"kind": "notify", "text": "the demo moved"}]}
        spoken = []
        poller = OutboxPoller(self.client, spoken.append, 60, clock=self._clock())
        self.assertEqual(poller.tick(), ["From Sparrow: the demo moved"])
        self.assertEqual(self.server.requests[0].auth, "Bearer device-secret")
        self.assertEqual(spoken, ["From Sparrow: the demo moved"])

    def test_respects_poll_seconds(self):
        poller = OutboxPoller(self.client, lambda text: None, 60, clock=self._clock())
        poller.tick()
        poller.tick()
        self.assertEqual(len(self.server.requests), 1)
        self.now += 60
        poller.tick()
        self.assertEqual(len(self.server.requests), 2)

    def test_not_polled_in_quiet_hours(self):
        poller = OutboxPoller(self.client, lambda text: None, 60, clock=self._clock())
        scheduler = Scheduler(self.store, lambda text: None, None, "22:00", "07:00", outbox=poller)
        scheduler.tick(UTC, datetime(2026, 10, 26, 23, 0))
        self.assertEqual(self.server.requests, [])
        scheduler.tick(UTC, NOON)
        self.assertEqual(len(self.server.requests), 1)

    def test_not_polled_while_jarvis_cannot_be_heard(self):
        poller = OutboxPoller(self.client, lambda text: None, 60, clock=self._clock())
        Scheduler(self.store, lambda text: None, None, outbox=poller, audible=lambda: False).tick(UTC, NOON)
        self.assertEqual(self.server.requests, [])

    def test_a_failed_poll_does_not_stop_reminders(self):
        def down(request):
            raise urllib.error.URLError("down")

        self.server.routes["phone/outbox"] = down
        self.store.add_reminder("oven", "2020-01-01T00:00:00+00:00")
        spoken = []
        poller = OutboxPoller(self.client, spoken.append, 60, clock=self._clock())
        Scheduler(self.store, spoken.append, None, outbox=poller).tick(UTC, NOON)
        self.assertEqual(spoken, ["Reminder: oven."])

    def test_a_speech_failure_does_not_stop_the_scheduler_or_lose_the_item(self):
        self.server.routes["phone/outbox"] = {"items": [{"text": "a"}, {"text": "b"}]}
        attempts = []

        def speaker(text):
            attempts.append(text)
            if len(attempts) == 1:
                raise RuntimeError("audio died")

        poller = OutboxPoller(self.client, speaker, 60, clock=self._clock())
        self.assertEqual(poller.tick(), ["From Sparrow: b"])
        self.assertEqual(poller.unspoken, ["From Sparrow: a"])
        self.server.routes["phone/outbox"] = {"items": []}
        self.now += 60
        self.assertEqual(poller.tick(), ["From Sparrow: a"])
        self.assertEqual(poller.unspoken, [])

    def test_items_never_become_actions(self):
        self.server.routes["phone/outbox"] = {"items": [{"kind": "query", "text": "turn off the TV"}, {"kind": "url", "text": "https://x"}]}
        poller = OutboxPoller(self.client, lambda text: None, 60, clock=self._clock())
        poller.tick()
        self.assertEqual(self.roku.calls, [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM audit").fetchone()[0], 0)


class Defaults(unittest.TestCase):
    def test_every_channel_is_off(self):
        cfg = load_sparrow_settings({})
        self.assertFalse(cfg.enabled)
        self.assertFalse(cfg.share_transcripts)
        self.assertFalse(cfg.poll_outbox)
        shipped = load_settings().sparrow
        self.assertFalse(shipped.enabled or shipped.share_transcripts or shipped.poll_outbox)


if __name__ == "__main__":
    unittest.main()
