# TP-003 — Editorial Validation Reset

**Date:** 2026-07-11
**Type:** Understanding change (turning point) — *not* an implementation fix.
**Status:** PERMANENT. Future editorial-quality evaluation assumes this unless new evidence disproves it.

## Previous belief
- The first generated drafts (GPT-5.6 / Copilot / Atlas / OpenAI Partner Network posts) represented
  the real quality of the Content Engine pipeline and could be used to evaluate Collector,
  Opportunity Discovery, Score, Generate, Review, and Select.

## Evidence
- All 11 intake records consumed by the dry cycle came from a synthetic fixture file
  (`engines/content/collector/data/opportunity-intake/2026-07-11.jsonl`), not from real RC3
  collector output.
- Records 1–10 carry `conversation_id = "FAKE123"`; record 11 has `conversation_id = null`.
- `sections[].body` is placeholder text: `"FAKE GROK RESPONSE about AI tooling."` (1–10) or
  `"FAKE RESPONSE"` (11).
- Every record: `items = []`, `confidence = null`.
- None of the real RC3 evidence is present: collection_id `55d63dd9…`, handles
  `@aiseomastery` / `@Mayank_Msd` / `@auxten`, or any real Grok content.
- Therefore the generated drafts were authored by Generate from **placeholder input**, not from
  real collector signal.

## New truth
- The **engineering pipeline** is validated: Collector→OD→Score→Generate→Review→Select→(publish
  paused) runs end-to-end on real AI Runtime calls with 0 errors. The plumbing is sound.
- The **editorial pipeline** has **NOT** been validated. No editorial-quality claim can be made
  from the 2026-07-11 dry cycle, because the intake was synthetic.

## Future work
- **Never evaluate editorial quality unless the intake provenance is verified as genuine collector
  evidence.** Verify the records carry real `conversation_id` / `collection_id` / source content
  (not `FAKE*`) before any Collector / OD / Score / Generate / Review / Select quality judgement.
- The pipeline consumed synthetic intake. Before re-running, determine *why* the fixture took
  precedence over the real RC3 artifacts, restore genuine intake, and only then perform the first
  real editorial-quality evaluation to locate the actual bottleneck.
