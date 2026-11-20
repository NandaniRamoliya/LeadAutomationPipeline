"""Configuration: YAML settings (config.yaml) merged over built-in defaults, plus secrets from .env."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "default_config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Dict-backed config with dotted lookups: cfg.get("sending.daily_cap")."""

    def __init__(self, data: dict, path: Path | None = None, home: Path | None = None):
        self.data = data
        self.path = path
        self.home = home or Path.cwd()

    # ---- lookups -------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict:
        return self.data.get(name) or {}

    @staticmethod
    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    # ---- paths ---------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        d = Path(self.env("LEADPIPE_DATA_DIR") or (self.home / "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return Path(self.env("LEADPIPE_DB") or (self.data_dir / "leadpipe.db"))

    # ---- mail identities ------------------------------------------------
    @property
    def from_email(self) -> str:
        return self.env("FROM_EMAIL") or self.env("SMTP_USER")

    @property
    def from_name(self) -> str:
        return self.env("FROM_NAME") or self.get("profile.your_name", "")

    @property
    def sending_domain(self) -> str:
        return self.from_email.split("@")[-1].lower() if "@" in self.from_email else "localhost"


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config.yaml (or LEADPIPE_CONFIG) over the defaults and read .env next to it."""
    cfg_path = Path(path or os.environ.get("LEADPIPE_CONFIG") or "config.yaml").resolve()
    home = cfg_path.parent
    env_file = home / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        defaults = yaml.safe_load(fh) or {}

    user: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        # Lists such as `targets` and sequence `steps` replace the defaults entirely.
    data = _deep_merge(defaults, user)
    return Config(data, cfg_path if cfg_path.exists() else None, home)
