"""Structural normalization: raw Grok evidence -> §9 canonical schema.

Increment 4, phase Q2. Design refs: COLLECTOR-DESIGN-001 §9 (normalized schema),
§1.6-g (NORMALIZE step). IMPLEMENTATION-ROADMAP-RC1 §5 (Q2).

CONTRACT (non-negotiable, per design §9 + §0 principles):
  - Normalization is STRUCTURAL PARSING ONLY. It does NOT judge importance,
    rank, filter, summarize, or editorialize. `confidence` is always null.
  - It is a PURE transform: normalize(raw_dict) -> normalized_dict. No I/O,
    no browser, no OD, no quota. Same input -> same output (deterministic).
  - It preserves full provenance verbatim and links back to raw via
    `raw_evidence_ref` so downstream can audit the untransformed source.
  - It NEVER mutates or destroys the raw record (evidence-first principle).

This module is new Increment 4 capability inside the collector subtree; it
touches no frozen module.
"""

from __future__ import annotations

import re

NORMALIZED_SCHEMA_VERSION = "1.0"

# A permissive http(s) URL matcher for embedded-link extraction. Structural
# only — we do not validate or resolve links, just faithfully extract them.
_URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"']+")

# Markdown-ish heading: leading #'s, or a bold-only line acting as a heading.
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
# List item: -, *, +, or "1." / "1)" ordered markers.
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")


def _strip_trailing_url_punct(url: str) -> str:
    return url.rstrip(".,;:!?")


def _extract_links(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL_RE.finditer(text):
        u = _strip_trailing_url_punct(m.group(0))
        if u not in seen:
            seen.append(u)
    return seen


def _raw_evidence_ref(provenance: dict, record_key: dict) -> str:
    """Best-effort structural ref to the raw file (design §9 example shape).

    Path form mirrors the durable store layout keyed by RecordKey. Uses
    collected_at date when present, else the literal 'unknown-date' — no
    fabrication of timestamps.
    """
    collected_at = provenance.get("collected_at") or ""
    date = collected_at[:10] if len(collected_at) >= 10 else "unknown-date"
    cid = record_key.get("collection_id", "")
    pid = record_key.get("prompt_id", "")
    ver = record_key.get("prompt_version", "")
    return f"evidence/{date}/{cid}/{pid}@{ver}.json"


def normalize(raw: dict) -> dict:
    """Convert a raw evidence record (RawEvidenceRecord.to_dict()) into the
    §9 normalized schema. Pure, deterministic, structural-only.

    Raises ValueError if the raw record is missing the identity fields needed
    to build provenance / raw_evidence_ref (fail loud, never fabricate).
    """
    provenance = raw.get("provenance")
    record_key = raw.get("record_key")
    if not provenance or not record_key:
        raise ValueError("raw record missing provenance/record_key")

    raw_response = raw.get("raw_response", "")
    sections, items = _parse_structure(raw_response)

    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "provenance": dict(provenance),  # verbatim copy, never mutated
        "record_key": dict(record_key),
        "raw_evidence_ref": _raw_evidence_ref(provenance, record_key),
        "sections": sections,
        "items": items,
        "confidence": None,  # §9: collector never asserts signal quality
        "notes": _structural_notes(sections, items, raw_response),
    }


def _parse_structure(text: str) -> tuple[list[dict], list[dict]]:
    """Split free text into sections (by heading) and items (bullets/ordered).

    Structural only: a heading opens a section; non-empty lines under it form
    the section body; bullet/ordered lines are ALSO captured as flat items with
    their embedded links. No ranking, no dedup of meaning, no summarization.
    """
    sections: list[dict] = []
    items: list[dict] = []
    current_heading: str | None = None
    current_body: list[str] = []
    item_index = 0

    def _flush_section() -> None:
        if current_heading is not None or current_body:
            sections.append(
                {
                    "heading": current_heading,
                    "body": "\n".join(current_body).strip(),
                }
            )

    for line in text.splitlines():
        hm = _HEADING_RE.match(line)
        if hm:
            _flush_section()
            current_heading = hm.group(2).strip()
            current_body = []
            continue

        bm = _BULLET_RE.match(line) or _ORDERED_RE.match(line)
        if bm:
            item_index += 1
            item_text = bm.group(1).strip()
            items.append(
                {
                    "index": item_index,
                    "text": item_text,
                    "embedded_links": _extract_links(item_text),
                }
            )
        if line.strip():
            current_body.append(line.rstrip())

    _flush_section()
    return sections, items


def _structural_notes(sections: list[dict], items: list[dict], raw: str) -> str:
    """Verbatim structural description (design §9 example: '3-bullet list')."""
    parts = []
    if items:
        parts.append(f"{len(items)} list item(s)")
    heading_count = sum(1 for s in sections if s.get("heading"))
    if heading_count:
        parts.append(f"{heading_count} heading section(s)")
    if not parts:
        parts.append("unstructured free text" if raw.strip() else "empty response")
    return "; ".join(parts)


__all__ = ["normalize", "NORMALIZED_SCHEMA_VERSION"]
