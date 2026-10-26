#!/usr/bin/env python3
"""Speak text through the Pi HDMI port."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.audio.speech import Speaker
from jarvis.config import load_settings


def speak(text):
    Speaker(load_settings()).speak(text)


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Say with: python3 jarvis_say.py "Hello. I am Jarvis."')
    speak(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
