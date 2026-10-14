# Maintainer manual

Scope: course site, registry, import, publication, editor, archive. Revision: 2026-10-14.

**WARNING**: irreversible loss of student content or published state.
**CAUTION**: stalled publication or wrong release.

## 1. Status

| Item | State |
| --- | --- |
| Production | `main` `9190958`; V1 publisher. Pages run `38525824509`; release metadata matches deployment record. |
| Published | `example` (local); `ddd-abstract` from `ginevraterenghi/ddd-abstract` `3a8f717`, 19 files, 22,466,039 B. |
| Hidden | `dust` (`2026-ddd-pilot-alpha`), `pilot-beta` (`2026-ddd-pilot-beta`). Sources intact. |
| V2 | Branch `v2`: release and archive cutover authorized on 2026-10-14. Switches remain off until protected refs, verified V1 capture and seed comparison pass. |
| Settings | Issues disabled. `main` unprotected: ruleset removed (maintainer-confirmed); `protected: false` (read-only check, 2026-10-14). |
| Checkpoint | `.scratch/tickets/2026-10-14_ddd_editor-v2_handoff.md`; scope `…_plan_final.md`; rationale `…_review.md`. Local, Git-ignored. |

The maintainer authorized the V2 commit, deployment and archive cutover on 2026-10-14. Local tests alone do not establish live acceptance.

## 2. Terms

| Term | Meaning |
| --- | --- |
| Group | Student team; owns one public repository. |
| Slug | Permanent group identifier: entry folder, URL fragment, archive path. |
| Registry | `groups.json`. Sole list of groups. |
| Source | Group repository at one exact commit. |
| Snapshot | Complete selected file set of one group, with sizes and digests. |
| Candidate | Built `_site`, not yet served. |
| Release | Identity of one built site: code commit, archive commit (V2), manifest and site digests. |
| Archive | Orphan branch `student-work` in the course repository. Durable copy of accepted snapshots. V2 only. |
| Accepted | Candidate committed to `student-work`. |
| Attempted | Deployment record names the release. |
| Verified | Served site matches the release. The only state meaning "published". |
| Seed | One-time creation of `student-work` from the verified V1 release. |
| Cutover | Transfer of publication authority from V1 to V2. |
| Request | Issue labelled `publication-request`, asking for a refresh. |

## 3. Architecture

### 3.1 Authority

| Datum | V1 (production) | V2 (gated) |
| --- | --- | --- |
| Code, registry | `main` | `main` |
| New student content | Group repository | Group repository |
| Last-good content | Served `publication.json` and files | `student-work` |
| Expected release | Deployment record, task `course-publication`, environment `course-publication-check` | Same record; `Main-Commit:` trailer on archive commit |
| Exact retry | Artifact `course-pages-<run>-<attempt>`, 30 days | Reconstruction from `main` + `student-work` pair |

### 3.2 Files

| Path | Function |
| --- | --- |
| `groups.json` | Registry. |
| `static/contract.json` | Content contract v2; read by Python and editor. |
| `static/main.js`, `bootstrap.js`, `playback.js` | Renderer, production bootstrap, media lifecycle. |
| `static/annotations.js`, `references.js` | Annotation parser; sole reference collector (browser, Node, Python). |
| `editor/` | Editor. Static; no compilation. |
| `scripts/build_site.py` | Build `_site`. Offline by default. |
| `scripts/import_groups.py`, `github_content.py` | Import; GitHub transport. |
| `scripts/content_contract.py`, `check_content_links.js` | File selection; reference check. |
| `scripts/publish_site.py`, `publication_state.py`, `deployment_records.py` | V1 controller; served-state recovery; deployment records. |
| `scripts/publish_archive.py` | V2 controller: `admit prepare accept build record authorize verify finalize answer`. |
| `scripts/archive_state.py`, `archive_transaction.py`, `archive_git.py`, `archive_release.py` | Archive manifest; candidate transaction; Git transport and writer; release identity. |
| `scripts/publication_requests.py` | Request capture and answers. |
| `.github/workflows/pages.yml` | Publication: V1 job and V2 jobs. |
| `.github/workflows/validate.yml` | PR gate. Offline. |
| `.github/ISSUE_TEMPLATE/` | Request form; blank issues off. |
| `.github/publishing.json` | Editor request policy. |
| `templates/group/` | Group repository template. |

## 4. Registry: `groups.json`

