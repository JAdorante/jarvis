"""Bounded goal loop. Tool results are the only evidence Jarvis will report."""

import threading
import time

from jarvis.agent.planning import plan_request, strip_wake
from jarvis.observe import log_event


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
        self.last_lookup = ""

    def cancel(self):
        self._cancel.set()

    def handle(self, text, followup_query="", now=None):
        self._cancel.clear()
        cleaned = strip_wake(text)
        plan = plan_request(cleaned, now=now)
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
            return self._finish(task_id, cleaned, "WAITING_FOR_USER", plan.speech)

        if plan.kind == "approval":
            self.store.update_task(task_id, "WAITING_FOR_APPROVAL", plan.speech)
            self.face("waiting", "Approval required")
            return self._finish(task_id, cleaned, "WAITING_FOR_APPROVAL", plan.speech)

        if plan.kind == "talk":
            speech = plan.speech or self._chat(cleaned)
            self.store.update_task(task_id, "COMPLETED", speech)
            self.face("speaking", "")
            return self._finish(task_id, cleaned, "COMPLETED", speech)

        started = self.clock()
        self.store.update_task(task_id, "RUNNING", "")
        results = []
        status = "COMPLETED"
        for step in plan.steps[: self.settings.max_steps]:
            if self._cancel.is_set():
                status = "CANCELLED"
                break
            if self.clock() - started > self.settings.max_seconds:
                status = "FAILED"
                results.append(_budget_result())
                break
            self.face("acting", step.tool)
            self.store.update_task(task_id, "WAITING_FOR_TOOL", step.tool)
            result = self._run_step(step, results, cleaned)
            if _should_retry(step, result, self.registry):
                self.store.update_task(task_id, "RETRYING", step.tool)
                self.sleeper(self.settings.retry_wait_seconds)
                if self._cancel.is_set():
                    status = "CANCELLED"
                    break
                result = self._run_step(step, results, cleaned)
            results.append(result)
            if step.tool == "web.wikipedia" and result.output.get("query"):
                self.last_lookup = result.output["query"]
            if result.error_code == "needs_approval":
                status = "WAITING_FOR_APPROVAL"
                break
            if _failed(result):
                status = "FAILED"
                break
        if status == "CANCELLED":
            speech = "Stopped. I did not start the remaining steps."
        else:
            speech = " ".join(item.speech for item in results if item.speech).strip()
            if plan.speech and status == "COMPLETED":
                speech = f"{speech} {plan.speech}".strip()
            if not speech:
                speech = "I could not complete that."
                status = "FAILED"
        self.store.update_task(task_id, status, speech[:500])
        self.face("speaking" if status == "COMPLETED" else "error", "")
        if followup_query and not self.last_lookup:
            self.last_lookup = followup_query
        return self._finish(task_id, cleaned, status, speech, results)

    def _run_step(self, step, previous, trigger):
        arguments = dict(step.arguments)
        if step.tool == "web.wikipedia" and followup_replaces(arguments.get("query", ""), self.last_lookup):
            arguments["query"] = f"{self.last_lookup} {arguments['query']}"
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
        return self.model.chat(text, memories)

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


def followup_replaces(query, last_lookup):
    import re

    if not last_lookup or not query:
        return False
    return bool(re.match(r"(what|how) about\b", query.lower()))


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
    if not result.ok and result.retryable:
        return True
    if result.error_code == "unverified":
        return True
    return False


def _budget_result():
    from jarvis.tools.registry import ToolResult

    return ToolResult(
        ok=False,
        output={},
        speech="I stopped because that request ran too long.",
        error_code="budget",
        tool="agent",
    )
