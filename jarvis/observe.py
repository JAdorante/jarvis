"""Structured logs. Sensitive argument names are redacted."""

import json
import logging
from pathlib import Path


SECRET_WORDS = ("password", "passcode", "token", "secret", "api_key", "apikey", "authorization", "credential")


def redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if any(word in str(key).lower() for word in SECRET_WORDS):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def configure_logging(data_dir):
    folder = Path(data_dir) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(message)s")
    file_handler = logging.FileHandler(folder / "jarvis.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream)
    logger.propagate = False
    return logger


def log_event(logger, **fields):
    if logger is None:
        return
    logger.info(json.dumps(redact(fields), ensure_ascii=False, default=str))
