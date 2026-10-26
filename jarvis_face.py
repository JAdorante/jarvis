#!/usr/bin/env python3
"""Full-screen Jarvis hologram."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import load_settings
from jarvis.visual.server import serve


def main():
    settings = load_settings()
    print("Keys on the TV window: I idle, L listening, T thinking, A acting, R speaking, W waiting, E error, F fullscreen.")
    serve(settings.face_host, settings.face_port)


if __name__ == "__main__":
    main()
