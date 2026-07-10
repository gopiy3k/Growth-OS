# ADR-027 — Local Chrome via CDP as Canonical Browser Runtime

**Status:** ACCEPTED (frozen)
**Date:** 2026-07-11
**Profile:** engine-content
**Supersedes:** Temporary investigation notes and handoff discussions (HANDOFF-2026-07-10T230500.md, HANDOFF-2026-07-11T-browser-runtime-canonical.md) as the long-term architectural record.
**Owning milestone:** Browser Runtime Persistence (COMPLETE)
**Next milestone:** Grok Trend Intelligence Collector

---

## 1. Context

The Grok Trend Intelligence Collector requires a stable, persistent, authenticated
X/Grok browsing session. The browser runtime that provides that session had to be
selected and proven reliable before any collector logic could be built.

Two candidate runtimes were evaluated:

- **Browser Use Cloud** — a managed cloud browser with a persistent profile
  (`cloud_profile_id: 2ebc3560-09c6-427e-8fb3-b9d6e7acd20c`), driven by Hermes's
  `browser_use` provider via `BROWSER_USE_API_KEY`.
- **Local Chrome via CDP** — a dedicated local Chrome instance launched with a
  persistent `--user-data-dir`, attached by Hermes through the Chrome DevTools
  Protocol at `http://127.0.0.1:9333` (configured via `browser.cdp_url`).

---

## 2. Decision

**Local Chrome attached via CDP is the canonical browser runtime for the
`engine-content` profile.** The dedicated persistent Chrome profile is the canonical
authentication mechanism. Browser Use Cloud remains available but is classified as
**experimental / dormant** and is not used by the production path.

This decision is **frozen**. The browser runtime is a completed subsystem.

---

## 3. Why Browser Use Cloud authentication was abandoned as the canonical approach

Browser Use Cloud was the original intended path for first-time authentication.
It was abandoned as canonical after **five consecutive first-time authentication
failures**, each ending in `about:blank` immediately after OTP submission.

Root-cause analysis established:

1. **OTP invalidation by page collapse.** The X login OTP is valid for ~2 hours
   *only while the login page is alive*. If the tab reaches `about:blank` and the
   flow is re-driven, X mints a **new** server-side OTP and the previously pasted
   code becomes permanently invalid. The cloud tab collapsed to `about:blank`
   repeatedly during the login flow, burning each OTP.
2. **Idle-timeout collapse.** Even with no navigation away, the Browser Use cloud
   tab self-collapsed to `about:blank` during the round-trip wait for a pasted OTP,
   independently invalidating the code. The single-tab discipline that fixed the
   "navigate away" mistake was **not sufficient** — the idle gap itself killed the
   session.
3. **Objective mismatch.** The goal was never to prove Browser Use Cloud *can* perform
   first-time authentication. The goal is a persistent authenticated X session for the
   Grok Collector. Forcing the cloud login consumed engineering time without converging.

After 5 failures, this was accepted as sufficient evidence that the cloud
authentication flow was not reliable enough for this milestone. Browser Use Cloud is
retained in config (`cloud_profile_id` preserved) and its Electron runtime left
running, but classified dormant.

---

## 4. Why Local Chrome + CDP was selected

1. **Reliability of native login.** A real local Chrome lets the user perform the
   one-time X login manually, with a real mouse/keyboard and no bot-detection or
   OTP-round-trip fragility. The user confirmed: X authenticated, Grok opened, and the
   session survived a full Chrome close + relaunch.
2. **Persistent profile on disk.** `--user-data-dir` stores X cookies in the dedicated
   profile. Authentication survives browser restarts with no re-login. Proven by the
   persistence test (close → relaunch → still authenticated).
3. **Hermes-native support, zero code change.** `browser_tool.py` already supports a
   CDP-attach backend: `browser.cdp_url` (or `BROWSER_CDP_URL`) makes Hermes connect
   directly to a running Chrome via the DevTools Protocol, skipping both the cloud
   provider and the local headless launcher. `_is_local_mode()` treats a CDP override
   as non-local (SSRF checks apply), which is correct for a host-local browser.
