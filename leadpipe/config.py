"""Configuration: YAML settings (config.yaml) merged over built-in defaults, plus secrets from .env.

The dashboard's Setup form writes back to config.yaml (comments kept) and .env, and the engine picks the
changes up automatically (`reload_if_changed`)."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv

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


def _upgrade_old_keys(user: dict) -> dict:
    """Settings files from version 1.0 used `targets` and `discovery`. Translate them."""
    user = dict(user or {})
    targets = user.get("targets")
    if targets and "reach" not in user:
        cats: list[str] = []
        for t in targets:
            for c in t.get("categories") or []:
                if c not in cats:
                    cats.append(c)
        user["reach"] = {"business_types": cats, "start_area": targets[0].get("area", ""),
                         "next_areas": [t.get("area") for t in targets[1:] if t.get("area")],
                         "world_rollout": False}
    disc = user.get("discovery")
    if isinstance(disc, dict) and disc.get("providers") and "sources" not in user:
        user["sources"] = {"providers": disc["providers"]}
    return user


class Config:
    """Dict-backed config with dotted lookups: cfg.get("sending.daily_cap")."""

    def __init__(self, data: dict, path: Path | None = None, home: Path | None = None):
        self.data = data
        self.path = path
        self.home = home or Path.cwd()
        self._stamp = self._mtimes()

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
    def config_path(self) -> Path:
        return self.path or (self.home / "config.yaml")

    @property
    def env_path(self) -> Path:
        return self.home / ".env"

    @property
    def data_dir(self) -> Path:
        d = Path(self.env("LEADPIPE_DATA_DIR") or (self.home / "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return Path(self.env("LEADPIPE_DB") or (self.data_dir / "leadpipe.db"))

    # ---- mail identities (main mailbox) -------------------------------------
    @property
    def from_email(self) -> str:
        return self.env("FROM_EMAIL") or self.env("SMTP_USER")

    @property
    def from_name(self) -> str:
        return self.env("FROM_NAME") or self.get("profile.your_name", "")

    @property
    def sending_domain(self) -> str:
        return self.from_email.split("@")[-1].lower() if "@" in self.from_email else "localhost"

    # ---- live reload ------------------------------------------------------
    def _mtimes(self) -> tuple:
        out = []
        for p in (self.path, self.home / ".env"):
            try:
                out.append(p.stat().st_mtime if p else 0)
            except OSError:
                out.append(0)
        return tuple(out)

    def reload_if_changed(self) -> bool:
        """Re-read config.yaml and .env if either changed on disk. Updates this object in place."""
        stamp = self._mtimes()
        if stamp == self._stamp:
            return False
        fresh = load_config(self.path or self.home / "config.yaml", override_env=True)
        self.data = fresh.data
        self.path = fresh.path
        self._stamp = stamp
        return True


def load_config(path: str | os.PathLike | None = None, override_env: bool = False) -> Config:
    """Load config.yaml (or LEADPIPE_CONFIG) over the defaults and read .env next to it."""
    cfg_path = Path(path or os.environ.get("LEADPIPE_CONFIG") or "config.yaml").resolve()
    home = cfg_path.parent
    env_file = home / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=override_env)

    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        defaults = yaml.safe_load(fh) or {}

    user: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
    # Lists such as `business_types` and sequence `steps` replace the defaults entirely.
    data = _deep_merge(defaults, _upgrade_old_keys(user))
    return Config(data, cfg_path if cfg_path.exists() else None, home)


# ---------------------------------------------------------------------------
# Writing settings back (dashboard Setup form)
# ---------------------------------------------------------------------------
def save_settings(cfg: Config, changes: dict[str, Any]) -> None:
    """Write dotted keys into config.yaml, keeping every comment. Creates config.yaml from the defaults if needed."""
    from ruamel.yaml import YAML

    target = cfg.config_path
    if not target.exists():
        target.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    ry = YAML()
    ry.preserve_quotes = True
    ry.width = 4096
    with open(target, encoding="utf-8") as fh:
        doc = ry.load(fh) or {}
    for dotted, value in changes.items():
        parts = dotted.split(".")
        node = doc
        for part in parts[:-1]:
            if part not in node or node[part] is None:
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    with open(target, "w", encoding="utf-8") as fh:
        ry.dump(doc, fh)
    cfg.path = target
    cfg.reload_if_changed()


def save_env(cfg: Config, values: dict[str, str]) -> None:
    """Update KEY=value lines in .env (adds missing keys, keeps everything else). Empty values are skipped."""
    path = cfg.env_path
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {k: v for k, v in values.items() if v is not None and str(v) != ""}
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in pending:
            out.append(f"{m.group(1)}={_env_quote(pending.pop(m.group(1)))}")
        else:
            out.append(line)
    for k, v in pending.items():
        out.append(f"{k}={_env_quote(v)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in values.items():
        if v is not None and str(v) != "":
            os.environ[k] = str(v)
    cfg.reload_if_changed()


def _env_quote(v: str) -> str:
    v = str(v)
    return f'"{v}"' if re.search(r"[\s#'\"]", v) else v


def env_file_values(cfg: Config) -> dict[str, str]:
    return {k: (v or "") for k, v in dotenv_values(cfg.env_path).items()} if cfg.env_path.exists() else {}
