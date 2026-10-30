import json
from pathlib import Path

import yaml


def load_yaml(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def load_json(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)
