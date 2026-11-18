# DDD editor redesign and completion plan

Date: 2026-11-17. Status: increments 1–2 implemented locally; manual acceptance pending.

Continuation: [handoff](2026-11-17_ddd_editor-redesign_handoff.md). Operating instructions: [maintainer manual](../MAINTAINERS.md#7-editor).

## Selected direction

The user confirmed **saving through GitHub’s website**, with the entire editor hosted on **GitHub Pages**. Students use their normal GitHub login on github.com when committing files. The editor does not need a GitHub App, credentials, personal tokens or a separate login service.

This supersedes the earlier authentication/service and direct-save proposals. The local sign-in increment, its controls/configuration, service code and dedicated tests have been removed. The preview has returned to the increment-2 protocol. A local backup of the removed increment is in `.scratch/editor-redesign/github-website-baseline/`; it is not part of the application or a deployment requirement. Do not reintroduce external hosting or ask the user to create a GitHub App.

## Student workflow

1. Choose the group and open its last published version, or start from the template. Resolve any saved browser draft before editing. Newer unpublished GitHub changes may exist; opening the published version does not fetch those changes.
2. Write and preview. Browser saving keeps a local draft; it does not update GitHub or publish the entry.
3. Use **Check and prepare GitHub steps**. The editor checks the proposed files and generates links for the selected group and branch.
4. Review the current GitHub files before replacing anything. Sign in on GitHub if prompted. Follow the generated steps: upload/replace media at their exact paths, copy the table into `media.csv`, paste the downloaded `text.md`, and delete removed files. Commit each change on GitHub. Keep the editor open while doing this.
5. Open the published entry and check its online commit after the publisher runs. A commit or checked editor step does not prove publication. Intermediate commits may show missing references until all related files are saved.

Saving text, table and attachments through GitHub remains a sequence of website operations, not one atomic multi-file save. Recovery downloads remain available independently.

## Implemented increments

### 1. Preserve work and repair draft operations

- Explicit media add/replace, unique upload paths, parsed reference rename and reference-safe removal.
- Exact removal undo including replaced bytes and table metadata; retained history bounded for recovery export.
- Malformed CSV source retained for explicit repair.
- Read-only recovery decisions, saved/loaded comparison and individual downloads.
- Versioned recovery bundles preserve identity, table source, changed asset paths/bytes, deletions, undo metadata and missing-byte declarations.
- Draft payload version 3; IndexedDB schema remains 2. Legacy drafts remain readable; missing historical information is never invented. Another-base recovery stays read-only rather than silently rebasing.

### 2. Focused writing and preview workspace

- Lossless title/authors/body projection, preserving unknown frontmatter, BOM and uniform line endings. Ambiguous source retains full-source editing.
- One normal media representation, dedicated `visual.svg` replacement, real named-media insertion and local/linked error feedback.
- Explicit Write / Preview navigation; acknowledged preview revisions, stale/unavailable state and retry.
- Plain browser JavaScript, existing content contract and production renderer retained.
- Preview protocol 1 uses an unauthenticated same-origin iframe for layout isolation. No credentials or authenticated API capability are present in the editor.

## Current scope and acceptance

The selected save workflow already exists. This correction removes the unused authentication path and updates documentation; it does not introduce another save mechanism or redesign the public course site.

Manual keyboard, narrow-screen, zoom, native editing, playback, real IndexedDB/storage failure and two-student-account acceptance remain pending. Use the [acceptance checklist](2026-10-16_ddd_acceptance-checks.md), keeping dated evidence outside the repository. Verify effective GitHub access; the group selector is not an authorization boundary.

One inherited Minor finding remains deferred: removing a main visual without a table row can reserialize otherwise unchanged CSV; exact undo retains the original source. Dedicated visual replacement leaves the CSV unchanged.

Automated browser tests are prohibited by `AGENTS.md`. Use the existing Node/Python suites and isolated static builds; do not replace the current `_site` while checking packaging. No commit, push, deployment, GitHub App registration or repository setting change is part of this correction.

## Verification

Run:

```sh
node --test scripts/test_annotations.js scripts/test_playback.js scripts/test_editor.js scripts/test_editor_recovery.js scripts/test_editor_document.js scripts/test_editor_preview.js scripts/test_editor_workspace.js
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/sync_course_materials.py
git diff --check
```

Build into a temporary output directory and verify editor module/script paths. The handoff records the latest run; automated checks do not establish hosted or student acceptance.
