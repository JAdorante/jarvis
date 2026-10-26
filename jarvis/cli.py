"""Command line entry points."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from jarvis.config import load_settings
from jarvis.runtime import ask_once, build_runtime, run_listener


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis local assistant")
    parser.add_argument("--text", help="Handle one sentence and exit")
    parser.add_argument("--no-speak", action="store_true", help="Print the reply without playing audio")
    parser.add_argument("--learn", action="store_true", help="Look up one fact about this system and store it")
    parser.add_argument("--check", action="store_true", help="Print configuration and exit")
    parser.add_argument("--backup", action="store_true", help="Copy the memory database into the data folder")
    parser.add_argument("--discover", action="store_true", help="Look on the local network for a Roku")
    parser.add_argument("--face", action="store_true", help="Serve only the hologram")
    args = parser.parse_args(argv)

    if args.face:
        settings = load_settings()
        from jarvis.visual.server import serve

        serve(settings.face_host, settings.face_port)
        return 0
    if args.discover:
        from jarvis.devices.roku import discover_roku

        host = discover_roku()
        if not host:
            print("No Roku answered on the local network.")
            return 1
        print(host)
        return 0
    if args.check:
        settings = load_settings()
        print(
            json.dumps(
                {
                    "config": str(settings.config_path),
                    "data_dir": str(settings.data_dir),
                    "roku": settings.roku_host,
                    "model": settings.model,
                    "remote_inference": settings.remote_enabled,
                    "whisper": settings.whisper_bin.exists(),
                    "piper": settings.piper_bin.exists() and settings.piper_voice.exists(),
                },
                indent=2,
            )
        )
        return 0
    if args.backup:
        runtime = build_runtime()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = runtime["settings"].data_dir / "backups" / f"jarvis-{stamp}.sqlite"
        runtime["store"].backup_to(dest)
        runtime["store"].close()
        print(dest)
        return 0
    if args.learn:
        runtime = build_runtime()
        result = runtime["agent"].handle("What is a Raspberry Pi 5?")
        runtime["store"].add_memory(
            "Learned: " + result["speech"],
            "episodic",
            "explicit_self_question",
            kind="event",
            confidence=0.6,
        )
        print(result["speech"])
        runtime["store"].close()
        return 0
    if args.text:
        ask_once(args.text, speak=not args.no_speak)
        return 0
    run_listener()
    return 0
