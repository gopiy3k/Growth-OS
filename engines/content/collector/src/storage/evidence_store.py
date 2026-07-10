"""Storage contract (Design §10) + exactly-once preservation (Amendment 1).

EvidenceStore: append-only, immutable. Raw evidence keyed by RecordKey
(collection_id, prompt_id, prompt_version) -> exactly-once write. A second
write with the same key is a no-op (returns existing path), never a duplicate.

Normalized store: JSONL append per record; idempotent by record_key.
OD intake: normalized records dropped to data/od_intake/<date>.jsonl.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.identity import RecordKey

BASE_DIR = Path(__file__).resolve().parents[2] / "data"


class EvidenceStore:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base = Path(base_dir)
        self.evidence_dir = self.base / "evidence"
        self.normalized_dir = self.base / "normalized"
        self.od_intake_dir = self.base / "od_intake"

    # ---- raw evidence (exactly-once) ----

    def _raw_path(self, key: RecordKey, date: str) -> Path:
        return self.evidence_dir / date / key.collection_id / key.to_filename()

    def exists(self, key: RecordKey, date: str) -> bool:
        return self._raw_path(key, date).exists()

    def write_raw(self, key: RecordKey, date: str, record: dict) -> Path:
        """Write raw evidence once. If key already exists for the date, return
        the existing path without overwriting (exactly-once)."""
        path = self._raw_path(key, date)
        if path.exists():
            return path  # idempotent no-op, no duplicate
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False)
        return path

    # ---- normalized ----

    def write_normalized(self, key: RecordKey, date: str, record: dict) -> Path:
        """Append normalized record to JSONL. Idempotent: if a record with the
        same record_key is already present in today's file, skip."""
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        path = self.normalized_dir / f"{date}.jsonl"
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    existing = json.loads(line)
                    k = existing.get("record_key", {})
                    if (
                        k.get("collection_id") == key.collection_id
                        and k.get("prompt_id") == key.prompt_id
                        and k.get("prompt_version") == key.prompt_version
                    ):
                        return path  # already recorded today
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    # ---- OD intake ----

    def write_od_intake(self, date: str, record: dict) -> Path:
        """Hand normalized record to Opportunity Discovery intake drop zone.
        Append-only; OD consumes from here. The collector does NOT write OD's
        internal state."""
        self.od_intake_dir.mkdir(parents=True, exist_ok=True)
        path = self.od_intake_dir / f"{date}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
