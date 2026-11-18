# DDD editor redesign handoff

Date: 2026-11-17. Increments 1–2 are implemented locally. Latest direction: host the entire editor on GitHub Pages and save through GitHub’s website, using the normal GitHub login there. The unused sign-in service and controls have been removed. Real browser storage, workspace/device and student acceptance remain pending. No commit, push or deployment was made.

Read the [current plan](2026-11-17_ddd_editor-redesign_plan_draft.md). The user has selected the existing GitHub website handoff as the save workflow. No external host, GitHub App, token entry or in-editor authentication is required. Earlier service/direct-save proposals are superseded.

## Start here

1. Read `AGENTS.md`, this handoff, the current plan and the [maintainer manual](../MAINTAINERS.md). The user wants minimal, modular changes and direction between increments. Saving through the GitHub website is selected and already implemented; manual workspace/recovery/save acceptance is next. Do not restart the authentication discussion or implement a service.
2. Inspect the dirty working tree before editing. Work is local on `main`, HEAD `78a0326ffd2dc107c64242cc3910559d107646a7` at handoff. This identifier is a checkpoint, not an instruction to reset or checkout that revision.
3. Preserve all existing changes. Many predate this editor task. Do not reset, clean, blindly restore backups, stage everything, create commits, push, deploy or change repository settings as part of this handoff.
4. Do not run automated browser tests, Playwright automation, `check_editor_browser.js`, `check_editor_fallback.js` or `check_editor_fidelity.js`. Current `AGENTS.md` overrides older browser-script instructions. Node tests with the VM controller fixture are unit tests and do not launch a browser.

## What increment 1 delivered

| Boundary | Implemented behavior |
| --- | --- |
| `editor/model.js` | Distinct add/replace operations; unique upload paths; media rename updates parsed names; removal refuses text references; exact removal undo restores replacement bytes and original table metadata/source. Removed uploads remain retained for undo. Further uploads are rejected before mutation if retained assets exceed the existing recovery byte/file limits. |
| `static/media-table.js` | Shared named-reference source ranges and rename logic using the existing Markdown lexer and media-name grammar. Case-insensitive names change in prose, lists and tables; frontmatter, code, escapes and link definitions remain untouched. |
| `editor/session.js` | `RecoveryGate`, version 3 draft snapshots and checked restoration. Carries explicit group identity, base ID/inventory, text, table, changed assets, missing paths, deletions and removal undo metadata. |
| `editor/recovery.js` | Version 1 `ddd-editor-recovery` JSON encoding with base64 changed assets, exact paths, size/SHA-256 checks and bounded import. Legacy version 1/2 drafts remain exportable; historically absent table/base inventory stay absent. Decoded imports normalize to draft version 3. |
| `editor/editor.js` | Read-only recovery decisions; saved-versus-loaded text comparison; complete current/saved bundle downloads plus individual saved text/table/asset downloads; explicit import/resume; malformed CSV repair; upload reset; replacement cancellation; storage-conflict locking. Failed loading retains the existing draft. |
| `editor/index.html` | Recovery, comparison, CSV repair and cancel-replacement controls. At that checkpoint the workspace layout had not been redesigned; increment 2 below supplies it. |
| Tests and CI | `scripts/test_editor_recovery.js` and `scripts/editor_test_fixture.js`; both validation and publication workflow Node test lists include the new suite. |
| Operating docs | Maintainer manual, acceptance checklist and plan status updated. |

IndexedDB **database schema remains 2**. Draft **payload version is 3**. `editor/draft-store.js` was not changed. Preserve the existing transactional compare-and-swap, blob sharing and tombstone behavior.

Recovery bundles contain changed attachment bytes, including removed replacements retained for undo. Base inventory pins unchanged source files; bundles do not claim to contain every unchanged repository asset. Legacy records cannot reconstruct metadata or bytes they never stored. Missing bytes remain explicit.

A matching group and base are required to resume. Another-base draft remains read-only, comparable and downloadable; automatic rebasing and conflict merging were not implemented. A historical deletion without exact undo metadata offers a confirmed, explicitly labelled restoration of the original repository file, not recovery of missing historical metadata.

