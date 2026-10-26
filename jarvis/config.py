"""Load device addresses, model names, and limits from configuration."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "jarvis.json"


@dataclass
class Settings:
    roku_host: str
    roku_port: int
    roku_timeout: float
    roku_rediscover: bool
    ollama_url: str
    model: str
    ollama_timeout: float
    max_predict_tokens: int
    remote_enabled: bool
    remote_url: str
    remote_model: str
    allow_private_memory_remote: bool
    data_dir: Path
    whisper_bin: Path
    whisper_model: Path
    piper_bin: Path
    piper_voice: Path
    wake_word: str
    followup_seconds: float
    sample_rate: int
    speech_level: float
    silence_level: float
    silence_seconds: float
    max_phrase_seconds: float
    max_steps: int
    max_seconds: float
    retry_wait_seconds: float
    roku_standing_authorization: bool
    quiet_start: str
    quiet_end: str
    face_host: str
    face_port: int
    config_path: Path


def _path(value, fallback):
    text = (value or "").strip()
    return Path(text).expanduser() if text else fallback


def load_settings(path=None):
    """Read JSON configuration. Environment JARVIS_CONFIG overrides the path."""
    selected = Path(path or os.environ.get("JARVIS_CONFIG") or DEFAULT_CONFIG)
    raw = {}
    if selected.exists():
        raw = json.loads(selected.read_text(encoding="utf-8"))
    roku = raw.get("roku", {})
    ollama = raw.get("ollama", {})
    remote = raw.get("remote_inference", {})
    paths = raw.get("paths", {})
    audio = raw.get("audio", {})
    agent = raw.get("agent", {})
    permissions = raw.get("permissions", {})
    quiet = raw.get("quiet_hours", {})
    face = raw.get("face", {})
    home = Path.home()
    data_dir = _path(paths.get("data_dir"), home / "jarvis")
    host = os.environ.get("JARVIS_ROKU_HOST", roku.get("host", "10.0.0.43"))
    return Settings(
        roku_host=host,
        roku_port=int(roku.get("port", 8060)),
        roku_timeout=float(roku.get("timeout_seconds", 8)),
        roku_rediscover=bool(roku.get("rediscover", True)),
        ollama_url=ollama.get("base_url", "http://127.0.0.1:11434").rstrip("/"),
        model=ollama.get("model", "jarvis"),
        ollama_timeout=float(ollama.get("timeout_seconds", 180)),
        max_predict_tokens=int(ollama.get("max_predict_tokens", 160)),
        remote_enabled=bool(remote.get("enabled", False)),
        remote_url=(remote.get("base_url") or "").rstrip("/"),
        remote_model=remote.get("model") or "",
        allow_private_memory_remote=bool(remote.get("allow_private_memory", False)),
        data_dir=data_dir,
        whisper_bin=_path(paths.get("whisper_bin"), home / "whisper.cpp" / "build" / "bin" / "whisper-cli"),
        whisper_model=_path(paths.get("whisper_model"), home / "whisper.cpp" / "models" / "ggml-base.en.bin"),
        piper_bin=_path(paths.get("piper_bin"), home / "jarvis-voice" / "bin" / "piper"),
        piper_voice=_path(paths.get("piper_voice"), home / "jarvis" / "voices" / "en_GB-alan-medium.onnx"),
        wake_word=audio.get("wake_word", "jarvis"),
        followup_seconds=float(audio.get("followup_seconds", 20)),
        sample_rate=int(audio.get("sample_rate", 16000)),
        speech_level=float(audio.get("speech_level", 0.045)),
        silence_level=float(audio.get("silence_level", 0.03)),
        silence_seconds=float(audio.get("silence_seconds", 1.1)),
        max_phrase_seconds=float(audio.get("max_phrase_seconds", 12)),
        max_steps=int(agent.get("max_steps", 4)),
        max_seconds=float(agent.get("max_seconds", 90)),
        retry_wait_seconds=float(agent.get("retry_wait_seconds", 0.4)),
        roku_standing_authorization=bool(permissions.get("roku_standing_authorization", True)),
        quiet_start=quiet.get("start") or "",
        quiet_end=quiet.get("end") or "",
        face_host=face.get("host", "0.0.0.0"),
        face_port=int(face.get("port", 8765)),
        config_path=selected,
    )
