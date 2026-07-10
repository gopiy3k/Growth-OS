"""Resume markers + duplicate detection (Amendment 1).

Per-collection state file records each prompt's status so a collector
restart resumes without re-submitting completed prompts.

State file: data/state/<collection_id>.json
  { "prompt_id@version": "pending|submitted|completed|failed", ... }

Determinism: collection_id is derived from inputs (identity.compute_collection_id),
so the same collection always reads/writes the same state file. A crash mid-prompt
leaves the prompt "submitted" (not "completed") -> on resume it is re-run from
scratch in a fresh conversation (Mode B, which isolates context) -> safe.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

DEFAULT_STATE_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "state"
)


class PromptStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    FAILED = "failed"


class ResumeState:
    def __init__(self, collection_id: str, state_dir: Path = DEFAULT_STATE_DIR):
        self.collection_id = collection_id
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / f"{collection_id}.json"
        self._state: dict[str, str] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2, sort_keys=True)

    @staticmethod
    def _key(prompt_id: str, prompt_version: str) -> str:
        return f"{prompt_id}@{prompt_version}"

    def status(self, prompt_id: str, prompt_version: str) -> PromptStatus:
        raw = self._state.get(self._key(prompt_id, prompt_version), "pending")
        return PromptStatus(raw)

    def is_completed(self, prompt_id: str, prompt_version: str) -> bool:
        return self.status(prompt_id, prompt_version) == PromptStatus.COMPLETED

    def mark(self, prompt_id: str, prompt_version: str, status: PromptStatus) -> None:
        self._state[self._key(prompt_id, prompt_version)] = status.value
        self._save()

    def skip_if_done(self, prompt_id: str, prompt_version: str) -> bool:
        """Returns True if the prompt is already completed (idempotent no-op)."""
        return self.is_completed(prompt_id, prompt_version)
