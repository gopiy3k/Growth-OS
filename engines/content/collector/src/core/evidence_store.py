"""Durable raw-evidence store (Increment 4, phase Q1).

Persists each ``RawEvidenceRecord`` to disk as one JSON file keyed by
``RecordKey.to_filename()`` — exactly-once per (collection_id, prompt_id,
prompt_version). A second preserve with the same key is a no-op (idempotent),
so a crash + resume never produces a duplicate row.

Design invariants:
  - Every write is atomic (temp file + os.replace) so a crash mid-write leaves
    no half-written JSON. This is the foundation Q4 (resume hardening) builds on.
  - Store path is derived from ``CollectorConfig.store_dir`` and resolves under
    ``<store_dir>/<collection_id>/`` — NO hardcoded machine paths.
  - Pure storage: no browser, no normalization, no OD, no quota logic.

Depends only on ``core.identity`` (for ``RecordKey``) and the in-memory record
shape from ``orchestrator.collection_result``.

This module is NOT one of the frozen Inc1–3 modules; it is new Increment 4
capability and stays inside the collector subtree.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from core.identity import RecordKey

# Default store root lives inside the collector data tree (resolved relative to
# this file) so the collector is self-contained with no machine-specific paths.
DEFAULT_STORE_DIR = Path(__file__).resolve().parents[2] / "data" / "evidence"


class EvidenceStore:
    """Durable, exactly-once raw-evidence store keyed by RecordKey.

    Layout: ``<root>/<collection_id>/<prompt_id>@<version>.json``
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else DEFAULT_STORE_DIR

    def _collection_dir(self, collection_id: str) -> Path:
        return self.root / collection_id

    def path_for(self, collection_id: str, key: RecordKey) -> Path:
        """Absolute path of the on-disk record for this key."""
        return self._collection_dir(collection_id) / key.to_filename()

    def contains(self, collection_id: str, key: RecordKey) -> bool:
        """True when a committed record already exists for this key."""
        return self.path_for(collection_id, key).exists()

    def preserve(self, record: dict) -> bool:
        """Atomically persist one raw-evidence record.

        Exactly-once: if a committed file already exists for the key, the
        existing bytes are left untouched and ``False`` is returned (no
        duplicate). Returns ``True`` when a new record was written.

        ``record`` MUST carry ``record_key`` (with collection_id, prompt_id,
        prompt_version) and ``schema_version`` — i.e. the output of
        ``RawEvidenceRecord.to_dict()``.
        """
        rk = record.get("record_key") or {}
        collection_id = rk.get("collection_id")
        prompt_id = rk.get("prompt_id")
        prompt_version = rk.get("prompt_version")
        if not (collection_id and prompt_id and prompt_version):
            raise ValueError("record missing record_key identity fields")
        key = RecordKey(collection_id, prompt_id, prompt_version)
        target = self.path_for(collection_id, key)
        if target.exists():
            # Exactly-once: already preserved -> idempotent no-op.
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, record)
        return True

    def load(self, collection_id: str, key: RecordKey) -> Optional[dict]:
        """Read a previously preserved record, or ``None`` if absent."""
        path = self.path_for(collection_id, key)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def records_for(self, collection_id: str) -> list[dict]:
        """All preserved records for one collection, sorted by filename."""
        coll_dir = self._collection_dir(collection_id)
        if not coll_dir.exists():
            return []
        out = []
        for p in sorted(coll_dir.glob("*.json")):
            with p.open("r", encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    # --- Normalized records (Q2). Stored in a parallel tree so raw stays
    # immutable and normalized artifacts are separable (design §10). ---

    def _normalized_dir(self, collection_id: str) -> Path:
        return self.root / "_normalized" / collection_id

    def normalized_path_for(self, collection_id: str, key: RecordKey) -> Path:
        return self._normalized_dir(collection_id) / key.to_filename()

    def preserve_normalized(self, normalized: dict) -> bool:
        """Atomically persist one normalized record (exactly-once by key).

        ``normalized`` MUST carry ``record_key`` identity fields (output of
        ``normalizer.normalize``). Returns True when newly written, False when
        an identical key already exists (idempotent no-op)."""
        rk = normalized.get("record_key") or {}
        collection_id = rk.get("collection_id")
        prompt_id = rk.get("prompt_id")
        prompt_version = rk.get("prompt_version")
        if not (collection_id and prompt_id and prompt_version):
            raise ValueError("normalized record missing record_key identity fields")
        key = RecordKey(collection_id, prompt_id, prompt_version)
        target = self.normalized_path_for(collection_id, key)
        if target.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, normalized)
        return True

    def normalized_for(self, collection_id: str) -> list[dict]:
        """All normalized records for one collection, sorted by filename."""
        nd = self._normalized_dir(collection_id)
        if not nd.exists():
            return []
        out = []
        for p in sorted(nd.glob("*.json")):
            with p.open("r", encoding="utf-8") as fh:
                out.append(json.load(fh))
        return out

    @staticmethod
    def _atomic_write(target: Path, payload: dict) -> None:
        """Write JSON atomically: temp file + os.replace (crash-safe)."""
        data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
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
            # Clean up the temp file if the rename didn't complete.
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
            raise


__all__ = ["EvidenceStore", "DEFAULT_STORE_DIR"]
