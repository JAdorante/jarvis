#!/usr/bin/env python3
"""Listen on the USB microphone and answer through the TV speakers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.runtime import run_listener


def main():
    run_listener()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