The existing editor still loads the last published snapshot or template and uses manual GitHub handoff. It does not load an authenticated current GitHub snapshot or save a multi-file commit directly. The preview remains an unauthenticated same-origin iframe. Do not describe GitHub commit creation, preview fidelity, publication or real-storage acceptance as completed.

## Increment 2 delivered locally

Plain browser JavaScript, the content contract, Markdown/annotation language and production renderer remain in use. The public course site and archive were not redesigned.

| Boundary | Implemented behavior |
| --- | --- |
| `editor/index.html`, `editor/editor.css` | Quiet writing/preview workspace, compact group/browser status bar, headings/rules, explicit **Write / Preview** navigation. Both panes keep their DOM; the narrow-screen preview keeps its dimensions while hidden. Actual layout and native editing acceptance remain manual. |
| `editor/document.js` | Lossless title/authors/body projection and edits; unknown frontmatter, BOM and uniform line endings retained. Duplicate/multiline metadata, mixed endings and ambiguous/incomplete headers use intact full-source editing with an explanation. Structured edits reproject after header-boundary changes and preserve selection when updating controls. Source loading retains UTF-8 BOMs. |
| Media controls | One card per normal asset with names/sources/file/use counts and Insert/Replace/Remove. Exact paths stay available in details and GitHub handoff. The dedicated main visual replaces exactly `visual.svg`, downloads under that name and leaves CSV unchanged. Existing authored visual metadata remains editable beside it; no inline visual row is created automatically. |
| Insertion | Native text selection/replacement in body or full source; **Insert media** chooses a real named asset. Note/link syntax controls remain available. |
| `editor/preview-client.js` | Parent lifecycle and transport separated from the controller. Requested/acknowledged revisions remain distinct; stale/unavailable status, bounded waiting and explicit retry. Failed startup can restart the iframe; load-event handshake does not create a reload loop. Debounced builds send a complete required asset set under a fresh revision. |
| `editor/protocol.js`, `editor/preview.js` | Exact sender/origin/version/schema checks retained. Version 1 adds a boolean `visibility` message to suspend hidden narrow-screen renderer/playback. Same-group render updates retain scroll position, and replaced/removed object URLs are revoked. Production rendering and reduced-motion behavior remain shared. Real playback, scroll and back/forward-cache acceptance are pending. |
| Feedback | Action/field errors stay near controls; failed review links to the relevant field and focuses the summary. Explicit view/recovery navigation focuses its destination; preview updates only change status. Browser storage, GitHub handoff and online publication remain separate meanings. |

A fresh read-only increment-2 review found three Important issues. Failing-then-passing regressions now cover pasted-frontmatter reprojection (including unsafe fallback), failed-startup retry, and existing visual metadata access. No new Minor findings were deferred. The reviewer did not claim manual keyboard/device/media or real-storage acceptance; generated artifacts and CI lists were checked separately.

The new Node suites are `test_editor_document.js`, `test_editor_preview.js` and `test_editor_workspace.js`. The existing controller regression now shares `editor_test_fixture.js`; prior recovery regressions remain. Both workflow Node test lists and the generated-editor packaging test include the new modules/suites.

A fresh read-only code review found five Important issues in increment 1, plus the individual-download regression regraded Important; all were fixed with failing-then-passing regressions. Covered cases include continued list paragraphs, root/nested link definitions, repeated table references, retained-history capacity, silent storage conflicts, duplicate provisional names, canceling replacement, and saved-file downloads. Do not redo these fixes or remove their tests.

## Later steps and unresolved acceptance

- The entire editor stays on Pages. Normal GitHub login and commits happen on github.com through the generated handoff links. Keep the editor’s local-draft status distinct from GitHub saving and publication.
- The last published snapshot remains the default base; newer GitHub edits may exist. Review current repository files before replacing them. The website handoff uses separate commits and offers no atomic multi-file save or automatic merge.
- Browser storage checks require actual IndexedDB/localStorage behavior: quota/denied storage, interrupted transactions, concurrent tabs, reload/resume/undo, legacy drafts and another-base recovery. The VM fixture is not evidence of these behaviors in Safari or Chrome.
- Manual keyboard, narrow-screen, zoom, media and focus checks remain pending, as do the eventual two-student-account tasks and effective-access verification. Do not infer account isolation from the group selector or treat historical account/device notes as current evidence.
- Use the current [acceptance checklist](2026-10-16_ddd_acceptance-checks.md); keep dated device/student/hosted acceptance evidence outside the repository.
- One deferred Minor finding: removing a main visual that has no table row can reserialize an otherwise unchanged CSV. Undo retains the original exact source. The dedicated replacement control leaves unrelated CSV untouched. The legacy removal operation remains unchanged; this older Minor finding is still deferred.
- Do not restore an editor link in the public site header; that earlier navigation decision remains outside scope.

