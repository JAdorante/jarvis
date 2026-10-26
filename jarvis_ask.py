#!/usr/bin/env python3
"""Ask Jarvis one sentence. TV commands, memory, and lookups go through the agent."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.runtime import ask_once, build_runtime


def answer(question, extra=""):
    runtime = build_runtime()
    try:
        text = question if not extra else f"{extra}\n\n{question}"
        return runtime["agent"].handle(text)["speech"]
    finally:
        runtime["store"].close()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--learn":
        from jarvis.cli import main as cli_main

        raise SystemExit(cli_main(["--learn"]))
    if len(sys.argv) < 2:
        raise SystemExit('Ask with: python3 jarvis_ask.py "your question"')
    ask_once(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
