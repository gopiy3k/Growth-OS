"""Opportunity Discovery intake emission (Increment 4, phase Q5).

Design refs: COLLECTOR-DESIGN-001 §16 (Interfaces with Opportunity Discovery),
IMPLEMENTATION-ROADMAP-RC1 §5 (Q5). AGENTS.md freezing discipline preserved:
this module emits a STABLE CONTRACT ARTIFACT only — it does NOT import, modify,
or depend on the Opportunity Discovery implementation.

Contract (design §16):
  - The collector passes ALL collected normalized findings to the intake drop
    zone. It does NOT filter, rank, or curate — selection is OD's job.
  - Each emitted record is a §9 normalized record carrying `raw_evidence_ref`
    so OD (and downstream EB) can audit the untransformed source.
  - Location: ``<intake_dir>/<YYYY-MM-DD>.jsonl`` (one JSON object per line).
  - Idempotent across runs: a re-run with the same record_key is a NO-OP
    (never duplicates a finding).
  - Exactly-once + crash-safe: each day file is rewritten atomically
    (temp + os.replace) so a crash mid-write leaves a complete prior file.

Pure I/O side-effect module: no browser, no normalization logic, no OD import.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from core.identity import utc_date

DEFAULT_OD_INTAKE_DIR = Path(__file__).resolve().parents[2] / "data" / "opportunity-intake"


def _record_signature(rec: dict) -> tuple[str, str, str]:
    rk = rec.get("record_key") or {}
    return (
        str(rk.get("collection_id", "")),
        str(rk.get("prompt_id", "")),
        str(rk.get("prompt_version", "")),
    )


class OpportunityIntake:
    """Emits normalized records to the OD intake drop zone (§16 contract)."""

    def __init__(self, intake_dir: Optional[Path] = None) -> None:
        self.intake_dir = Path(intake_dir) if intake_dir else DEFAULT_OD_INTAKE_DIR

    def _day_file(self, date: str) -> Path:
        return self.intake_dir / f"{date}.jsonl"

    def _read_existing(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip a malformed line defensively; atomic rewrite below
                    # will not preserve it, which is acceptable for an intake
                    # drop zone (OD re-derives from raw via raw_evidence_ref).
                    continue
        return out

    def emit(self, records: Iterable[dict], date: Optional[str] = None) -> int:
        """Append normalized records to the day's intake jsonl, skipping any
        record_key already present (idempotent). Returns the number of NEW
        records written.

        ``records`` are §9 normalized dicts (output of ``normalizer.normalize``).
        """
        day = date or utc_date()
        path = self._day_file(day)
        existing = self._read_existing(path)
        seen = {_record_signature(r) for r in existing}

        added: list[dict] = []
        for rec in records:
            sig = _record_signature(rec)
            if sig in seen:
                continue  # idempotent — already in the intake drop zone
            seen.add(sig)
            added.append(rec)

        if not added:
            return 0

        merged = existing + added
        self.intake_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_jsonl(path, merged)
        return len(added)

    @staticmethod
    def _atomic_write_jsonl(path: Path, records: list[dict]) -> None:
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
            raise


__all__ = ["OpportunityIntake", "DEFAULT_OD_INTAKE_DIR"]
