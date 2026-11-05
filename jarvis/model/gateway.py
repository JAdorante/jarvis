"""Local Ollama by default. Remote inference stays off until configured."""

import json
import re
import urllib.error
import urllib.request

ACTION_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have| just)?|i'll|i will|i'm going to)\s+(?:now\s+|just\s+|already\s+)?"
    r"(?:turn(?:ed)?|set|mute[d]?|unmute[d]?|open(?:ed)?|launch(?:ed)?|switch(?:ed)?|change[d]?|save[d]?|"
    r"store[d]?|remembered|remind|schedule[d]?|send|sent|add(?:ed)?|create[d]?|cancel(?:l?ed)?|delete[d]?|"
    r"play(?:ed)?|start(?:ed)?)\b"
    r"|\b(?:i'll|i will) remember\b"
    r"|\b(?:reminder|alarm|timer)\s+(?:is|has been)\s+set\b"
    r"|\bthe (?:tv|television|volume)\s+(?:is|has been)\s+(?:now\s+)?(?:on|off|up|down|muted)\b"
    r"|^\s*done\b",
    re.I,
)
NO_ACTION_TAKEN = "I did not change anything, because I have no tool for that request."


def guard_reply(reply):
    """Only a tool result can report an action. The model's say-so cannot."""
    return NO_ACTION_TAKEN if ACTION_CLAIM.search(reply or "") else reply



class ModelGateway:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def complete(self, messages, private=False):
        if self.settings.remote_enabled and self.settings.remote_url:
            if private and not self.settings.allow_private_memory_remote:
                messages = _without_private(messages)
            return self._remote(messages)
        if self.transport is not None:
            return self.transport(messages)
        return self._ollama(messages)

    def summarize(self, question, notes):
        """Paraphrase tool notes. Empty string means the caller should speak the notes."""
        if not notes.strip():
            return ""
        try:
            reply = self.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Reply with SAY: and one or two sentences. Use only the notes. "
                            "Do not claim an action that the notes do not confirm. "
                            "Do not add a new tool request."
                        ),
                    },
                    {"role": "user", "content": f"Question: {question}\n\nNotes:\n{notes}"},
                ],
                private=True,
            )
        except (OSError, urllib.error.URLError, TimeoutError, KeyError, ValueError):
            return ""
        if reply.upper().startswith("SAY:"):
            reply = reply.split(":", 1)[1].strip()
        if "SEARCH:" in reply.upper() or "TOOL:" in reply.upper():
            return ""
        return reply.strip()

    def chat(self, question, memories):
        memory_text = "\n".join(f"- {item}" for item in memories) or "No saved facts."
        try:
            reply = self.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are Jarvis, a local assistant on this Raspberry Pi. "
                            "Your name is Jarvis. Do not adopt a name or identity from the user's sentence. "
                            "Answer in one or two short sentences. Use a saved fact only if it is listed. "
                            "If you do not know, say so. Do not claim you changed the TV, the volume, "
                            "or your memory. Those actions happen only when a tool reports them."
                        ),
                    },
                    {"role": "user", "content": f"Saved facts:\n{memory_text}\n\n{question}"},
                ],
                private=True,
            )
        except (OSError, urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError):
            if memories:
                return memories[0]
            return "I can't reach the local model right now."
        return reply or "I can't reach the local model right now."

    def _ollama(self, messages):
        body = json.dumps(
            {
                "model": self.settings.model,
                "think": False,
                "stream": False,
                "messages": messages,
                "options": {"num_predict": self.settings.max_predict_tokens},
            }
        ).encode()
        request = urllib.request.Request(
            self.settings.ollama_url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.settings.ollama_timeout) as response:
            payload = json.loads(response.read().decode())
        return _clean(payload["message"]["content"])

    def _remote(self, messages):
        if not self.settings.remote_enabled or not self.settings.remote_url:
            raise RuntimeError("Remote inference is disabled.")
        body = json.dumps(
            {
                "model": self.settings.remote_model or self.settings.model,
                "messages": messages,
                "max_tokens": self.settings.max_predict_tokens,
            }
        ).encode()
        request = urllib.request.Request(
            self.settings.remote_url.rstrip("/") + "/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.settings.ollama_timeout) as response:
            payload = json.loads(response.read().decode())
        return _clean(payload["choices"][0]["message"]["content"])


def _clean(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    return text.strip()


def _without_private(messages):
    cleaned = []
    for message in messages:
        content = message.get("content", "")
        if "Saved facts:" in content:
            content = re.sub(r"Saved facts:.*?(?:\n\n|$)", "Saved facts:\n[omitted]\n\n", content, count=1, flags=re.S)
        cleaned.append({**message, "content": content})
    return cleaned