| Field | Rule |
| --- | --- |
| `slug` | `[a-z0-9]+(-[a-z0-9]+)*`, ≤ 63 chars. Permanent. Reserved: `example static scripts _site templates editor index entries groups`. Must not collide with a local folder. |
| `repository` | `owner/name`; no URL, no `.git`; unique, case-insensitive; public. Redirects rejected. |
| `branch` | Default `main`. Valid Git ref name. |
| `state` | `live`, `frozen` or `hidden`. Legacy `enabled`: `true` = `live`, `false` = `hidden`. Conflict rejected. V1 rejects `frozen`. |
| `exclude` | Exact optional media paths, dropped before download and size accounting. Cannot drop `text.md` or `visual.svg`. A referenced excluded file fails validation. Reported as `excluded_files`. |
| `require_visual` | Default `true`. `false` permits absent `visual.svg`; a present one is still validated. |

Maximum 15 groups. Unknown fields rejected. Validation is offline; the importer checks visibility.

Current exclusions: `ddd-abstract` drops `media/12.mov` and `media/4.mov` (each > 20 MiB). Remove when replacements exist. Exclusion never alters the source repository.

## 5. Content contract

Source: `static/contract.json` (v2). Python constants are adapters.

| Class | Paths | Limit |
| --- | --- | --- |
| Text | Root `text.md`, UTF-8, title/authors front matter. Required. | 256 KiB |
| Main visual | Root `visual.svg`; `viewBox="0 0 1920 1080"`; optional width/height = 1920/1080 px. Other root `visual.*` rejects the entry. | 5 MiB |
| Image | `.svg .png .jpg .jpeg .webp .gif .avif` | SVG 5 MiB; others 20 MiB |
| Audio | `.mp3 .m4a .wav .ogg .aac .flac` | 20 MiB |
| Video | `.mp4 .webm .mov` | 20 MiB |
| Group | ≤ 100 selected files | 50 MiB admission; 100 MiB legacy V1 recovery |
| Site | Whole candidate | 900 MiB (943,718,400 B) |

Paths:

- Media at root or under `media/`, subfolders allowed.
- Component pattern `[a-z0-9][a-z0-9._-]*`. Case-sensitive. Separator `/`.
- ≤ 512 bytes, ≤ 16 components.
- Rejected: absolute paths, `.`/`..`, empty segments, backslashes, control characters, Git LFS pointers.
- A bad name on eligible media rejects the update.
- Other files (READMEs, HTML, JavaScript, workflows, design sources) are ignored and never published.

SVG: static UTF-8. Rejected: scripts, event handlers, foreign markup, links, animation, entities, processing instructions, external resources. Permitted: local paint references; embedded PNG/JPEG/WebP data images.

References: Markdown links and images, including reference syntax, and annotation targets must resolve to selected files. `./media/x.svg` and local fragments pass. Missing, ignored, excluded or cross-group targets reject the update. `http`, `https`, `mailto` pass unfetched; other schemes reject.

Rejected update: previous snapshot retained. No previous snapshot: group skipped. Audio/video codecs are not validated; browser check required.

## 6. Renderer

Invariants:

- `text.md` is data. Contributor HTML is escaped. Unsafe link and media schemes are rejected.
- SVG loads only through `<img>`.
- Marked is vendored; `static/vendor/LICENSE.marked.md` ships with the site.

| Element | Behavior |
| --- | --- |
| Annotation | `[[label\|target\|type=…;style=…;color=…]]`. Keys in any order. Duplicate or unknown key, or bad value: literal text. Two-field legacy form valid. |
| Note | Expanded by default. Marker and content in braces. Black italic; `style=normal` removes italic. |
| Bibliography | Expanded by default. `(■ Author, year. Title)`. Black; overrides surrounding and modifier colors. Target as text or link, never embedded. |
| Quote | Native quotation marks on reveal. |
| Inline image | Thumbnail. Hover/focus preview anchored to the thumbnail, opaque, fitted to viewport. Tap/Enter/Space pins. |
| Inline video | No buttons. Muted autoplay when half-visible, page visible, lightbox closed. Hover preview plays with sound. Reduced motion or refused autoplay: still. |
| Inline audio | Native controls. Hover plays; exit pauses. Browser policy may require a first click. |
| External embed | Manual. Loads on reveal; unloads on inactivity; provider link shown. |
| Dismissal | Escape, outside activation, scroll, resize, navigation, focus loss, page hide. |
| Prose | Newlines and blank lines between adjacent paragraphs become spaces. Headings, lists, quotes, code stay separate. 16 px type, 40 px leading. |
| Main visual | Image element; lightbox. Escape closes lightbox before playback refresh. |

## 7. Editor

URL: `<site>/editor/`. Build outputs:

