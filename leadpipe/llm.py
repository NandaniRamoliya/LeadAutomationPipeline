"""Optional AI layer. Three backends:

* anthropic  : the Claude API (needs ANTHROPIC_API_KEY or an `ant auth login` profile)
* claude_cli : the `claude` command-line tool, which runs on your Claude Pro/Max plan
* none       : no AI. Built-in templates and keyword rules are used instead.

Every call asks for JSON that matches a schema. A failed or refused call returns None, and the
caller falls back to templates, so the pipeline never stops because the AI is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess

from .config import Config

log = logging.getLogger("leadpipe.llm")


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = cfg.get("llm.model", "claude-opus-5-5")
        self.timeout = float(cfg.get("llm.timeout_seconds", 180))
        self.provider = self._pick_provider(str(cfg.get("llm.provider", "auto")).lower())
        self._client = None
        self.last_error: str | None = None

    @staticmethod
    def _pick_provider(choice: str) -> str:
        if choice in ("none", "off", "false", ""):
            return "none"
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        if choice == "anthropic":
            return "anthropic"
        if choice == "claude_cli":
            return "claude_cli" if shutil.which("claude") else "none"
        # auto
        if has_key:
            return "anthropic"
        if shutil.which("claude"):
            return "claude_cli"
        return "none"

    @property
    def available(self) -> bool:
        return self.provider != "none"

    # ------------------------------------------------------------------
    def json(self, system: str, prompt: str, schema: dict, effort: str = "medium") -> dict | None:
        if not self.available:
            return None
        try:
            if self.provider == "anthropic":
                return self._anthropic(system, prompt, schema, effort)
            return self._cli(system, prompt, schema)
        except Exception as exc:  # network, auth, quota... never crash the pipeline
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("AI call failed (%s). Falling back to templates.", self.last_error)
            return None

    # ---- Claude API ------------------------------------------------------
    def _client_obj(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=2)
        return self._client

    def _anthropic(self, system: str, prompt: str, schema: dict, effort: str) -> dict | None:
        import anthropic

        client = self._client_obj()
        common = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            # Server-side fallback: if the model declines, the API retries on a fallback model.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **common)
        except anthropic.BadRequestError as exc:
            log.info("Retrying without server-side fallback (%s)", exc)
            response = client.messages.create(**common)

        if response.stop_reason == "refusal":
            self.last_error = "model declined the request"
            return None
        if response.stop_reason == "max_tokens":
            self.last_error = "response was cut off"
            return None
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return _extract_json(text)

    # ---- Claude Code CLI (uses your Claude plan) -------------------------
    def _cli(self, system: str, prompt: str, schema: dict) -> dict | None:
        full = (f"{system}\n\n{prompt}\n\nRespond with ONLY a JSON object matching this JSON schema, "
                f"no other text:\n{json.dumps(schema)}")
        proc = subprocess.run(["claude", "-p", full, "--output-format", "text"], capture_output=True,
                              text=True, timeout=self.timeout)
        if proc.returncode != 0:
            self.last_error = (proc.stderr or proc.stdout)[:300]
            return None
        return _extract_json(proc.stdout)

    def ping(self) -> tuple[bool, str]:
        if not self.available:
            return False, "no AI configured (templates will be used)"
        data = self.json("You are a test.", 'Reply with {"ok": true}.',
                         {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                          "additionalProperties": False}, effort="low")
        if data and data.get("ok"):
            return True, f"{self.provider} ({self.model if self.provider == 'anthropic' else 'claude CLI'}) works"
        return False, f"{self.provider} failed: {self.last_error}"
