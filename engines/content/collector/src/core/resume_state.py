"""Resume markers + duplicate detection (Amendment 1).

Per-collection state file records each prompt's status so a collector
restart resumes without re-submitting completed prompts.

State file: data/state/<collection_id>.json
  { "prompt_id@version": "pending|submitted|completed|failed", ... }

Determinism: collection_id is derived from inputs (identity.compute_collection_id),
so the same collection always reads/writes the same state file. A crash mid-prompt
leaves the prompt "submitted" (not "completed") -> on resume it is re-run from
scratch in a fresh conversation (Mode B, which isolates context) -> safe.

Increment 4 (Q4) hardening: marker writes are atomic (temp + fsync + os.replace)
so a crash during _save() cannot corrupt the state file; a fresh ResumeState
recovers any in-flight ("submitted") markers back to "pending" so a crashed
prompt is resumed, never silently skipped or double-counted.
"""

from __future__ import annotations

import json
import os
import tempfile
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
        self.state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        self.path = self.state_dir / f"{collection_id}.json"
        self._state: dict[str, str] = self._load()
        self.recover_in_flight()

    def recover_in_flight(self) -> int:
        """Demote any SUBMITTED marker (left mid-collect by a crash) to
        PENDING so the prompt is re-run on resume, never silently skipped or
        double-counted. Returns the number of markers recovered."""
        recovered = 0
        for key, value in self._state.items():
            if value == PromptStatus.SUBMITTED.value:
                self._state[key] = PromptStatus.PENDING.value
                recovered += 1
        if recovered:
            self._save()
        return recovered

    def _load(self) -> dict:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.path, self._state)

    @staticmethod
    def _atomic_write(target: Path, payload: dict) -> None:
        """Write JSON atomically: temp file + fsync + os.replace (crash-safe)."""
        data = json.dumps(payload, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), suffix=".tmp", prefix="."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
            raise

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