| File | Content |
| --- | --- |
| `editor/preview.html` | Production shell; only bootstrap and asset paths substituted. |
| `editor/groups.json` | Public registry fields, `editable` (= `live`), contract version, request policy. |
| `editor/template/`, `template.json` | Template files with SHA-256. |

Open: last published snapshot (default; hash-checked against served `publication.json`) or template (new group). Non-`live` groups are read-only.

Drafts:

- IndexedDB schema 2. Revision compare-and-swap; digest-addressed shared blobs; discard tombstones.
- Quota failure keeps the previous complete draft. Saves serialized; switching awaits save; restored assets hash-checked.
- Concurrent tab: conflict; explicit restore or discard.
- Storage denied: localStorage text-only fallback; missing media listed. Old backups kept until explicit migration.
- Label: "Saved in this browser only."

Hand-off, generated per draft, not atomic:

1. Review current `text.md` on GitHub. Stale-text comparison warns; equality is not proof of freshness.
2. Upload new media; commit. Replace referenced media under a new name.
3. Paste text into `text.md`; commit.
4. Delete removed files.
5. Submit request (if enabled) or read the run summary.

Intermediate totals are checked. A valid intermediate commit can publish.

Preview: same-origin iframe; layout isolation, not a sandbox. Protocol v1, types `hello ready render ack error`; window, origin, schema, slug, path, size and revision checked. Only editor-owned object URLs bypass scheme checks; `blob:` and `data:` in text are rejected.

Editor success = local checks passed. It does not prove commit or publication. Importer-only checks: visibility, tree completeness, Git object types, blob integrity, SVG security.

## 8. Import

Per group, per run:

1. Four metadata REST calls: repository, ref, commit, tree. Tree ≤ 2,000 entries, untruncated.
2. Select files; apply exclusions, path rules, limits. Reject symlinks, submodules, collisions.
3. Reuse verified bytes; download the rest from `raw.githubusercontent.com/<owner>/<repo>/<commit>/<path>` without credentials.
4. Verify Git blob SHA and size; validate content.

Bounds: 15 s socket timeout; 3 attempts; 180 s per group; 15 min per run. JSON ≤ 2 MiB. REST defers at ≤ 200 remaining quota. No REST-blob fallback. No checkout; group code never runs.

Early exit: all inputs equal the verified release → `unchanged`, without full Pages recovery.

Report: `.scratch/group-import-report.json` or `--report`.

| Field | Values |
| --- | --- |
| `status` | `imported`, `failed`, `disabled` |
| `publication_status` | `updated`, `retained`, `skipped`, `disabled` |
| `selected_source` | Repository, branch, commit actually published; may differ from attempt. |
| `build_status` | `prepared`, `failed`. Prepared ≠ published. |

Requirements: Python 3.9+, Node. No packages. Optional `GITHUB_TOKEN` raises API quota; never sent to raw or Pages hosts.

## 9. Publication: V1 (production)

Workflow **Publish course website**, job `publish`. Active while `DDD_ARCHIVE_ENABLED` ≠ `true`.

| Trigger | Effect |
| --- | --- |
| Push to `main` | Refresh. |
| Schedule `7,17,27,37,47,57 * * * *` | Refresh. GitHub may delay or drop runs. |
| **Run workflow → main**, inputs off | Refresh. |
| `retry_publication` | Redeploy exact recorded candidate. |
| `first_publication` | Initial rollout. Done 2026-10-13. |

Sequence: tests → import → recover failed groups from served site → build → upload → record → deploy → verify → summary.

- One concurrency group `pages`; `cancel-in-progress: false`.
- Queued runs recheck `main` before prepare and before record.
- Unchanged snapshots and source tree: no deployment. Code or registry change: deployment.
- Served `publication.json`: per group repository, branch, commit, files, sizes, SHA-256. Fallback source.
- `release.json`: run, source commit and tree, site digest, manifest digest.
- Deployment record written before deploy. It is an expectation, never content.
- Verify: release marker and manifest, cache-busted; ≤ 12 attempts, 10 s apart.
- Unverified record blocks later runs. Intentional.
- Recovery reads: canonical HTTPS URL; no redirects; no token; 15 s socket, 180 s per group; manifest pinned by digest. Any mismatch stops the build.
- Failed group: its complete served snapshot is restored and revalidated. Old and new files never mix.
- Permissions: contents read, actions read, deployments write, pages write, id-token write. No contents write.
- Run summary: per group status, selected and attempted revisions, errors.

**WARNING**: Never select `first_publication` to bypass a recovery error. It declares that no prior content exists.

**WARNING**: Never deploy an offline build over an imported publication. It lacks `publication.json`.

