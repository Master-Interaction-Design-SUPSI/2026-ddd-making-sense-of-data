# Nine readability tickets implementation plan

Goal: implement the nine tickets approved in this conversation, preserving the current working-tree editor redesign and all supported content/storage formats.

Architecture: retain plain browser JavaScript and CommonJS test adapters. Separate pure data calculation from DOM views and persistence effects; use native dialog for the main visual. No new dependencies. The release integration preserves newer `origin/main` changes, including removal of issue-based publication requests; the handoff therefore has no retired request action.

Constraints during implementation: no automated browser tests or deployment. The user subsequently accepted the manual preview and explicitly authorized commits and push. Preserve URL validation, source escaping, recovery formats and canonical/generated file boundaries. Work in the current checkout because the authorized scope includes its uncommitted files; baseline copies are in `/tmp/ddd-nine-before`.

- [x] 1. Simplify annotation cases in `static/markup.js`; run annotation tests.
- [x] 2. Remove unused accent tokens and consolidate existing CSS declarations without changing values or specificity.
- [x] 3. Add `static/entry.js`: describe(entry), safeUrl(url, folder, kind, resolver), render(entry, resolver). Wire main and scripts; verify immutable metadata and URL policies.
- [x] 4. Add `static/media-preview.js`: create() returns clear/dispose; flatten media dispatch and own all listeners/elements. Verify stale play and repeated teardown.
- [x] 5. Add `editor/persistence.js`: Persistence.open/read/save/discard/idle and restoration metadata; preserve queued guards, migration, storage failure and conflicts. Wire existing editor recovery UI.
- [x] 6. Add pure `editor/inventory.js` calculation and `editor/media-view.js` DOM view; editor retains mutation policy. Verify counts, missing files and main visual metadata.
- [x] 7. Add `editor/review.js` SVG validation and pure `editor/handoff.js` steps; `editor/handoff-view.js` renders actions. Preserve revision checks and step wording/order.
- [x] 8. Add `static/lightbox.js` controller and native dialog/button markup/styles; validate close/focus/playback logic with unit doubles; manual native-browser acceptance remains separate.
- [x] 9. Add `editor/download.js` bounded streaming fetch using AbortSignal.timeout; preserve cancellation and hash validation. Verify success/oversize/timeout/interruption.

Verification: Node unit suites (including new modules), Python unit suite, course-material drift check, isolated temporary offline build, syntax checks, diff inspection. Native browser focus/backdrop, responsive appearance, audible playback and real IndexedDB acceptance require manual inspection; do not substitute automated browser tests.

Execution notes: use existing behavior tests for pure refactors; new behavior/module contracts get focused tests before implementation. No cosmetic tests that mirror source. The user authorized all nine proposed tickets; no second design approval is needed.


Implementation evidence (2026-11-18):

- Verification: 111 Node tests and 156 Python tests pass; 27 frontend scripts pass syntax checks; canonical material copies and diff whitespace checks pass. Offline build resolves every script in all three shells.
- Nine code increments are present. Existing source snapshot retained in `/tmp/ddd-nine-before`; no commits, push, deployment or browser automation.
- Entry and preview lifecycles, persistence, inventory, handoff, review and download unit suites added; CI and maintainer commands include them once each.
- Fresh read-only reviewer found no critical/important regressions against the baseline; duplicate CI suite paths were removed.
- The dialog close callback handles both immediate and browser-queued close events, including teardown before dispatch. It keeps full-viewport image/blank-area dismissal to preserve existing interaction.
- Optional `media.csv` deletion now produces a deletion instruction without attempting to copy an absent table.

Manual acceptance is still pending; unit tests do not establish native-browser focus behavior, appearance or audible playback:

- [ ] Desktop/mobile appearance after CSS consolidation and the native-dialog visual trigger.
- [ ] Tab to the visual, open with keyboard, close with Escape/button/blank-area click, and confirm focus returns and inline playback refreshes.
- [ ] Hover/focus media, enable sound when blocked, then dismiss and navigate; verify audible playback stops.
- [ ] Real IndexedDB saving, restoration, denied/quota storage and concurrent-tab decisions (existing editor redesign acceptance).

Manual preview: `http://localhost:8766/`, serving `/tmp/ddd-nine-preview` (isolated offline build; local course-team entry plus the canonical template rendered as `group-01`, without imported published group snapshots).


Completion audit:

| Ticket | Current implementation evidence | Remaining evidence |
| --- | --- | --- |
| 1 | `markup.js` uses named heading cases and no unreachable bibliography resource alternative; annotation suite passes. | None for code cleanup. |
| 2 | Unused accent tokens removed; iframe, bibliography and media-file declarations consolidated with existing selector specificity and values. | Manual desktop/mobile appearance. |
| 3 | `CourseEntry.describe` derives metadata without source mutation; render receives resolver; entry/annotation policy and packaging tests pass. | Manual production/preview appearance. |
| 4 | Preview controller owns AbortController listeners, audio/button nodes and clear/dispose operations; stale playback and repeated teardown tests pass. | Audible hover/focus/dismissal checks. |
| 5 | Persistence owns read/save/discard, queue and migration; conflict, failed-write retention, legacy retirement, fallback and tombstone tests pass. | Real browser storage acceptance, already pending in editor redesign. |
| 6 | Pure inventory includes aliases/direct counts, missing files and explicit visual metadata; editor recovery/workspace suites cover mutations and undo. | Manual inventory presentation. |
| 7 | SVG review, pure handoff plan and action view are separate; ordered actions, encoded paths, optional requests and table deletion tests pass. | Manual handoff presentation. |
| 8 | Native dialog/button/form wired in shared shell; immediate and queued close, focus callback and teardown tests pass. | Native focus trap/return, Escape, close and blank-area dismissal with playback. |
| 9 | Bounded streaming fetch uses a 30000ms AbortSignal deadline; bytes, cancellation, oversize, interruption and TimeoutError tests pass. | None for code change. |

The temporary preview now includes the actual template visual (`/group-01/visual.svg`, verified HTTP 200) so ticket 8 has a reachable manual acceptance fixture. No browser automation was used. The manual acceptance request remains pending.


Release acceptance: the user reviewed the preview and said "ok seems alright, commit and push". This accepts the nine-ticket increment for release. The detailed device/storage matrix above remains a record of wider editor acceptance, not a claim that every browser/storage failure mode was manually exercised.

Remote integration: use latest `origin/main` as the release base, keep its publication cleanup, student-guide introduction/template content and media updates. Commit the prerequisite editor redesign separately from the nine-ticket refactor. Leave the original dirty checkout, unrelated archive/build edits and thesis timeline CSV intact.

Release validation after integration with current main: 110 Node tests and 145 Python tests pass, canonical materials match, isolated offline build resolves all scripts, and actionlint 1.7.12 reports no workflow errors. The lower suite counts reflect newer main removing retired issue-publication tests. A fresh integration review found no blockers.
