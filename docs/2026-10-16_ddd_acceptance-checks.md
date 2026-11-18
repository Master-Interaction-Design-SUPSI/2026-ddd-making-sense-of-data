# DDD acceptance checks

Open checks for the new course repository, as of 2026-10-16. Procedures are in [the maintainer manual](../MAINTAINERS.md). For each completed check, record the date, the exact code and archive commits, the account or device used, and the result, in maintainer storage outside this repository.

## Before and at the first run

- [ ] `main` ruleset: pull request, the `validate` check, up-to-date branch, resolved threads, no deletion, no non-fast-forward. `student-work` ruleset: no deletion, no non-fast-forward. The `github-pages` environment is restricted to `main`. No Actions variables are set (not paused).
- [ ] First run (**Run workflow → main**): `student-work` is created as a root commit, the release is recorded and verified, and the site shows the local course-team pages and the editor. A second run reports `unchanged`.

## Student accounts

**Deferred by the user on 2026-10-14:** choosing two non-admin student pilot accounts and a Safari device and version. `lucadra` and `ginevraterenghi` have maintainer rights and cannot show student isolation.

- [ ] With each of two ordinary student accounts: edit the text, upload media, delete an unreferenced file. The entry's status line names the new commit. Commit a broken visual: the entry shows the reason and the previous commit stays online. Fix it: the line clears.
- [ ] Each account has Write on its own group repository and less than Write on every other group repository and on the course repository, inherited grants included.

## Devices

- [ ] Safari media behavior, keyboard and touch on the selected devices; record device and version.
- [ ] Manual editor checks (§12) in Chrome and Safari for the revision under review. Automated browser tests are prohibited by current `AGENTS.md`.

## Editor recovery increment, 2026-11-17

Unit tests cover domain mutations, bounded recovery import/export and controller recovery decisions; they do not establish real browser storage or hosted acceptance.

- [ ] In real IndexedDB: replace an asset, remove it, reload, resume and undo; confirm exact bytes, original name/source and text/table content. Remove and undo a new attachment too.
- [ ] Download and import current and saved recovery bundles with nested paths, binary assets, deletions and missing-byte declarations. Verify old version 1/2 drafts, including a null/invalid/absent table. Another-base imports remain read-only and downloadable.
- [ ] With a pending draft, compare versions, retry a failed load and cancel **Use loaded version**: the original draft survives and cannot be overwritten by comparison. Confirmed replacement resets local edits to the loaded version.
- [ ] Denied storage, quota failures, concurrent tabs and interrupted transactions preserve the preceding durable draft. Text-only fallback explicitly reports missing media after reload.
- [ ] Attach two files consecutively: the second gets its own unique path. Explicit replacement keeps the selected asset's path/name/source; canceling replacement returns to an addition. Exercise the retained-history capacity error and confirm the previous draft still exports. Repair malformed CSV without adding an asset silently replacing it.
- [ ] Rename mixed-case references while preserving fenced/inline code, escaped examples, frontmatter, lists and tables. Removing a used asset names its affected references and leaves prose unchanged.
- [ ] Keyboard, narrow-screen and zoom inspection of recovery/repair controls and the existing preview. Record actual device/version and any interventions outside this repository.

## Editor workspace increment, 2026-11-17

Node/VM tests cover lossless projection, controller mutations and preview protocol lifecycle; they do not establish browser layout, native editing, media playback or device acceptance.

- [ ] In Chrome and Safari, edit title/authors/body in LF and CRLF sources with unknown frontmatter and a UTF-8 BOM. Download and compare untouched fields/bytes. Duplicate or multiline metadata and mixed endings retain full-source editing with an explanation.
- [ ] At narrow widths and 200% zoom, use **Write / Preview** with keyboard and touch. Return to the same selection and preview scroll position. Insert media in body and raw source; check native typing, selection and undo behavior. Background updates never move focus.
- [ ] Confirm one card per normal asset, use counts, rename/source editing, Insert/Replace/Remove/Undo and explicit file paths. Replace the main visual with a 1920 × 1080 SVG, download `visual.svg`, and confirm no inline row or unrelated CSV change. Exercise malformed-table repair and all recovery controls in the new layout.
- [ ] Check available-width, 1280px and 390px previews; horizontal scrolling stays inside the preview. Block/reload the frame and interrupt an asset fetch: status remains stale/unavailable until the current revision is acknowledged. Retry renders the latest draft.
- [ ] Verify reduced motion, hidden-view playback suspension, external embeds, renderer cleanup and navigation back/forward. Review errors link and focus the relevant action; storage, GitHub and publication claims remain distinct.