4. **Isolation from the experimental runtime.** Using port **9333** (distinct from the
   Browser Use Electron's **9222**) keeps the two runtimes fully isolated — proven by
   target enumeration showing automation traffic appeared only on 9333 while 9222 held
   only its own `file://` popup pages.
5. **Configuration over implementation.** The entire change was one config line
   (`browser.cdp_url: "http://127.0.0.1:9333"`), applied via `hermes config set`. No
   engine modules, collector logic, or core browser code were modified.

---

## 5. Browser Runtime invariants (enforced)

These are recorded as constraints, not failures.

- **I1 — Persistence.** X authentication persists in the Chrome profile on disk and
  survives Chrome restarts. Grok opens without re-authentication.
- **I2 — Isolation.** The canonical runtime (9333) and the Browser Use Cloud Electron
  (9222) are independent processes on separate ports. Neither affects the other.
- **I3 — Attach, don't auth.** Hermes attaches to an already-authenticated session. It
  never performs first-time authentication, OTP handling, or login flows.
- **I4 — New automation tab (see §6).** Automation must never assume an existing page
  is disposable.
- **I5 — Reconnection is lazy/on-next-use.** On each `browser_navigate`, the supervisor
  re-discovers `webSocketDebuggerUrl` from `http://127.0.0.1:9333/json/version` and
  re-binds. A restarted Chrome (same port, same profile) is re-attached automatically on
  the next navigation. There is no background watchdog; reconnection is driven by the
  next collector action.

---

## 6. The "new automation tab" rule (mandatory)

**Discovered during verification:** the high-level `browser_navigate` tool reuses the
*existing* page target when only one tab is open, replacing the user's active X/Grok tab
with the navigated URL. Cookies survived (so the user was not logged out), but the
visible session was interrupted.

Therefore, all automation MUST follow this sequence:

1. Enumerate existing page targets (`Target.getTargets` / `/json`).
2. Create a **NEW** automation tab (`Target.createTarget` / `PUT /json/new?<url>`).
3. Perform **all** automation only inside that new target.
4. Leave every pre-existing user tab untouched.
5. Close **only** the automation tab (`Target.closeTarget` / `PUT /json/close/<id>`).

**If the runtime cannot guarantee a new automation tab, STOP and report** rather than
navigating the user's active tab.

This was proven end-to-end via raw CDP:
baseline `54F5F32… | https://x.com/i/grok` → created `4BB34814… | https://example.com/`
→ existing tab unchanged → closed `4BB34814…` → only `54F5F32… | https://x.com/i/grok`
remained.

---

## 7. Browser Runtime boundaries

**Inside the browser runtime (infrastructure) — NOT the collector's concern:**
- Browser runtime selection (local Chrome via CDP).
- Browser profile strategy (dedicated `--user-data-dir`).
- CDP configuration (`browser.cdp_url`, port 9333).
- Authentication (manual one-time X login; cookie persistence).
- Login / OTP / verification flows.
- Browser Use Cloud lifecycle (kept dormant, Electron running).
- The "new automation tab" rule enforcement.

**Inside the Grok Trend Intelligence Collector (application) — begins only after attach:**
1. Open a fresh automation tab.
2. Navigate directly to **`https://x.com/i/grok`** (the canonical Grok endpoint — see §6.1). Do NOT use `grok.com` unless a separate runtime architecture is explicitly authorized.
3. Submit research prompts.
4. Wait for completion.
5. Extract the complete response.
6. Preserve evidence.
7. Normalize the output.
8. Store the result.
9. Close only the automation tab.

**The collector must NOT:** authenticate, manage browser profiles, change CDP
configuration, handle login flows, or modify browser runtime behaviour.

---

## 6.1 Canonical Grok endpoint (ADR-027 amendment, 2026-07-11)

The authenticated X session is established and persisted on the X profile
(`x.com`). The Browser Runtime guarantees persistence for that X profile. The
Grok Trend Intelligence Collector is built on top of that authenticated X
session, not on a separate `grok.com` session.

**Canonical browser flow:**

```
Attach to local Chrome (CDP :9333)
  -> Create NEW automation tab
  -> Navigate directly to https://x.com/i/grok
  -> Verify authenticated Grok interface
  -> Submit prompt
  -> Wait for completion
  -> Extract response
  -> Close automation tab
```

- `https://x.com/i/grok` is the **only** authorized Grok endpoint for the
  collector. A fresh tab opened there inherits the X `auth_token` / `twid` /
  `ct0` cookies from the persistent profile and loads already authenticated
  (verified by smoke test 2026-07-11: new tab at `x.com/i/grok` carried the
  real X login cookies; a tab at `grok.com` did NOT).
- `grok.com` is **not** used unless the PO explicitly authorizes a separate
  runtime architecture.

---

## 8. Browser Runtime freeze decision

The browser runtime architecture is **FROZEN** as of 2026-07-11. It is a completed
subsystem. Engineering focus shifts entirely to the Grok Trend Intelligence Collector.

Frozen facts (do not re-litigate without cause):
- Local Chrome via CDP is the canonical browser runtime.
- The dedicated persistent Chrome profile is the canonical authentication mechanism.
- X authentication persists across Chrome restarts; Grok opens without re-auth.
- Browser Use Cloud is experimental/dormant but available.
- Automation always creates a new tab, operates only within it, closes only it.
- Existing user tabs are never navigated, reused, or modified.

---

## 9. Conditions under which this ADR may be revisited

This ADR is reopened ONLY if one of the following occurs:

1. A **reproducible defect** is discovered in the frozen runtime (e.g. CDP attach fails
   consistently, cookies stop persisting across restarts, the new-tab rule cannot be
   satisfied by the runtime, or port conflicts break isolation).
2. The user (PO) **explicitly requests a redesign** of the browser runtime, CDP
   architecture, profile strategy, or Browser Use Cloud authentication.
3. A new requirement emerges that the frozen runtime provably cannot satisfy and that
   cannot be met within the collector boundary.

Absent one of these, the browser runtime is treated as a stable dependency and is not
re-examined during collector implementation.

---

## 10. Configuration reference (frozen)

`profiles/engine-content/config.yaml`:
```yaml
browser:
  cloud_profile_id: 2ebc3560-09c6-427e-8fb3-b9d6e7acd20c   # kept, experimental/dormant
  cdp_url: http://127.0.0.1:9333                           # canonical: local Chrome via CDP
```

Dedicated Chrome launch (user-run, Windows):
```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --user-data-dir="<DEDICATED_X_PROFILE_DIR>" ^
  --remote-debugging-port=9333 ^
  --no-first-run --no-default-browser-check
```

---

## 11. Supersession

This ADR supersedes all temporary investigation notes and handoff discussions as the
long-term architectural record for the browser runtime. The handoff docs
(HANDOFF-2026-07-11T-browser-runtime-canonical.md et al.) remain useful as operational
state but are not the authoritative architecture statement. Future work begins from
this ADR plus the collector requirements.

---

## 12. Smoke Test Result (2026-07-11) — PASSED

**Objective:** prove Hermes can send one prompt to Grok and retrieve one complete
response through the frozen Browser Runtime, using the canonical `x.com/i/grok` flow.

**Method:** raw CDP only (`Target.getTargets`, `Target.createTarget`,
`Network.getCookies`, `Runtime.evaluate`, `Input.dispatchMouseEvent`,
`Target.closeTarget`) — the high-level `browser_navigate` is avoided because it
reuses the live user tab (see §6 invariant).

| # | Step | Result |
|---|------|--------|
| 1 | Attach via CDP (9333) | PASS |
| 2 | Create NEW automation tab | PASS — `0866AE49…` |
| 3 | Navigate to `https://x.com/i/grok` | PASS |
| 4 | Verify authenticated (real X cookies present: `auth_token`, `twid`, `ct0`) | PASS |
| 5 | Submit prompt "Reply with exactly: GROK_SMOKE_TEST_OK" | PASS (clicked "Grok something" send button) |
| 6 | Wait for completion | PASS |
| 7 | Extract response | PASS — exact match `GROK_SMOKE_TEST_OK` |
| 8 | Close ONLY automation tab | PASS |
| 9 | User tab unchanged | PASS — `54F5F32… | https://x.com/i/grok` intact |

**Note (defect avoided):** an earlier run that navigated the new tab to `grok.com`
landed **logged-out** (no X auth cookies on that origin). This is why §6.1 mandates
`x.com/i/grok` as the canonical endpoint — the authenticated X session only propagates
to the `x.com` origin. The smoke test is considered the positive proof of the
canonical flow.

**Conclusion:** Hermes can reliably send one prompt to Grok and retrieve one complete
response through the frozen Browser Runtime. Collector design may now proceed within
ADR-027 boundaries.

### 12.1 Prompt-submission technique (lessons from the smoke test)

Submitting a prompt to the Grok composer was the only step that did not work on the
first attempt. Documented here so the collector reuses the working method and avoids
the failed ones.

**The composer is a React single-page app, not a `<form>`:**
- The text input is a `<textarea>` (no surrounding `<form>` element wraps it).
- The send control is a standalone `<button aria-label="Grok something">` positioned at
  the bottom-right of the textarea (in the smoke test its center was at viewport
  coordinates ~`(1247, 379)`). It is NOT labelled "Send"/"Submit".

**What FAILED:**
1. Setting the textarea value directly (e.g. `el.value = "..."` or a naive
   `Input.insertText`) did NOT register in React state — on a later key press React
   reset it (observed as the textarea ending up containing only `"x"`).
2. `Input.dispatchKeyEvent` with `Enter` did NOT submit, even after the value was set
   in React state. Grok's composer did not fire the send on synthetic Enter in this
   runtime.

**What WORKED (use this for the collector):**
1. Set the value through React's controlled-input path:
   ```js
   const ta = document.querySelector('textarea');
   const setter = Object.getOwnPropertyDescriptor(
     window.HTMLTextAreaElement.prototype, 'value').set;
   setter.call(ta, PROMPT_TEXT);
   ta.dispatchEvent(new Event('input', { bubbles: true }));
   ```
2. Locate the send button by its `aria-label === "Grok something"` (fallback: the
   `<button>` whose center is near the textarea's bottom-right corner).
3. Click it with a real mouse event, NOT `element.click()`:
   ```js
   Input.dispatchMouseEvent({ type: 'mousePressed', button: 'left', x, y, clickCount: 1 });
   Input.dispatchMouseEvent({ type: 'mouseReleased', button: 'left', x, y, clickCount: 1 });
   ```
4. Verify submission by polling the page: the textarea is cleared and the prompt text
   appears as an echoed user message in `document.body.innerText`.

**Why `element.click()` / synthetic Enter were insufficient:** the composer's submit
handler is bound to a real pointer/click gesture on the button, not to a DOM `.click()`
call or a programmatic key event. A true `mousePressed`+`mouseReleased` at the button's
coordinates is the reliable trigger.

---

## 13. Multi-Interaction Capability Validation (2026-07-11) — PASSED

**Objective:** validate Hermes can reliably conduct multiple sequential interactions
with Grok inside a single automation session (conversation continuity, response-boundary
distinction, multi-line extraction), using the canonical `x.com/i/grok` flow.

**Method:** same raw-CDP tooling as §12. One automation tab, 5 sequential prompts,
each submitted via the §12.1 technique, completion detected by polling
(`textarea` cleared + response text present + no spinner), raw response preserved per
prompt.

**Prompts executed (all in one conversation thread):**
1. `Reply with exactly: INTERACTION_1_OK` → `INTERACTION_1_OK` ✅
2. `Reply with exactly: INTERACTION_2_OK` → `INTERACTION_2_OK` ✅
3. `What is 7 times 6? Reply with only the number.` → `42` ✅
4. `Name three primary colors. List them on separate lines.` → `red` / `yellow` / `blue` (3-line list) ✅
5. `Reply with exactly: INTERACTION_5_OK` → `INTERACTION_5_OK` ✅

**Evidence (final capture):** all 5 responses present in a single continuous thread
(`document.body.innerText` length 685; markers r1–r5 all true). No truncation. Response
boundaries cleanly distinguishable (prompt echo → response → "Fast" / suggestion chips).

**Conversation continuity:** CONFIRMED. After Prompt 1 the URL became
`x.com/i/grok?conversation=<id>` and all subsequent prompts appended to the same thread.
A fresh chat is NOT required between prompts — the collector may run multiple prompts in
one tab/session.

**New findings (must inform collector design):**
- **Send-button coordinates are DYNAMIC.** They shift as the conversation grows and the
  composer re-layouts. Observed positions: `(1247,379)` → `(1279,686)` → `(1279,685)` →
  `(1279,686)`. The collector MUST re-locate the button via `getBoundingClientRect` of the
  `aria-label="Grok something"` button (or bottom-right-of-textarea heuristic) immediately
  before EVERY click. Hard-coded coordinates will miss after the first prompt.
- **Transient CDP `Runtime.evaluate` errors** (`code: -32601, method not found`) occurred
  ~4× during the run. They are transport glitches, not state loss — the tab stays alive
  and a retry of the same call succeeds. The collector MUST treat `Runtime.evaluate` /
  `Input.dispatch*` failures as retryable (small backoff, re-attach if needed) rather than
  fatal. (Note: this retry is at the *browser-tooling* layer, distinct from the PO's
  "do not auto-retry on smoke-test step failure" rule, which concerns interaction logic,
  not transport flakiness.)
- **Completion heuristic:** `textarea` cleared + response text present + no
  `[class*=animate-spin]` / `[role=progressbar]` / "Thinking about your request" in body.
  Sufficient for these prompts; the collector should also cap wait time per prompt.

**Tab hygiene:** automation tab `CBE7E7AD…` closed; user tab `54F5F32… | x.com/i/grok`
untouched. Invariant held.

**Conclusion:** the interaction model is proven — Hermes can run multiple sequential
Grok prompts in one session, distinguish response boundaries, and extract multi-line
responses without truncation. Collector design may proceed on this proven model.