**CAUTION**: Schedules stop after 60 days without repository activity. Restore: **Actions → Publish course website → Enable workflow**, then **Run workflow → main**.

Local builds:

```sh
python3 scripts/build_site.py                      # offline; local entries only
python3 scripts/build_site.py --import-groups \
  --published-url https://master-interaction-design-supsi.github.io/2026-ddd-making-sense-of-data/
python3 scripts/build_site.py --import-groups --first-publication   # disposable preview only
```

## 10. Publication: V2 (gated, not deployed)

### 10.1 Switches

| Switch | Effect when set |
| --- | --- |
| Variable `DDD_ARCHIVE_ENABLED=true` | V2 jobs replace the V1 job. |
| Variable `DDD_ARCHIVE_WRITES_ENABLED=true` | Job `archive` may push `student-work`. |
| Variable `DDD_REQUESTS_ENABLED=true` | Issue requests admitted; job `answer` runs. Stop switch for requests. |
| `.github/publishing.json` `requests_enabled` | Editor shows the request step. UI only. |
| Dispatch input `seed_archive` | Seed `student-work` from verified V1. No deployment; no cutover. |
| Dispatch input `reconcile_seed` | Advance an existing V1 seed to current verified V1, retaining disappeared snapshots hidden. Archive publishing must still be disabled. |
| Dispatch input `rebind_archive` | Apply the exact reviewed `.github/archive-rebind.json` plan after V2 cutover. No import or deployment in that dispatch. |

**CAUTION**: Disabling the issue form does not block API-created issues. Use `DDD_REQUESTS_ENABLED`.

### 10.2 Jobs

| Job | Permissions | Function |
| --- | --- | --- |
| `pin` | contents read, pages read | Pin latest `main` at execution. |
| `admission` | contents read, issues read, deployments write | Choose mode; capture request batch at cutoff; reserve cooldown. |
| `prepare_archive` | contents read, deployments read | Run tests; read archive; import; emit candidate or recovery pair. |
| `archive` | contents write, deployments read | Revalidate registry, parent and verified release; expected-parent fast-forward of `student-work`. |
| `publish_archive` | contents read, deployments write, pages write, id-token write | Build, record, authorize, deploy, verify, finalize. |
| `answer` | contents read, issues write | One bot comment per request; close where final. |

All checkouts at the pinned commit, `persist-credentials: false`. Imported content never runs.

Artifacts: `v2-{admission,prepared,accepted,result}-<run>-<attempt>`, `v2-pages-<run>-<attempt>`; 7 days. The envelope binds kind, repository, main commit and tree, run, producing attempt, registry digest, file digests. Members: `metadata.json`, `batch.json`, `report.json`, optional `candidate.tar`, `seed-manifest.json`. Extra members and links rejected. Reports ≤ 8 MiB; metadata and seed manifest ≤ 2 MiB. `EXPECTED_PRODUCER_ATTEMPT` admits an earlier successful attempt of the same run only. Artifacts transport; Git refs authorize.

### 10.3 Modes

| Mode | Condition | Action |
| --- | --- | --- |
| `seed` | Dispatch `seed_archive` | Requires verified V1 release. Captures served bytes. Rejects groups > 50 MiB. Refuses if `student-work` exists. 20 min total, 180 s per group. No deployment. |
| `reconcile_seed` | Explicit dispatch, archive publishing disabled | Rechecks current verified V1 in prepare/writer. Updates a parent marked `v1-seed`; retains disappeared snapshots hidden. No deployment or answer. |
| `rebind` | Explicit dispatch, reviewed exact-parent plan, verified V2 | Preserves old identity/content under a deterministic hidden retired slug; new active identity starts without a fabricated snapshot. No deployment or answer. |
| `refresh` | Eligible event | Import live groups → accept → build → deploy → verify. |
| `unchanged` | Archive, `main` and site digest equal verified release | No write, no deployment. |
| `pending` | Accepted archive ahead of release, same `main` | Build accepted pair; no import. |
| `retry` | Latest attempt unverified, same code | Reconstruct pair from refs; redeploy. |
| `repair` | Latest attempt already served | Record verification; no deployment. |
| `deferred` | Requests off, issue ineligible, cooldown, or quota ≤ 200 | No work. Requests stay open. |
| `invalid` | Malformed Group field | No import. Answer; close. |
| `superseded` | `main` moved | Stop. No answer. |

### 10.4 Archive

