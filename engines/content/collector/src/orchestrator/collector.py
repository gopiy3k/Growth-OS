"""Increment 3 — Collection Orchestrator (GrokCollector).

Single lifecycle over the frozen Browser Runtime. Depends ONLY on:
  - browser.adapter.BrowserAdapter (+ exceptions)   (frozen, not modified)
  - prompt_registry.loader.PromptRegistry           (Inc1)
  - core.identity                                   (Inc1)
  - core.resume_state.ResumeState                   (Inc1)
  - orchestrator.config.CollectorConfig             (this increment)
  - orchestrator.collection_result.*               (this increment)

OUT OF SCOPE (Increment 4+): storage writes, normalization, OD emission,
scheduler, metrics, real quota enforcement. Raw evidence is kept IN MEMORY in
the returned CollectionResult only.

PO Increment 3 amendments:
  #1 conversation_id OPTIONAL — parsed from the tab URL if the runtime exposes
     it, else None; stored in browser_metadata (orchestrator stays runtime-agnostic).
  #2 explicit CollectionStatus on every result.
  #3 policy from CollectorConfig (no hardcoded retry/timeout/quota/endpoint).
  #4 endpoint read from config.endpoint (default ADR-027 canonical).
"""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import parse_qs, urlparse

from browser.adapter import (
    AuthError,
    BrowserAdapter,
    BrowserAdapterError,
    CompletionTimeout,
    ExtractionError,
    SubmitError,
    TabHandle,
)
from core.identity import (
    compute_collection_id,
    compute_prompt_hash,
    utc_date,
    utc_iso,
    RecordKey,
)
from core.resume_state import PromptStatus, ResumeState
from core.evidence_store import EvidenceStore
from prompt_registry.loader import PromptRegistry

from .collection_result import (
    CollectionResult,
    CollectionStatus,
    PromptRef,
    RawEvidenceRecord,
)
from .config import CollectorConfig, DEFAULT_ENDPOINT


def _parse_conversation_id(url: str) -> Optional[str]:
    """Best-effort extraction of ?conversation=<id> from a Grok URL.

    Optional (PO Inc3 #1): returns None if absent or unparseable — the
    orchestrator never requires a runtime to expose this.
    """
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return None
    vals = qs.get("conversation")
    if vals and vals[0]:
        return vals[0]
    return None


