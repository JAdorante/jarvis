"""Microphone capture and the wake-word check after transcription."""

import array
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path


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


def record_phrase(device, settings):
    block_bytes = int(settings.sample_rate * 0.05) * 2
    proc = subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(settings.sample_rate), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    heard = []
    quiet_blocks = 0
    started = False
    started_at = None
    try:
        while True:
            data = proc.stdout.read(block_bytes)
            if len(data) < block_bytes:
                break
            samples = array.array("h")
            samples.frombytes(data)
            level = max(abs(sample) for sample in samples) / 32768
            now = time.monotonic()
            if not started:
                if level >= settings.speech_level:
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
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
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


def worth_asking(text, followup_open=False):
    """Ignore noise. Act on the wake word, a TV command, or a recent follow-up."""
    lowered = text.lower().strip()
    if not lowered or lowered.startswith("("):
        return False
    words = re.findall(r"[a-z']+", lowered)
    if re.search(r"\bjar\w*", lowered):
        return True
    if re.search(r"\b(open|launch|resume|turn off|turn on|power off|power on|volume|mute)\b", lowered):
        return len(words) >= 2
    if followup_open and len(words) <= 12 and re.match(r"(what|how) about\b|^and\b|^(he|she|they|it|that)\b", lowered):
        return True
    return False


class Ear:
    """Holds the follow-up window between turns."""

    def __init__(self, settings):
        self.settings = settings
        self.last_heard = 0.0

    def accepts(self, text, now):
        followup = now - self.last_heard <= self.settings.followup_seconds
        return worth_asking(text, followup_open=followup)

    def mark(self, now):
        self.last_heard = now


def capture_once(device, settings):
    raw = record_phrase(device, settings)
    if len(raw) < settings.sample_rate * 2 * 0.4:
        return ""
    with tempfile.TemporaryDirectory() as folder:
        wav_path = Path(folder) / "heard.wav"
        write_wav(raw, wav_path, settings.sample_rate)
        return transcribe(wav_path, settings)
