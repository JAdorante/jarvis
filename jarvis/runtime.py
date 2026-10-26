"""Start memory, the face, reminders, and the microphone loop."""

import threading
import time
from datetime import datetime, timezone

from jarvis.agent.orchestrator import Agent
from jarvis.agent.permissions import PermissionEngine
from jarvis.audio.listen import Ear, capture_once, find_microphone
from jarvis.audio.lock import AudioLock
from jarvis.audio.speech import Speaker
from jarvis.config import load_settings
from jarvis.devices.roku import RokuClient
from jarvis.memory.store import MemoryStore
from jarvis.model.gateway import ModelGateway
from jarvis.observe import configure_logging, log_event
from jarvis.scheduler import Scheduler
from jarvis.tools.builtin import build_tools
from jarvis.visual.face_client import set_face


def recover_interrupted(store):
    """Leave reminders alone. Do not repeat a half-finished TV command."""
    recovered = []
    for task in store.unfinished_tasks():
        store.update_task(
            task["id"],
            "FAILED",
            "Interrupted by a restart. I did not repeat it.",
        )
        recovered.append(task["id"])
    return recovered


def build_runtime(settings=None, wiki_fetch=None, roku=None, model=None, speak=None):
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(settings.data_dir)
    store = MemoryStore(settings.data_dir / "jarvis.sqlite")
    imported = store.import_legacy(settings.data_dir / "user.txt", settings.data_dir / "existence.txt")
    recovered = recover_interrupted(store)
    log_event(logger, event="startup", imported_memories=imported, recovered_tasks=recovered)
    if roku is None:
        roku = RokuClient(settings.roku_host, settings.roku_port, settings.roku_timeout)
    registry = build_tools(
        store,
        roku,
        wiki_fetch=wiki_fetch,
        legacy_user_file=settings.data_dir / "user.txt",
    )
    permissions = PermissionEngine(settings.roku_standing_authorization)
    if model is None:
        model = ModelGateway(settings)
    face_url = f"http://127.0.0.1:{settings.face_port}/state"

    def face(state, activity=""):
        set_face(state, activity, url=face_url)

    agent = Agent(settings, store, registry, permissions, model=model, face=face, logger=logger)
    speaker = speak or Speaker(settings).speak
    scheduler = Scheduler(store, _locked_speak(settings, speaker, face), roku, settings.quiet_start, settings.quiet_end)
    return {
        "settings": settings,
        "store": store,
        "agent": agent,
        "roku": roku,
        "scheduler": scheduler,
        "speaker": speaker,
        "face": face,
        "logger": logger,
    }


def _locked_speak(settings, speaker, face):
    def speak(text):
        face("speaking", "")
        with AudioLock(settings.data_dir / "audio.lock"):
            speaker(text)
        face("idle", "")

    return speak


def run_listener(settings=None, runtime=None):
    runtime = runtime or build_runtime(settings)
    settings = runtime["settings"]
    agent = runtime["agent"]
    face = runtime["face"]
    speak = _locked_speak(settings, runtime["speaker"], face)
    ear = Ear(settings)
    stop = threading.Event()

    def reminders():
        while not stop.is_set():
            now_utc = datetime.now(timezone.utc).replace(microsecond=0)
            runtime["scheduler"].tick(now_utc, datetime.now().astimezone())
            stop.wait(1)

    thread = threading.Thread(target=reminders, name="jarvis-scheduler", daemon=True)
    thread.start()
    try:
        from jarvis.visual.server import serve

        serve(settings.face_host, settings.face_port, background=True)
    except OSError as exc:
        log_event(runtime["logger"], event="face_unavailable", error=str(exc))
    device, name = find_microphone()
    print(f"Microphone: {name}")
    print("Speak, then pause. Press Ctrl+C to stop.")
    log_event(runtime["logger"], event="listening", microphone=name)
    try:
        while True:
            face("listening", "")
            print("\nListening...")
            with AudioLock(settings.data_dir / "audio.lock"):
                text = capture_once(device, settings)
            if not text:
                continue
            face("transcribing", "")
            now = time.monotonic()
            if not ear.accepts(text, now):
                print(f"Ignored: {text}")
                face("idle", "")
                continue
            print(f"You said: {text}")
            result = agent.handle(text, followup_query=agent.last_lookup)
            ear.mark(now)
            print(result["speech"])
            speak(result["speech"])
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        face("offline", "")


def ask_once(text, speak=True):
    runtime = build_runtime()
    result = runtime["agent"].handle(text)
    print(result["speech"])
    if speak:
        _locked_speak(runtime["settings"], runtime["speaker"], runtime["face"])(result["speech"])
    runtime["store"].close()
    return result
