"""Speak through HDMI. Playback can be stopped."""

import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


class Speaker:
    def __init__(self, settings):
        self.settings = settings
        self._proc = None
        self._lock = threading.Lock()

    def speak(self, text):
        words = " ".join((text or "").split())
        if not words:
            return
        wav = None
        try:
            device = hdmi_device()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                wav = Path(handle.name)
            if self.settings.piper_bin.exists() and self.settings.piper_voice.exists():
                subprocess.run(
                    [str(self.settings.piper_bin), "--model", str(self.settings.piper_voice), "--output_file", str(wav)],
                    input=words + "\n",
                    text=True,
                    check=True,
                )
            else:
                subprocess.run(
                    ["espeak-ng", "-v", "en-us+m3", "-s", "145", "-w", str(wav), words],
                    check=True,
                )
            with self._lock:
                self._proc = subprocess.Popen(
                    ["aplay", "-q", "-D", device, str(wav)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc = self._proc
            proc.wait()
        except (OSError, subprocess.CalledProcessError, SystemExit) as exc:
            print(words)
            print(f"Speech output is unavailable: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                self._proc = None
            if wav is not None:
                wav.unlink(missing_ok=True)

    def stop(self):
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


def hdmi_device():
    try:
        listing = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Could not list sound devices: {exc}") from exc
    found = []
    for line in listing.splitlines():
        match = re.search(r"card (\d+): (\S+) \[([^\]]+)\], device (\d+):", line)
        if match and "hdmi" in match.group(3).lower():
            found.append(match.groups())
    if not found:
        raise SystemExit("No HDMI sound device found. Plug the HDMI cable into HDMI0 and reboot the Pi.")
    for card, card_name, description, device in found:
        label = f"{card_name} {description}".lower()
        if "hdmi-0" in label or "hdmi0" in label or card_name.lower().endswith("0"):
            return f"plughw:{card},{device}"
    card, _name, _description, device = found[0]
    return f"plughw:{card},{device}"