- Orphan branch `student-work`. Layout `<slug>/…` plus manifest v2 `publication.json` (≤ 8 MiB; V1 manifests ≤ 2 MiB).
- Manifest records per group: state, repository, branch, source commit and tree, contract version, registry settings, files (size, SHA-256, Git blob SHA), excluded paths and blob IDs when observed. Unknown IDs stay explicit unknowns.
- Commit only on content, provenance or state change. Expected parent; no force; sole writable ref `student-work`. Push credentials ephemeral, GitHub-only.
- Commit trailer `Archive-Authority: v1-seed` or `v2` establishes the publishing authority. Transition to V2 creates a child even with an unchanged content tree. Seed reconciliation cannot move authority back to V1. Missing/ambiguous markers require explicit inspection; they are not guessed from snapshot schema. Protection against branch deletion/non-fast-forward remains a required live invariant.
- Read: pinned shallow fetch into an isolated bare repository; `git ls-tree -r -z -l --full-tree` preflight (regular blobs, unique paths, exact inventory); then bounded expansion and hash/content validation. Transfer cap: 900 MiB + 8 MiB + 16 MiB.
- Pages receives visible groups only. The archive manifest never enters `_site`. Hidden = absent from Pages, readable in the public archive.

| State | Source read | Pages | Archive bytes |
| --- | --- | --- | --- |
| `live` | Yes, one commit per run | Latest accepted; retained on failure | Kept |
| `frozen` | No | Pinned snapshot | Kept; snapshot required |
| `hidden` | No | Absent | Kept if present |

- Removed registry row: archive entry kept as hidden. Bytes never deleted.
- Routine slug rebinds are rejected. An explicit reviewed migration preserves the previous identity and snapshot under a hidden `retired-…` slug, then installs the new registry identity without a snapshot. The subsequent normal publication imports that repository. Local preparation and independent writer admission are implemented; the live rehearsal is pending.
- Unreachable source: last accepted snapshot kept.

### 10.5 Release

Identity v2: `main` commit, archive commit, tree, manifest and site digests. Site digest excludes `release.json`.

- Archive write fails: no deployment.
- Deployment fails: archive kept; next run retries the pair before any new import.
- `authorize` rechecks `main` and latest attempt before the Pages mutation.
- Newer archive or release authority blocks rollback.
- Verification: release, manifest, one changed-text probe, one changed-media probe. Sampling, not a full hash of served bytes.
- API budgets: deployment transport 40 calls per client; issue transport 60. Ambiguous writes never retried automatically.

### 10.6 Requests

- Form `.github/ISSUE_TEMPLATE/publish.yml`; label `publication-request`; field **Group** (slug).
- Trigger `issues: opened`. Unlabelled or ineligible issues run in concurrency `ignored-issue-<n>`; they cannot displace publication.
- Capture at cutoff: ≤ 6 requests, ≤ 3 pages of 100. Body ≤ 8 KiB, hashed. Later, edited, closed, unlabelled and overflow requests wait for the next run.
- Issue-triggered refresh ≥ 10 min apart. Reservation: deployment task `course-refresh`, environment `course-refresh-cooldown`, written before import. Push, schedule and dispatch exempt.
- A request refreshes all live groups. Issue text never reaches shell, refs or paths.
- One bot comment per issue, marker `<!-- ddd-publication-request:v1 -->`. Reruns update it and repair failed closes.

| Outcome | Issue |
| --- | --- |
| Verified; group `updated` or `unchanged` | Published revision stated; closed. |
| Invalid content | Correction stated; closed. |
| Group `hidden`, `frozen` or disabled; invalid Group field | Policy stated; closed. |
| Transient source failure; deferred | Open. |
| Build, archive, deploy or verify failure | Failed stage stated; open. |
| Superseded | No answer. |

Schedule stays at ten minutes until the request pilot passes; then hourly, off the hour (e.g. `17 * * * *`).

## 11. Groups and access

### 11.1 Roster

Keep outside this repository, maintainer-controlled. Per group: name, slug, repository, verified GitHub usernames, invitation state, effective access, pilot result. A display name is insufficient; students confirm the exact account. `groups.json` holds configuration only.

### 11.2 Provisioning

1. Confirm owner and username mapping.
2. Create one public repository, default branch `main`. Copy the **contents** of `templates/group/` to its root. No Pages, secrets or workflows. Personal repositories accepted; private rejected.
3. Owner adds teammates with **Write** (**Settings → Collaborators and teams**). Track acceptance; an invitation is not access.
4. Audit effective permissions: organization base permission, team grants including parent teams, direct grants, custom roles. Organization-wide Write, Maintain or Admin breaks isolation. A lower direct grant does not cancel a higher inherited one. Changing organization defaults affects other projects; assess first. Record results.
5. Check rulesets on the group's `main`: direct commits, no mandatory PR or approval.
6. Register as `hidden` through a reviewed PR; `python3 scripts/build_site.py` must pass. Switch to `live` by reviewed PR when ready.
7. Hand over: repository link, slug, site link, editor link, [publication results](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-making-sense-of-data/actions/workflows/pages.yml). Confirm an ordinary account can read a run's **Course publication** summary.

