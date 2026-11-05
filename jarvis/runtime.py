"""Start memory, the face, reminders, and the microphone loop."""

import re
import threading
import time
from datetime import datetime, timezone

from jarvis.agent.orchestrator import Agent
from jarvis.agent.permissions import PermissionEngine
from jarvis.audio.listen import Ear, capture_once, find_microphone
from jarvis.audio.lock import AudioLock
from jarvis.audio.speech import Speaker
from jarvis.config import load_settings
from jarvis.devices.roku import RokuClient, RokuError
from jarvis.memory.store import MemoryStore
from jarvis.model.gateway import ModelGateway
from jarvis.observe import configure_logging, log_event
from jarvis.scheduler import Scheduler
from jarvis.sparrow import OutboxPoller, SparrowClient, build_sparrow_tools
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


def build_runtime(settings=None, wiki_fetch=None, roku=None, model=None, speak=None, sparrow_opener=None):
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
        legacy_existence_file=settings.data_dir / "existence.txt",
    )
    sparrow = None
    sparrow_cfg = getattr(settings, "sparrow", None)
    if sparrow_cfg and sparrow_cfg.enabled:
        sparrow = SparrowClient(sparrow_cfg, opener=sparrow_opener, logger=logger)
        for tool in build_sparrow_tools(sparrow):
            registry.add(tool)
    permissions = PermissionEngine(settings.roku_standing_authorization)
    if model is None:
        model = ModelGateway(settings)
    face_url = f"http://127.0.0.1:{settings.face_port}/state"

    def face(state, activity=""):
        set_face(state, activity, url=face_url)

    agent = Agent(settings, store, registry, permissions, model=model, face=face, logger=logger)
    speaker = speak or Speaker(settings).speak
    outbox = None
    if sparrow is not None and sparrow_cfg.poll_outbox:
        outbox = OutboxPoller(sparrow, _locked_speak(settings, speaker, face), sparrow_cfg.poll_seconds, logger=logger)

    def call_tool(name, arguments, trigger):
        return registry.call(name, arguments, {}, permissions, store, trigger)

    scheduler = Scheduler(
        store,
        _locked_speak(settings, speaker, face),
        call_tool,
        settings.quiet_start,
        settings.quiet_end,
        outbox=outbox,
        audible=_audible_check(settings, roku),
        logger=logger,
    )
    return {
        "sparrow": sparrow,
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
            delivered = speaker(text)
        face("idle", "")
        return delivered

    return speak


def _audible_check(settings, roku):
    """Jarvis is only heard while the TV shows the Pi's input. Unset means this cannot be checked."""
    if not settings.roku_pi_input:
        return None

    def audible():
        try:
            return roku.active_app().get("id") == settings.roku_pi_input
        except RokuError:
            return True

    return audible


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
            try:
                runtime["scheduler"].tick(now_utc, datetime.now().astimezone())
            except Exception as exc:
                log_event(runtime["logger"], event="scheduler_error", part="tick", error=str(exc))
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
            text = capture_once(device, settings, AudioLock(settings.data_dir / "audio.lock"))
            if not text:
                continue
            face("transcribing", "")
            now = time.monotonic()
            heard_at = time.time()
            if not ear.accepts(text, now, expecting_reply=agent.expecting_reply()):
                print("Ignored a sentence not addressed to me.")
                face("idle", "")
                continue
            print(f"You said: {text}")
            result = agent.handle(text)
            print(result["speech"])
            share_with_sparrow(runtime, text, heard_at, result)
            speak(result["speech"])
            ear.mark(time.monotonic())
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        face("offline", "")


def share_with_sparrow(runtime, text, heard_at=None, result=None):
    """SHARE: send a sentence addressed to Jarvis, wake word removed, on a background thread.

    Follow-up replies carry no wake word and may be room chatter, so they stay here.
    Trust boundary 3: anything Jarvis keeps as local memory stays on the Pi.
    """
    client = runtime.get("sparrow")
    cfg = runtime["settings"].sparrow
    if client is None or not cfg.share_transcripts:
        return None
    from jarvis.agent.planning import strip_wake
    from jarvis.audio.listen import has_wake_word
    from jarvis.sparrow import stays_local

    wake_word = runtime["settings"].wake_word
    if not has_wake_word(re.findall(r"[a-z']+", text.lower()), wake_word):
        return None
    cleaned = strip_wake(text, wake_word)
    if stays_local(cleaned, result):
        return None
    return client.share_later(cleaned, started_at=heard_at, ended_at=time.time())


def ask_once(text, speak=True):
    runtime = build_runtime()
    result = runtime["agent"].handle(text)
    print(result["speech"])
    if speak:
        _locked_speak(runtime["settings"], runtime["speaker"], runtime["face"])(result["speech"])
    runtime["store"].close()
    return result
