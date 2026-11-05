"""Microphone capture and the wake-word check after transcription."""

import array
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path


ECHO_GUARD_SECONDS = 0.6
WAKE_OPENERS = {"hey", "ok", "okay", "hi", "yo"}


def find_microphone():
    result = subprocess.run(["arecord", "-l"], text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("arecord is missing. Install it with: sudo apt install -y alsa-utils")
    devices = []
    for line in result.stdout.splitlines():
        match = re.match(r"card (\d+): .*\[([^\]]+)\].*device (\d+):", line)
        if match:
            devices.append((f"plughw:{match.group(1)},{match.group(3)}", match.group(2)))
    for device, name in devices:
        if "usb" in name.lower() or "mic" in name.lower():
            return device, name
    if devices:
        return devices[0]
    raise SystemExit("No microphone found. Plug it into a USB port on the Pi.")


def record_phrase(device, settings, lock=None):
    block_bytes = int(settings.sample_rate * 0.05) * 2
    proc = subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(settings.sample_rate), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        return read_phrase(lambda: proc.stdout.read(block_bytes), block_bytes, settings, lock)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


def read_phrase(read_block, block_bytes, settings, lock=None, clock=time.monotonic):
    """Collect one sentence. `lock` is taken only once speech starts.

    Waiting in a silent room holds nothing, so a reminder can speak. Sound heard
    while something else holds the lock is Jarvis itself and is thrown away.
    """
    heard = []
    quiet_blocks = 0
    started = False
    started_at = None
    busy_until = 0.0
    try:
        while True:
            data = read_block()
            if len(data) < block_bytes:
                break
            samples = array.array("h")
            samples.frombytes(data)
            level = max(abs(sample) for sample in samples) / 32768
            now = clock()
            if not started:
                if now < busy_until or level < settings.speech_level:
                    continue
                if lock is not None and not lock.acquire(blocking=False):
                    busy_until = now + ECHO_GUARD_SECONDS
                    continue
                started = True
                started_at = now
                heard.append(data)
                continue
            heard.append(data)
            if level < settings.silence_level:
                quiet_blocks += 1
            else:
                quiet_blocks = 0
            if quiet_blocks >= int(settings.silence_seconds / 0.05):
                break
            if started_at and now - started_at > settings.max_phrase_seconds:
                break
    finally:
        if started and lock is not None:
            lock.release()
    return b"".join(heard)


def write_wav(raw, path, sample_rate):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(raw)


def transcribe(wav_path, settings):
    if not settings.whisper_bin.exists() or not settings.whisper_model.exists():
        raise SystemExit(
            "Speech recognition is not installed yet. Build whisper.cpp and download the base.en model."
        )
    result = subprocess.run(
        [
            str(settings.whisper_bin),
            "-m",
            str(settings.whisper_model),
            "-f",
            str(wav_path),
            "-nt",
            "-np",
            "-l",
            "en",
            "-t",
            "4",
        ],
        text=True,
        capture_output=True,
    )
    text = re.sub(r"\[.*?\]", "", result.stdout).strip()
    return text


def has_wake_word(words, wake_word="jarvis"):
    """The wake word opens or closes the sentence. Mentioning it midway is not addressing it."""
    wake = wake_word.lower()
    if not words:
        return False
    if words[0] == wake or words[-1] == wake:
        return True
    return len(words) > 1 and words[1] == wake and words[0] in WAKE_OPENERS


def worth_asking(text, followup_open=False, expecting_reply=False, wake_word="jarvis"):
    """Ignore noise. Act on the wake word, or on a reply during the follow-up window.

    Outside that window a TV command needs the wake word too, so a line of TV
    dialogue cannot turn the TV off.
    """
    lowered = text.lower().strip()
    if not lowered or lowered.startswith("("):
        return False
    words = re.findall(r"[a-z']+", lowered)
    if has_wake_word(words, wake_word):
        return True
    if not followup_open:
        return False
    if expecting_reply and len(words) <= 12:
        return True
    if re.search(r"\b(open|launch|resume|turn off|turn on|power off|power on|volume|mute)\b", lowered):
        return len(words) >= 2
    if len(words) <= 12 and re.match(r"(what|how) about\b|^and\b|^(he|she|they|it|that)\b", lowered):
        return True
    return False


class Ear:
    """Holds the follow-up window between turns."""

    def __init__(self, settings):
        self.settings = settings
        self.last_heard = float("-inf")

    def accepts(self, text, now, expecting_reply=False):
        followup = now - self.last_heard <= self.settings.followup_seconds
        return worth_asking(
            text,
            followup_open=followup,
            expecting_reply=expecting_reply,
            wake_word=self.settings.wake_word,
        )

    def mark(self, now):
        self.last_heard = now


def capture_once(device, settings, lock=None):
    raw = record_phrase(device, settings, lock)
    if len(raw) < settings.sample_rate * 2 * 0.4:
        return ""
    with tempfile.TemporaryDirectory() as folder:
        wav_path = Path(folder) / "heard.wav"
        write_wav(raw, wav_path, settings.sample_rate)
        return transcribe(wav_path, settings)