**WARNING**: A `live` group still holding template content publishes it.

Acceptance, ordinary student account (never an admin session): commit to own `text.md`; see it published; effective permission below Write in every other group repository and in the course repository.

### 11.3 Changes

- Rename, transfer or branch change: keep the public slug and use the explicit rebind procedure below. A routine registry edit alone is rejected by V2.
- Membership: verify username; invite to the correct repository only. Review direct and inherited grants of departing members. Removing access does not unpublish.
- Unpublish: set `hidden` by reviewed PR; confirm the next verified release omits the entry. Never delete `text.md` or `visual.svg` to unpublish: an incomplete update retains the previous snapshot.
- Failed import: students correct content from the run summary; maintainers correct address, branch, quota and publication faults.

### 11.4 Course repository

Review every PR to `main`, including the full **Files changed** list. No ruleset exists: review is unenforced. Required: `validate` as required check (not set); `github-pages` environment restricted to `main`. PR validation is offline, read-only, fixture-based.

## 12. Recovery

### 12.1 V1: failed or cancelled deployment

1. Wait until the run stops.
2. Run an ordinary refresh. If the release was served late, it verifies and continues.
3. Never served: **Run workflow → main**, `retry_publication` on, `first_publication` off. The run downloads `course-pages-<run>-<attempt>`, verifies it against the record and redeploys. No imports; older code may return with it.
4. Confirm summary **verified**. Run an ordinary refresh.

Artifact expired (30 days): restore the exact candidate from a preserved copy under supervision.

**WARNING**: Never delete deployment records, rerun bootstrap or deploy an offline preview to clear a pending record.

V1 retry refuses a recorded V2 release.

### 12.2 V2

- Pending or unverified release: next run selects `retry` or `repair`. No manual action.
- Seed: dispatch `seed_archive` only after protected refs exist (§14, item 6), with archive publishing disabled. If V1 advances, dispatch `reconcile_seed` (not `seed_archive`) before cutover. It creates a normal child, verifies current V1 again in the writer, and retains disappeared snapshots hidden. A failed/unverified latest V1 attempt must be recovered first. Cutover still rejects a stale seed. Seed/reconciliation inputs cannot be combined or used after archive mode is enabled.
- Shadow or recovery build:

  ```sh
  python3 scripts/build_site.py --archive-snapshot <verified-folder> --archive-commit <full-sha>
  ```

  Uses code and registry from the current checkout; check out the recorded `main` commit for exact reproduction. No import, no Pages read, no publication. Does not prove the folder's origin.

### 12.3 Original V1 recovery artifact and shadow comparison

Retain the original complete Pages artifact for the currently verified V1 release and its matching code commit/tree. A build from newer editor code is not that artifact. The following local commands verify the full artifact digest, manifest and every file, then preserve a self-contained recovery bundle:

```sh
python3 scripts/publication_recovery.py capture-v1 --site <original-v1-site> --release <verified-v1-release.json> --out <new-capture-directory>
python3 scripts/publication_recovery.py verify-capture --capture <capture-directory> --expected-release <verified-v1-release.json>
python3 scripts/publication_recovery.py compare-seed --capture <capture-directory> --archive-snapshot <verified-seed-directory> --archive-commit <full-seed-sha>
```

`compare-seed` checks the visible inventory and every included byte, enumerates intended archive metadata/schema additions and exits nonzero on mismatch. It does not demand equal whole-site hashes across the renderer/schema change. Capture verifies consistency with the supplied release; obtain verified/live release authority independently through deployment records and served checks. Retain the capture across Actions artifact expiry.

Stage a rollback locally with the current V2 archive head recorded:

```sh
python3 scripts/publication_recovery.py stage-rollback --capture <capture-directory> --expected-release <verified-v1-release.json> --archive-commit <current-student-work-sha> --out <new-rollback-directory>
```

The staged `site/` is byte-identical to the original V1 artifact. `rollback-plan.json` records its code/release pins and archive head to preserve. Staging does not deploy or change refs. The reviewed rollback deployment must pause ordinary publishing, verify that archive head before/after, deploy these exact V1 bytes with their matching V1 recovery code/manifest, record the visible rollback point, and prove served verification. Do not feed manifest v2 to a V1 decoder. Deployment workflow/rehearsal is still pending; restoring only old `main` is insufficient.