## Verification checkpoint

The Pages-only correction removed only the verified sign-in increment, using its exact reverse patch after backing up its files in `.scratch/editor-redesign/github-website-baseline/`. Earlier writing/recovery/media work is preserved. The current plan and operating docs now identify GitHub’s website as the save path. Fresh verification passed **88 Node tests** and **155 Python tests**, canonical-material drift, editor JavaScript syntax, document links and `git diff --check`. An isolated static build matched editor sources and resolved script paths, with no auth files or service code. The existing `_site` was preserved; all 35 other files from the pre-sign-in dirty baseline remain byte-identical. No automated browser test, commit, push or deployment ran.

The final implementation run passed **88 Node tests** and **155 Python tests**, canonical-material drift, JavaScript syntax and `git diff --check`. A complete isolated static build contained the new editor modules and valid script paths; the existing `_site` was preserved. `actionlint` was unavailable locally. No automated browser or hosted acceptance check ran. These are recorded results, not a substitute for verification after new changes.

```sh
node --test scripts/test_annotations.js scripts/test_playback.js scripts/test_editor.js scripts/test_editor_recovery.js scripts/test_editor_document.js scripts/test_editor_preview.js scripts/test_editor_workspace.js
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/sync_course_materials.py
git diff --check
```

Build in a temporary destination; an ordinary offline build would replace a potentially useful imported `_site` snapshot:

```sh
python3 - <<'PY'
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, 'scripts')
import build_site
with tempfile.TemporaryDirectory(prefix='ddd-editor-check-') as directory:
    build_site.OUTPUT = Path(directory) / '_site'
    build_site.main()
    for name in ['editor/editor.js', 'editor/document.js', 'editor/preview-client.js', 'editor/protocol.js', 'editor/preview.js', 'editor/session.js', 'editor/recovery.js', 'static/media-table.js']:
        assert (build_site.OUTPUT / name).read_bytes() == Path(name).read_bytes(), name
PY
```

Extend the package verification and both workflow test lists when new modules/suites are added. Run meaningful domain/protocol/generated-artifact tests; do not add tests that only mirror DOM markup or CSS.

## Local continuation artifacts

- `.scratch/editor-redesign/increment2-progress.md`: increment 2 verification and review ledger. `increment2-baseline/` preserves the relevant pre-increment bytes; `increment2-manifest.json` records current continuation file digests.
- `/tmp/ddd-increment2-node.log` and `/tmp/ddd-increment2-python.log`: increment 2 test output. These are local/ephemeral, not hosted acceptance evidence.

- `.scratch/editor-redesign/progress.md`: increment 1 decisions, review fixes, deferred finding and verification ledger.
- `.scratch/editor-redesign/baseline/manifest.json`: SHA-256 manifest and copies of the original changed/untracked files before implementation. Compare against these when separating this increment from earlier local work, especially `editor/editor.js`, `editor/model.js`, `scripts/test_editor.js` and `MAINTAINERS.md`. Do not restore wholesale.
- `.scratch/editor-redesign/increment1.diff`: refreshed increment-only patch, including final review fixes and this handoff. It excludes unrelated baseline changes and is more useful than the full dirty-tree diff for attribution.
- `.scratch/editor-redesign/handoff-status.txt` and `handoff-manifest.json`: working-tree status and changed-file digests captured after writing this handoff.
- `/tmp/ddd-final-node.log` and `/tmp/ddd-final-python.log`: final implementation test output. These and ignored `.scratch` artifacts are local/ephemeral; preserve required release evidence separately.

The older `.scratch/tickets/2026-10-14_ddd_editor-v2_handoff.md` describes an earlier publication project. Its branch, deployment, account, browser and authority notes must not be adopted as current instructions for this redesign.
