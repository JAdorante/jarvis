"""Bounded goal loop. Tool results are the only evidence Jarvis will report."""

import re
import threading
import time

from jarvis.agent.planning import Step, is_cancel, is_no, is_yes, plan_request, strip_wake
from jarvis.model.gateway import guard_reply
from jarvis.observe import log_event

APPROVAL_PROMPT = "Say yes to go ahead, or no to cancel."


class Agent:
    def __init__(self, settings, store, registry, permissions, model=None, face=None, sleeper=None, logger=None, clock=None):
        self.settings = settings
        self.store = store
        self.registry = registry
        self.permissions = permissions
        self.model = model
        self.face = face or (lambda state, activity="": None)
        self.sleeper = sleeper or time.sleep
        self.logger = logger
        self.clock = clock or time.monotonic
        self._cancel = threading.Event()
        self.active_task = ""
        self.pending = None
        self.topic = None
        self.topic_before = None

    def cancel(self):
        self._cancel.set()

    def expecting_reply(self):
        """True while a question or an approval is open. The microphone uses this."""
        return self._live_pending() is not None

    def handle(self, text, now=None):
        self._cancel.clear()
        cleaned = strip_wake(text, getattr(self.settings, "wake_word", "jarvis"))
        pending = self._live_pending()
        self.pending = None
        if pending is not None:
            resolved = self._resolve_pending(pending, cleaned, now)
            if isinstance(resolved, dict):
                return resolved
            cleaned = resolved
        cleaned = self._apply_topic(cleaned)
        sparrow = getattr(self.settings, "sparrow", None)
        plan = plan_request(cleaned, now=now, sparrow=bool(sparrow and sparrow.enabled))
        task_id = self.store.create_task(plan.objective)
        self.active_task = task_id
        self.face("thinking", plan.objective[:80])
        log_event(self.logger, event="plan", task=task_id, kind=plan.kind, steps=[step.tool for step in plan.steps])

        if plan.kind == "cancel":
            self.store.update_task(task_id, "CANCELLED", plan.speech)
            self.face("idle", "")
            return self._finish(task_id, cleaned, "CANCELLED", plan.speech)

        if plan.kind == "clarify":
            self.store.update_task(task_id, "WAITING_FOR_USER", plan.speech)
            self.face("waiting", "Need a detail")
            if plan.reply_template:
                self._hold({"kind": "question", "task": task_id, "template": plan.reply_template})
            return self._finish(task_id, cleaned, "WAITING_FOR_USER", plan.speech)

        if plan.kind == "talk":
            speech = plan.speech or self._chat(cleaned)
            self.store.update_task(task_id, "COMPLETED", speech)
            self.face("speaking", "")
            return self._finish(task_id, cleaned, "COMPLETED", speech)

        return self._execute(task_id, plan.steps[: self.settings.max_steps], cleaned, plan.speech)

    def _execute(self, task_id, steps, trigger, closing_speech=""):
        started = self.clock()
        self.store.update_task(task_id, "RUNNING", "")
        results = []
        status = "COMPLETED"
        self.topic = None
        for index, step in enumerate(steps):
            if self._cancel.is_set():
                status = "CANCELLED"
                break
            if self.clock() - started > self.settings.max_seconds:
                status = "FAILED"
                results.append(_budget_result())
                break
            self.face("acting", step.tool)
            self.store.update_task(task_id, "WAITING_FOR_TOOL", step.tool)
            result = self._run_step(step, results, trigger)
            if _should_retry(step, result, self.registry):
                self.store.update_task(task_id, "RETRYING", step.tool)
                self.sleeper(self.settings.retry_wait_seconds)
                if self._cancel.is_set():
                    status = "CANCELLED"
                    break
                result = self._run_step(step, results, trigger)
            results.append(result)
            self._remember_topic(step, result)
            if result.error_code == "needs_approval":
                status = "WAITING_FOR_APPROVAL"
                arguments = result.output.get("approve") or step.arguments
                self._hold(
                    {
                        "kind": "approval",
                        "task": task_id,
                        "steps": [Step(step.tool, arguments)] + list(steps[index + 1 :]),
                        "closing": closing_speech,
                    }
                )
                break
            if _failed(result):
                status = "FAILED"
                break
        if status == "CANCELLED":
            speech = "Stopped. I did not start the remaining steps."
        else:
            speech = " ".join(item.speech for item in results if item.speech).strip()
            if status == "WAITING_FOR_APPROVAL":
                speech = f"{speech} {APPROVAL_PROMPT}".strip()
            if closing_speech and status == "COMPLETED":
                speech = f"{speech} {closing_speech}".strip()
            if not speech:
                speech = "I could not complete that."
                status = "FAILED"
        self.store.update_task(task_id, status, speech[:500])
        self.face("speaking" if status == "COMPLETED" else "waiting" if status == "WAITING_FOR_APPROVAL" else "error", "")
        return self._finish(task_id, trigger, status, speech, results)

    def _run_step(self, step, previous, trigger):
        arguments = dict(step.arguments)
        if step.tool == "web.wikipedia" and self._live_topic("lookup") and followup_replaces(arguments.get("query", "")):
            rest = re.sub(r"^(?:what|how) about\s+", "", arguments["query"], flags=re.I)
            arguments["query"] = f"{self.topic_before['query']} {rest}"
        context = {"previous": [item.output for item in previous]}
        return self.registry.call(
            step.tool,
            arguments,
            context,
            self.permissions,
            self.store,
            trigger,
        )

    def _chat(self, text):
        rows = self.store.search(text, limit=5, category="semantic")
        memories = [row["content"] for row in rows]
        self.face("thinking", "")
        if self.model is None:
            if memories:
                return memories[0]
            return "I'm here."
        return guard_reply(self.model.chat(text, memories))

    def _finish(self, task_id, user_text, status, speech, results=None):
        self.store.add_turn(user_text, speech, task_id)
        self.active_task = ""
        log_event(
            self.logger,
            event="turn",
            task=task_id,
            status=status,
            tools=[item.tool for item in (results or [])],
        )
        return {
            "task_id": task_id,
            "status": status,
            "speech": speech,
            "results": results or [],
        }

    # Open questions and approvals ----------------------------------------
    def _hold(self, pending):
        pending["expires"] = self.clock() + getattr(self.settings, "pending_seconds", 60)
        self.pending = pending

    def _live_pending(self):
        if self.pending is None:
            return None
        if self.clock() > self.pending["expires"]:
            self.store.update_task(self.pending["task"], "CANCELLED", "Nobody answered in time.")
            self.pending = None
        return self.pending

    def _resolve_pending(self, pending, cleaned, now):
        """Return a finished turn, or the sentence to plan as usual."""
        task_id = pending["task"]
        if pending["kind"] == "approval":
            if is_yes(cleaned):
                first = pending["steps"][0]
                self.permissions.grant(first.tool, first.arguments)
                try:
                    return self._execute(task_id, pending["steps"], cleaned, pending["closing"])
                finally:
                    self.permissions.clear_grants()
            if is_no(cleaned):
                self.store.update_task(task_id, "CANCELLED", "You said no.")
                self.face("idle", "")
                return self._finish(task_id, cleaned, "CANCELLED", "Okay, I won't do that.")
            self.store.update_task(task_id, "CANCELLED", "Replaced by a new request.")
            return cleaned
        if is_no(cleaned) or is_cancel(cleaned) or is_yes(cleaned):
            self.store.update_task(task_id, "CANCELLED", "You said never mind.")
            self.face("idle", "")
            return self._finish(task_id, cleaned, "CANCELLED", "Okay.")
        if _looks_like_answer(cleaned, now):
            self.store.update_task(task_id, "COMPLETED", "Answered.")
            return pending["template"].replace("{answer}", cleaned.strip(" .,!?"))
        self.store.update_task(task_id, "CANCELLED", "Replaced by a new request.")
        return cleaned

    # Follow-ups ----------------------------------------------------------
    def _remember_topic(self, step, result):
        if not result.ok:
            return
        expires = self.clock() + getattr(self.settings, "pending_seconds", 60)
        if step.tool == "web.wikipedia":
            before = self.topic_before
            query = step.arguments.get("query", "")
            if followup_replaces(query) and before and before["kind"] == "lookup":
                self.topic = {**before, "expires": expires}
            elif not step.arguments.get("follow"):
                self.topic = {"kind": "lookup", "query": query, "expires": expires}
        elif step.tool == "weather.forecast":
            self.topic = {
                "kind": "weather",
                "place": step.arguments.get("place", ""),
                "day": step.arguments.get("day", "today"),
                "expires": expires,
            }

    def _live_topic(self, kind):
        topic = self.topic_before
        return bool(topic and topic["kind"] == kind and self.clock() <= topic["expires"])

    def _apply_topic(self, cleaned):
        """Point "what about Friday?" at the last tool that ran, not always at Wikipedia."""
        self.topic_before = self.topic
        match = re.match(r"(?:what|how) about\s+(.+?)[.?!]*$", cleaned.strip(), flags=re.I)
        if not match or not self._live_topic("weather"):
            return cleaned
        rest = re.sub(r"^on\s+", "", match.group(1).strip(), flags=re.I)
        topic = self.topic_before
        if re.fullmatch(r"today|tomorrow|(?:mon|tues|wednes|thurs|fri|satur|sun)day", rest, flags=re.I):
            return f"what's the weather in {topic['place']} {rest}"
        return f"what's the weather in {rest} {topic['day']}"


def followup_replaces(query):
    return bool(re.match(r"(what|how) about\b", (query or "").lower()))


def _looks_like_answer(text, now):
    """A short reply that would not otherwise be a tool command."""
    text = text.strip(" .,!?")
    if len(text.split()) > 8:
        return False
    plan = plan_request(text, now=now)
    return plan.kind == "talk" and not plan.speech


def _failed(result):
    if not result.ok:
        return True
    if result.verified is False:
        return True
    return False


def _should_retry(step, result, registry):
    tool = registry.tools.get(step.tool)
    if tool is None or not tool.idempotent:
        return False
    return not result.ok and result.retryable


def _budget_result():
    from jarvis.tools.registry import ToolResult

    return ToolResult(
        ok=False,
        output={},
        speech="I stopped because that request ran too long.",
        error_code="budget",
        tool="agent",
    )