class GrokCollector:
    """Drives one collection run: attach -> new tab -> auth -> per-prompt
    submit/wait/extract -> preserve raw (in memory) -> close tab -> detach.

    The adapter is injected (caller owns the CDP url / runtime construction).
    Cleanup (close_tab, detach) happens in a finally block so a mid-run failure
    never leaks the automation tab (ADR-027 §6 new-automation-tab invariant).
    """

    def __init__(
        self,
        adapter: BrowserAdapter,
        registry: PromptRegistry,
        config: CollectorConfig,
        prompt_refs: list[PromptRef],
        collection_label: str,
        collection_date: Optional[str] = None,
    ) -> None:
        if not prompt_refs:
            raise ValueError("GrokCollector requires at least one PromptRef")
        self.adapter = adapter
        self.registry = registry
        self.config = config
        self.prompt_refs = list(prompt_refs)
        self.collection_label = collection_label
        # Deterministic collection_id (Amendment 1): derived from the defining
        # inputs, not process/clock — so a resume reads the same state file.
        first_id, first_ver = prompt_refs[0].key()
        self.collection_id = compute_collection_id(
            first_id, first_ver, collection_label, collection_date or utc_date()
        )
        self._state_dir = config.state_dir
        self._resume = ResumeState(self.collection_id, self._state_dir)
        self._store = EvidenceStore(config.store_dir)
        self._url_by_target: dict[str, str] = {}
        # Diagnostic: the automation tab opened for this run (opaque to the
        # orchestrator; exposed read-only for cleanup verification/tests).
        self.automation_tab_id: Optional[str] = None

    async def run_collection(self) -> CollectionResult:
        result = CollectionResult(
            collection_id=self.collection_id,
            status=CollectionStatus.FAILED,
            prompts_total=len(self.prompt_refs),
        )
        tab: Optional[TabHandle] = None
        try:
            # ATTACH
            await self.adapter.attach()
            # OPEN_TAB (new automation tab only — ADR-027 §6)
            tab = await self.adapter.new_tab()
            self.automation_tab_id = tab.target_id
            # AUTH_VERIFY (navigate + assert auth) — interaction failure here is
            # a real stop-and-report, not a transport glitch (adapter retries
            # transport internally); convert to FAILED status via the handler.
            try:
                endpoint = self.config.endpoint
                await self.adapter.navigate(tab, endpoint)
                await self.adapter.verify_auth(tab)
            except BrowserAdapterError as e:
                self._fail(result, e, CollectionStatus.FAILED)
                return result
            except asyncio.CancelledError:
                # Cancellation during AUTH_VERIFY: close/detach best-effort,
                # then re-raise so the caller observes the cancellation.
                await self._safe_close(tab)
                await self._safe_detach()
                raise

            # CONV_SETUP: derive conversation_id ONLY if the runtime exposes it.
            browser_metadata: dict = {}
            conv_id: Optional[str] = self.config.conversation_id
            if conv_id is None:
                targets = await self.adapter.enumerate_targets()
                self._url_by_target = {t.target_id: t.url for t in targets}
                conv_id = _parse_conversation_id(self._tab_url(tab))
            if conv_id is not None:
                browser_metadata["conversation_id"] = conv_id

            # Per-prompt loop (Design §1.6)
            for ref in self.prompt_refs:
                pid, ver = ref.key()
                # SKIP_IF_DONE (idempotent no-op — Amendment 1)
                if self._resume.is_completed(pid, ver):
                    # Q1: rehydrate the previously-durably-preserved record into
                    # the in-memory result so a resumed run still returns full
                    # evidence (no re-collection, no duplicate on disk).
                    prior = self._store.load(
                        self.collection_id, RecordKey(self.collection_id, pid, ver)
                    )
                    if prior is not None:
                        result.add_record(RawEvidenceRecord.from_dict(prior))
                        result.records_persisted += 1
                    result.prompts_skipped += 1
                    continue
                try:
                    await self._collect_one(tab, ref, result, browser_metadata)
                except BrowserAdapterError as e:
                    self._fail(result, e, CollectionStatus.FAILED)
                    return result
                except asyncio.CancelledError:
                    # Cancellation mid-prompt: the outer finally still closes
                    # the tab + detaches; re-raise after marking the failure.
                    self._fail(result, e, CollectionStatus.FAILED)
                    raise

            # Terminal status
            if result.prompts_total == result.prompts_skipped:
                result.finish(CollectionStatus.SKIPPED)
            else:
                result.finish(CollectionStatus.SUCCESS)
            return result
        finally:
            # CLOSE_TAB + DETACH always run (ADR-027 §6 invariant, finally-block)
            if tab is not None:
                await self._safe_close(tab)
            await self._safe_detach()

    async def _collect_one(
        self,
        tab: TabHandle,
        ref: PromptRef,
        result: CollectionResult,
        browser_metadata: dict,
    ) -> None:
        pid, ver = ref.key()
        # PROMPT_RESOLVE (Amendment 2 — externalized registry)
        prompt_def = self.registry.get(pid, ver)
        rendered = prompt_def.render(**ref.variables)
        prompt_hash = compute_prompt_hash(rendered)
        # SUBMIT (mark submitted first so a crash leaves it resumable)
        self._resume.mark(pid, ver, PromptStatus.SUBMITTED)
        submitted_at = utc_iso()
        await self.adapter.submit_prompt(tab, rendered)
        # WAIT
        await self.adapter.wait_for_completion(
            tab, timeout_s=self.config.completion_timeout
        )
        # EXTRACT
        raw = await self.adapter.extract_response(tab)
        completed_at = utc_iso()
        # PRESERVE_RAW (in memory, full provenance) before any normalization.
        rec = RawEvidenceRecord.build(
            collection_id=self.collection_id,
            prompt_id=pid,
            prompt_version=ver,
            prompt_text=rendered,
            prompt_hash=prompt_hash,
            variables=ref.variables,
            raw_response=raw,
            browser_metadata=browser_metadata,
            submitted_at=submitted_at,
            completed_at=completed_at,
            endpoint=self.config.endpoint,
        )
        assert rec.is_valid(), "provenance incomplete on preserved raw record"
        result.add_record(rec)
        # PERSIST (Q1): durable exactly-once write. A second preserve for the
        # same key is a no-op (returns False) — resume never duplicates.
        self._store.preserve(rec.to_dict())
        # Mark completed so resume skips it (Amendment 1 exactly-once)
        self._resume.mark(pid, ver, PromptStatus.COMPLETED)
        result.prompts_completed += 1

    # --- helpers (runtime-agnostic) ---

    def _tab_url(self, tab: TabHandle) -> str:
        """Return the automation tab's URL from the CONV_SETUP snapshot.

        We obtain the URL via enumerate_targets() (the only adapter method that
        exposes a URL) — never reaching into CDP internals, so BrowserAdapter is
        not modified (PO guardrail).
        """
        return self._url_by_target.get(tab.target_id, "")

    def _fail(self, result: CollectionResult, exc: Exception, status: CollectionStatus) -> None:
        """Record a terminal failure and set status (single error-format path)."""
        result.error = f"{type(exc).__name__}: {exc}"
        result.finish(status)

    async def _safe_close(self, tab: TabHandle) -> None:
        # Best-effort under normal failure AND under cancellation: a shielded
        # close means the automation tab is torn down even if the outer task
        # was cancelled, so the ADR-027 §6 invariant holds on every path.
        try:
            await asyncio.shield(self.adapter.close_tab(tab))
        except (BrowserAdapterError, asyncio.CancelledError):
            # best-effort; must not raise and mask the real error
            pass

    async def _safe_detach(self) -> None:
        try:
            await asyncio.shield(self.adapter.detach())
        except (BrowserAdapterError, asyncio.CancelledError):
            pass


__all__ = ["GrokCollector", "_parse_conversation_id"]
