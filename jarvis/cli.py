"""Command line entry points."""

import argparse
import json
from datetime import datetime, timezone

from jarvis.config import load_settings
from jarvis.memory.store import MemoryStore
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
    parser.add_argument("--sparrow-pair", metavar="CODE", help="Claim a Sparrow pairing code and save the device token")
    parser.add_argument("--sparrow-check", action="store_true", help="Print Sparrow connection status and exit")
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
    if args.sparrow_pair or args.sparrow_check:
        return sparrow_command(args)
    if args.check:
        settings = load_settings()
        print(
            json.dumps(
                {
                    "config": str(settings.config_path),
                    "data_dir": str(settings.data_dir),
                    "roku": settings.roku_host,
                    "pi_input": settings.roku_pi_input,
                    "model": settings.model,
                    "remote_inference": settings.remote_enabled,
                    "whisper": settings.whisper_bin.exists(),
                    "piper": settings.piper_bin.exists() and settings.piper_voice.exists(),
                    "sparrow": settings.sparrow.base_url if settings.sparrow.enabled else False,
                },
                indent=2,
            )
        )
        return 0
    if args.backup:
        # Open only the database. A full runtime would mark the live service's tasks as interrupted.
        settings = load_settings()
        store = MemoryStore(settings.data_dir / "jarvis.sqlite")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = settings.data_dir / "backups" / f"jarvis-{stamp}.sqlite"
        store.backup_to(dest)
        store.close()
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


def sparrow_command(args):
    from jarvis.sparrow import SparrowClient, SparrowError

    settings = load_settings()
    client = SparrowClient(settings.sparrow)
    if args.sparrow_pair:
        try:
            device_id = client.pair(args.sparrow_pair.strip())
        except SparrowError as exc:
            print(f"Pairing failed: {exc}")
            return 1
        print(f"Paired. Device id: {device_id}")
        print(f"Token saved to {settings.sparrow.device_token_file}")
        return 0
    cfg = settings.sparrow
    print(f"url: {cfg.base_url}")
    print(f"enabled: {cfg.enabled}")
    print(f"mcp_token: {'present' if client.mcp_token() else 'missing'} ({cfg.mcp_token_file})")
    print(f"device_token: {'present' if client.device_token() else 'missing'} ({cfg.device_token_file})")
    try:
        data = client.list_tools()
        tools = data.get("tools") if isinstance(data, dict) else data
        names = [item.get("name") if isinstance(item, dict) else str(item) for item in (tools or [])]
        print(f"tools: {', '.join(names) if names else 'none'}")
    except SparrowError as exc:
        print(f"tools: unavailable ({exc})")
    if client.device_token():
        try:
            print(f"outbox_waiting: {len(client.outbox(peek=True))}")
        except SparrowError as exc:
            print(f"outbox_waiting: unavailable ({exc})")
    return 0