### 12.4 Explicit identity rebind

Recover pending publication first. Read the exact current archive commit and old group identity. Prepare using the proposed trusted registry, which changes only the intended group identity/settings (unrelated state changes may be included):

```sh
python3 scripts/archive_rebind.py prepare --archive-snapshot <verified-parent-directory> --archive-commit <full-parent-sha> --registry <proposed-groups.json> --slug <group> --old-repository <old-owner/repository> --old-branch <old-branch> --out <new-rebind-directory>
```

Review `candidate/` and `rebind.json`. Include the exact spec as `.github/archive-rebind.json` alongside the proposed registry in a reviewed course-code change. The spec pins the archive parent, old/new identities and deterministic retired slug. With V2 enabled and writes authorized, dispatch `rebind_archive` alone. Both prepare and writer derive the whole expected archive independently from the pinned parent, reviewed spec and registry; retained bytes cannot be replaced. No source read or Pages deployment occurs. Run a normal refresh afterward to accept/publish the new repository; remove the one-time spec in the follow-up review. If the parent advances first, regenerate and review the spec rather than changing its SHA by hand.

### 12.5 Freeze and offline reproduction

Set intended visible groups to `frozen` through the reviewed registry change; leave hidden groups hidden. Publish and verify that frozen release first. In a clean checkout of its exact code commit/tree:

```sh
python3 scripts/archive_freeze.py prepare --release <verified-v2-release.json> --archive-snapshot <verified-archive-directory> --archive-commit <full-archive-sha> --main-commit <full-main-sha> --main-tree <full-main-tree-sha> --out <new-freeze-record.json>
python3 scripts/archive_freeze.py reproduce --record <freeze-record.json> --archive-snapshot <verified-archive-directory> --archive-commit <full-archive-sha> --main-commit <full-main-sha> --main-tree <full-main-tree-sha>
python3 scripts/archive_freeze.py create-local-tag --repository <isolated-archive.git> --record <freeze-record.json>
python3 scripts/archive_freeze.py verify-local-tag --repository <isolated-archive.git>
```

The annotated `final-2026` tag identifies the exact archive commit and the matching main commit/tree, both manifest digests and site digest. Local creation refuses an existing tag; reproduction reads no student repositories, Pages or media caches. Tag publication/protection requires the reviewed authorized release procedure and live acceptance. The tag covers included archived bytes, not excluded originals or external services.

### 12.6 Unpublishing and history requests

For ordinary unpublishing, set the group to `hidden` through review, publish and verify both its absent entry and absent served content paths. The public archive still retains its bytes.

For a requested history purge, first stop publication and archive writes using their maintainer switches. Inventory the affected paths/blob identities across `student-work`, all freeze tags, Actions artifacts, preserved recovery bundles and the served site. Read-only `git rev-list --objects` and the Actions artifact inventory establish the copies under repository control. Prepare the exact refs/artifacts to change, preserved unaffected release pairs, rollback copies and re-verification steps for specific approval. After an authorized rewrite, rebuild only from the reviewed remaining state, verify the new archive and served inventories, and record which copies were removed. No history rewrite or deletion is automatic. Public clones/caches and excluded originals remain outside that operation.

Remaining live work: actual V1 rollback deployment, protected freeze-tag publication, rebind rehearsal and specifically authorized history operations.

## 13. Verification

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_annotations.js scripts/test_playback.js scripts/test_editor.js
python3 scripts/build_site.py
actionlint .github/workflows/pages.yml .github/workflows/validate.yml
git diff --check
```

- Browser: `scripts/check_editor_browser.js`, `check_editor_fallback.js`, `check_editor_fidelity.js`, in Chrome and WebKit. Rebuild `_site` first.
- Migration rehearsal: `python3 -m unittest discover -s scripts -p 'test_pilot_migration.py'`. Simulated transports only.
- CI: `validate.yml` runs unit, Node and offline build on PRs. Publication reruns tests before import.

Last local results (2026-10-14):

| Check | Result |
| --- | --- |
| Python | 185 passed in 126.607 s for the release. `/tmp/ddd-v2-release-python.log`. |
| Node | 34 passed. |
| Chrome fidelity | Inconsistent: one exact pass at 1280/390 px; other runs differ at 23 native-audio-edge pixels at 1280. Equal computed layout. Foreground delay did not fix it and was reverted. No tolerance or masking added. |
| WebKit fidelity | Current script passed exact layout/pixels at 1280 and 390 px. `/tmp/ddd-v2-webkit-fidelity.log`. |
| Browser lifecycle/storage | Chrome and WebKit passed reload with media, failed-save switch, repeated renders, 390 px overflow, CAS, quota rollback and shared blobs. `/tmp/ddd-v2-{chrome,webkit}-browser.log`. |
| Denied storage | Chrome and WebKit passed text fallback, quota retry, coexisting draft preservation, explicit migration and missing-attachment discard. `/tmp/ddd-v2-{chrome,webkit}-fallback.log`. |
| Build/workflow/whitespace | Offline build, actionlint 1.7.7 and `git diff --check` passed. |

Limits of evidence: fixtures and local bare repositories. Unproven: Actions identity and permissions, repository protections, Pages and issue behavior, Safari media, keyboard/touch on devices, cohort timing and bandwidth.

## 14. Open work

Dependency order:

1. Review controller and workflow security against real Actions semantics: effective permissions, `main` pin, rerun artifact admission, concurrency, answers.
2. Seed reuse is implemented: private temporary cache outside candidate authority, hash-checked reads, cleanup on success/failure and aggregate seed Pages metrics. Tests prove one download per distinct blob and corrupt-cache refetching.
3. Controller API metrics now accumulate per command through sealed reports/build state, including prepare, accept, build, record, authorize and verify. Tests use fresh clients and check repeated checkpoints/nested authorization. Remaining: `archive_transfer_bytes` measures `.git/objects`, a proxy; measure near-limit cohorts, history growth, low quota, global deadline, writer transfer costs and cheaper idle exit.
4. Migration: stale-seed reconciliation, V1 capture, shadow comparison, cutover. V1 rollback preserving V2 archive. Freeze tag and offline reproduction. Takedown procedure. Rebind migration.
5. Chrome fidelity. Lifecycle, security, inventory and capacity matrix. Safari media. Keyboard and touch. Deployment races.
6. Recheck live baseline read-only. Propose `main`, `student-work` and tag protections. Prove Actions actor policy in a disposable pilot before enabling writes.
7. Two ordinary student-account pilots: edit, upload, delete, request. Source-loss, offline and rollback rehearsals. Enable Issues, label, UI policy and switches only on authorization. Hourly schedule only after pilot acceptance.
8. Reconcile documentation; final checks; release and settings proposal.

Carried from V1: required `validate` check; access cutover (remove student grants on the course repository after pilot); student-account checks; live failure recovery and rollback.

## 15. Record

| Date | Event | Evidence |
| --- | --- | --- |
| 2026-10-12 | Inventory: `example` and `dust` served from `027c2e3`. 24 source, 20 served, 2 unmerged files saved with digests. | `.scratch/pilot-06/2026-10-12/` |
| 2026-10-12 | `main` ruleset: 1 approval, stale dismissal, maintainer bypass, no required check. Organization base Read; 17 Read, 4 Maintain, 3 Admin grants; 1 team Maintain. Ruleset since removed. | — |
| 2026-10-13 | [`2026-ddd-pilot-alpha`](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-pilot-alpha), [`2026-ddd-pilot-beta`](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-pilot-beta) created: public, `main`, template. No students invited. | — |
| 2026-10-13 | Repository pilot, 6 stages passed: import, isolated bad SVG, correction, missing-text retention, removal, restoration. | `.scratch/pilot-06/2026-10-13/repository-pilot-results.json` |
| 2026-10-13 | Dust migrated to alpha `3fca3f8`: 13 files; media renamed lowercase; PNG embedded in SVG container. | `…/2026-10-13/dust-migration/` |
| 2026-10-13 | PR #12 merged (maintainer bypass). Bootstrap run `38375728267` verified `a18de13`; refresh `38375997452` `unchanged`; 16 files matched. | `…/2026-10-13/deployed-verification.json` |
| 2026-10-13 | PR #13 (tickets 07–09): run `38412487212` verified `7ca73bd`; 31 files matched. Bypass authorized by maintainer. | `…/2026-10-13/release-07-09-evidence/` |
| 2026-10-13 | PRs #14–#16: continuous prose, inline bibliography, uniform leading, `ddd-abstract` without visual. | Git history |
| 2026-10-14 | PRs #17–#18, `9190958`: notes open, video buttons removed, publication reduced to `example` and `ddd-abstract`. Served by run `38525824509`. | Git history |
| 2026-10-14 | Issues disabled. `main` ruleset removed (maintainer-confirmed); `main` unprotected. V2 checkpoint on `v2`; paused. | Handoff |

`.scratch/` is local and Git-ignored, not a backup. Copy evidence to maintainer-controlled storage before cutover.
