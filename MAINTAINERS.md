# Maintainer manual

Scope: course site, registry, import, publication, editor, archive. Revision: 2026-10-15.

Use this manual for current operating instructions. [Acceptance checks](docs/2026-10-15_ddd_acceptance-checks.md) track unfinished validation; [publication evidence](docs/2026-10-15_ddd_publication-evidence.md) holds dated deployments, test results and legacy V1 reference procedures. Update operating instructions when behavior changes, acceptance checks when validation completes, and the evidence record when preserving an observation.

**WARNING**: irreversible loss of student content or published state.
**CAUTION**: stalled publication or wrong release.

## 1. Operating baseline

V2 is the production publication path following the recorded 2026-10-14 archive cutover. V1 remains a legacy path for pre-cutover reference and reviewed recovery. The [cutover record](docs/2026-10-15_ddd_publication-evidence.md#v2-cutover-on-2026-10-14) preserves the deployment and archive identities; recheck live state before an operation that depends on it.

The working registry (`groups.json`) has live remote groups `materialities-of-ai` and `ddd-abstract`, and hidden pilots `dust` and `pilot-beta`; `example` is local. Registry intent is not proof of what Pages currently serves. The editor request UI is disabled in `.github/publishing.json`. Live Issues, Actions variables and rulesets must be checked independently.

Routine publication uses **Publish course website** on `main`, with dispatch inputs left off. Keep archive mode and archive writes enabled for V2; use the pause switch for reviewed recovery. The ten-minute schedule remains in `pages.yml` until request-pilot acceptance. Hosted recovery, ordinary student-account and device checks remain on the [acceptance checklist](docs/2026-10-15_ddd_acceptance-checks.md).

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

| Datum | V2 production authority |
| --- | --- |
| Code, registry | `main` |
| New student content | Group repository at one exact commit |
| Last-good content | `student-work` |
| Expected release | Deployment record, task `course-publication`, environment `course-publication-check`; `Main-Commit:` trailer on archive commit |
| Exact retry | Reconstruction from the pinned `main` + `student-work` pair |

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
| `.github/workflows/recovery.yml`, `scripts/publication_rollback.py` | Reviewed capture, V1 rollback and V2 resumption. |
| `scripts/multimodal_source.py` | Source adapter for the opening brief. |
| `.github/ISSUE_TEMPLATE/` | Request form; blank issues off. |
| `.github/publishing.json` | Editor request policy. |
| `templates/group/` | Canonical student guide and demonstration files. |
| `scripts/sync_course_materials.py` | Check or regenerate root guide and local demonstration copies. |

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

Adapted source: the opening brief, **Materialities of AI**, imports from `ginevraterenghi/multimodal-abstract-ddd` under slug `materialities-of-ai`. `scripts/multimodal_source.py` adapts that repository's `article#abstract` in `index.html` and original media links in `sources.json` into `text.md`; media bytes remain unchanged. The exception is allowlisted only in `scripts/multimodal_source.py`; that module owns repository selection, source-file bounds, pinned downloads, blob verification and conversion. The importer calls its `prepare` boundary and then applies normal content validation; the editor uses its repository predicate. Other HTML repositories still require `text.md`. Source files are verified against one exact Git commit; remote scripts and styles are omitted. Future imports refresh the brief automatically. Edit the brief in its source repository; the Markdown editor is disabled for this adapted entry. A future source `text.md` takes precedence over adaptation. Keep `require_visual: false` because this brief has inline media and no main SVG.

## 5. Content contract

Source: `static/contract.json` (v2). Python constants are adapters.

| Class | Paths | Limit |
| --- | --- | --- |
| Text | Root `text.md`, UTF-8, title/authors front matter. Required. | 256 KiB |
| Media table | Root `media.csv`, UTF-8 CSV, first line `name,source,file`; comma or semicolon. Required at import admission. | 64 KiB |
| Main visual | Root `visual.svg`; `viewBox="0 0 1920 1080"`; optional width/height = 1920/1080 px. Other root `visual.*` rejects the entry. | 5 MiB |
| Image | `.svg .png .jpg .jpeg .webp .gif .avif` | SVG 5 MiB; others 20 MiB |
| Audio | `.mp3 .m4a .wav .ogg .aac .flac` | 20 MiB |
| Video | `.mp4 .webm .mov` | 20 MiB |
| Group | ≤ 100 selected files, `media.csv` included | 50 MiB admission; 100 MiB legacy V1 recovery |
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

Media table (`static/media-table.js`, shared by renderer, editor and importer): `[[name]]` expands to `[[name|file|source=url]]` before any other parsing. Names are unique case-insensitively. Every media file `text.md` uses, by name or path, needs a row; rows for files not yet uploaded pass, so media → `media.csv` → `text.md` commits stay valid one by one. Empty source passes; the editor warns. A row's source also links path references and Markdown images.

`media.csv` is required only by `import_group` (`validate_content(require_table=True)`). Archive verification and served-snapshot recovery revalidate older snapshots without it; when present it is checked. Unchanged groups keep their snapshot until the next commit. The adapted brief (`multimodal_source`) is exempt. Contract version stays 2: older snapshots remain valid, and a version change would invalidate archived v2 snapshots.

Rejected update: previous snapshot retained. No previous snapshot: group skipped. Audio/video codecs are not validated; browser check required.

## 6. Renderer

Invariants:

- `text.md` is data. Contributor HTML is escaped. Unsafe link and media schemes are rejected.
- SVG loads only through `<img>`.
- Marked is vendored; `static/vendor/LICENSE.marked.md` ships with the site.

| Element | Behavior |
| --- | --- |
| Annotation | `[[label\|target\|type=…;style=…;color=…]]`. Keys in any order. Duplicate or unknown key, or bad value: literal text. Two-field legacy form valid. |
| Note | Expanded by default. Marker and content in braces. Normal sans serif; `style=italic` explicitly adds italic. |
| Bibliography | Inline serif `(■ Author, year. Title)`. Black; overrides surrounding and modifier colors. Text targets expand by default; URL targets make the entire citation a source link, underlined on hover, opening in a new tab. |
| Quote | Native quotation marks on reveal. |
| Inline image | Thumbnail. Hover/focus preview anchored to the thumbnail, opaque, fitted to viewport. Click/tap/Enter opens the original source in a new tab. |
| Inline video | No buttons. Muted loop when half-visible, page visible, lightbox closed. Hover enlarges with sound; keyboard focus enlarges muted. Click opens the original source in a new tab. Reduced motion: still; browser playback policy applies. |
| Inline audio | Static player. Click opens the original source in a new tab; no hover playback. |
| External embed | Manual. Loads on reveal; unloads on inactivity; provider link shown. |
| Dismissal | Escape, outside activation, scroll, resize, navigation, focus loss, page hide. |
| Prose | Newlines and blank lines between adjacent paragraphs become spaces. Headings, lists, quotes, code stay separate. Responsive 24–32 px type (`--prose-size: clamp(24px, 2.2vw, 32px)`), fixed 52 px leading (`--prose-leading`). Source: `static/main.css`. |
| Main visual | Image element; lightbox. Escape closes lightbox before playback refresh. |

The student guide teaches one form per idea. These forms remain supported but are no longer taught there: media by path (`[[label|media/x.svg]]`, `![alt](media/x.svg)`, `[![alt](media/x.svg)](https://…)`); the `source=` modifier, whose URL must encode `;`, `|`, `[` and `]`, overriding the `media.csv` source; emoji markers in the third field. An explicit `color` overrides a surrounding `{color|…}` span; `{color|✶ text}` renders in braces; Markdown `*italics*` works in labels. Path references still need a `media.csv` row.

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

Meaning of each group status, for answering students. The student guide sends them to the site itself and to the course team.

| Status | Meaning |
| --- | --- |
| `updated` | New version selected for this run. Published only once the run is verified. |
| `unchanged` | Same version as before. |
| `retained` | Update rejected; previous version kept. The report's `error` gives the reason. |
| `skipped` | No valid published version yet. |
| `disabled`, `hidden`, `frozen` | Publication turned off or fixed by the course team. |

Requirements: Python 3.9+, Node. No packages. Optional `GITHUB_TOKEN` raises API quota; never sent to raw or Pages hosts.

## 9. Publication: V2 production

Workflow **Publish course website** (`.github/workflows/pages.yml`). Pushes to `main`, the schedule `7,17,27,37,47,57 * * * *`, and **Run workflow → main** with inputs off refresh the site. Issue-opened events are admitted only when requests are enabled and eligible. Publication uses concurrency group `pages`, with `cancel-in-progress: false`.

GitHub may delay or drop scheduled runs. If schedules stop after 60 days without repository activity, use **Actions → Publish course website → Enable workflow**, then **Run workflow → main**.

Do not use the legacy `first_publication` or `retry_publication` inputs for V2. V2 recovers the recorded code/archive pair through ordinary refresh. Seed and reconciliation are pre-cutover operations, preserved in [V1 reference procedures](docs/2026-10-15_ddd_publication-evidence.md#v1-reference-procedures).

### 9.1 Switches

| Switch | Effect when set |
| --- | --- |
| Variable `DDD_ARCHIVE_ENABLED=true` | V2 jobs replace the V1 job. |
| Variable `DDD_ARCHIVE_WRITES_ENABLED=true` | Job `archive` may push `student-work`. |
| Variable `DDD_REQUESTS_ENABLED=true` | Issue requests admitted; job `answer` runs. Stop switch for requests. |
| Variable `DDD_PUBLICATION_PAUSED=true` | Stops ordinary V1/V2 publication entry jobs and controller mutations/answers. Required by the dedicated rollback controller; changing this variable remains an operator action. |
| `.github/publishing.json` `requests_enabled` | Editor shows the request step. UI only. |
| Dispatch input `rebind_archive` | Apply the exact reviewed `.github/archive-rebind.json` plan after V2 cutover. No import or deployment in that dispatch. |

**CAUTION**: Disabling the issue form does not block API-created issues. Use `DDD_REQUESTS_ENABLED`.

### 9.2 Jobs

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

### 9.3 Modes

| Mode | Condition | Action |
| --- | --- | --- |
| `rebind` | Explicit dispatch, reviewed exact-parent plan, verified V2 | Preserves old identity/content under a deterministic hidden retired slug; new active identity starts without a fabricated snapshot. No deployment or answer. |
| `refresh` | Eligible event | Import live groups → accept → build → deploy → verify. |
| `unchanged` | Archive, `main` and site digest equal verified release | No write, no deployment. |
| `pending` | Accepted archive ahead of release, same `main` | Build accepted pair; no import. |
| `retry` | Latest attempt unverified, same code | Reconstruct pair from refs; redeploy. |
| `repair` | Latest attempt already served | Record verification; no deployment. |
| `deferred` | Requests off, issue ineligible, cooldown, or quota ≤ 200 | No work. Requests stay open. |
| `invalid` | Malformed Group field | No import. Answer; close. |
| `superseded` | `main` moved | Stop. No answer. |

### 9.4 Archive

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
- Routine slug rebinds are rejected. An explicit reviewed migration preserves the previous identity and snapshot under a hidden `retired-…` slug, then installs the new registry identity without a snapshot. The subsequent normal publication imports that repository. Use the [identity-rebind acceptance check](docs/2026-10-15_ddd_acceptance-checks.md#hosted-recovery-and-archive-operations) for hosted validation.
- Unreachable source: last accepted snapshot kept.

### 9.5 Release

Identity v2: `main` commit, archive commit, tree, manifest and site digests. Site digest excludes `release.json`.

- Archive write fails: no deployment.
- Deployment fails: archive kept; next run retries the pair before any new import.
- `authorize` rechecks `main` and latest attempt before the Pages mutation.
- Newer archive or release authority blocks rollback.
- Verification: release, manifest, one changed-text probe, one changed-media probe. Sampling, not a full hash of served bytes.
- API budgets: deployment transport 40 calls per client; issue transport 60. Ambiguous writes never retried automatically.

### 9.6 Requests

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

## 10. Groups and access

### 10.1 Roster

Keep outside this repository, maintainer-controlled. Per group: name, slug, repository, verified GitHub usernames, invitation state, effective access, pilot result. A display name is insufficient; students confirm the exact account. `groups.json` holds configuration only.

### 10.2 Provisioning

1. Confirm owner and username mapping.
2. Create one public repository, default branch `main`, from the template repository [`2026-ddd-pilot-beta`](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-pilot-beta) (**Use this template**), or copy the **contents** of `templates/group/` to its root. No Pages, secrets or workflows. Personal repositories accepted; private rejected.
3. Owner adds teammates with **Write** (**Settings → Collaborators and teams**). Track acceptance; an invitation is not access.
4. Audit effective permissions: organization base permission, team grants including parent teams, direct grants, custom roles. Organization-wide Write, Maintain or Admin breaks isolation. A lower direct grant does not cancel a higher inherited one. Changing organization defaults affects other projects; assess first. Record results.
5. Check rulesets on the group's `main`: direct commits, no mandatory PR or approval.
6. Register as `hidden` through a reviewed PR; `python3 scripts/build_site.py` must pass. Switch to `live` by reviewed PR when ready.
7. Hand over: repository link, slug, site link, editor link, [publication results](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-making-sense-of-data/actions/workflows/pages.yml). Confirm an ordinary account can read a run's **Course publication** summary.

**WARNING**: A `live` group still holding template content publishes it.

`templates/group/` is canonical. Edit its README for student instructions and its content/media for demonstrations, then run:

```sh
python3 scripts/sync_course_materials.py --write
python3 scripts/sync_course_materials.py
```

The script generates the marked student-guide section of the root README and all files in `example/`. Only the example title/authors differ; prose, visual, media table and media come from the template. The root introduction and repository layout remain separately maintained. The editor template is already generated directly from `templates/group/` at build time. CI rejects stale checked-in copies. Unexpected example files require explicit review/removal; synchronization does not delete them.

The remote template repository is a distribution copy of `templates/group/`, never an upstream editing source. After an authorized template update, compare its full file inventory and bytes with the canonical folder; do not copy remote edits back automatically. It stays registered as the `hidden` pilot `pilot-beta`, which is never imported. Local generation and CI do not synchronize or verify that remote repository.

Complete the [ordinary student-account acceptance checks](docs/2026-10-15_ddd_acceptance-checks.md#ordinary-student-accounts-and-request-rollout) before access cutover.

### 10.3 Changes

- Rename, transfer or branch change: keep the public slug and use the explicit rebind procedure below. A routine registry edit alone is rejected by V2.
- Membership: verify username; invite to the correct repository only. Review direct and inherited grants of departing members. Removing access does not unpublish.
- Unpublish: set `hidden` by reviewed PR; confirm the next verified release omits the entry. Never delete `text.md` or `visual.svg` to unpublish: an incomplete update retains the previous snapshot.
- Failed import: students correct content from the run summary; maintainers correct address, branch, quota and publication faults.

### 10.4 Course repository

Review every PR to `main`, including the full **Files changed** list. The required V2 protection baseline is a PR and the GitHub Actions `validate` check, with an up-to-date branch and resolved review threads. The recorded configuration has zero required approvals and no bypass actors; recheck effective settings before relying on it. `main` and `student-work` reject deletion and non-fast-forward updates. The `github-pages` environment is restricted to `main`. PR validation is offline, read-only, fixture-based.

## 11. Recovery

### 11.1 Ordinary V2 recovery

- Pending or unverified release: next run selects `retry` or `repair`. No manual action.
- Shadow or recovery build:

  ```sh
  python3 scripts/build_site.py --archive-snapshot <verified-folder> --archive-commit <full-sha>
  ```

  Uses code and registry from the current checkout; check out the recorded `main` commit for exact reproduction. No import, no Pages read, no publication. Does not prove the folder's origin.

### 11.2 Original V1 recovery artifact and shadow comparison

Retain the original complete Pages artifact for the verified pre-cutover V1 release and its matching code commit/tree. A build from newer editor code is not that artifact. The following local commands verify the full artifact digest, manifest and every file, then preserve a self-contained recovery bundle:

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

The staged `site/` is byte-identical to the original V1 artifact. `rollback-plan.json` records its code/release pins and archive head to preserve. Staging does not deploy or change refs. The reviewed rollback deployment must pause ordinary publishing, verify that archive head before/after, deploy these exact V1 bytes with their matching V1 recovery code/manifest, record the visible rollback point, and prove served verification. Do not feed manifest v2 to a V1 decoder. Restoring only old `main` is insufficient.

`scripts/publication_rollback.py` supplies the dedicated prepare → record → authorize → verify controller. It requires `DDD_PUBLICATION_PAUSED=true`, trusted current code/run identity, a reviewed plan binding the exact V1 release/capture digest and V2 deployment/archive head, and a matching staged site. Its intent/status records use `course-publication-rollback` / `course-publication-rollback-point`, leaving ordinary V2 authority intact. Full verification checks every captured site file and rechecks V2 authority before success. Missing/conflicting intents and pre/post-deployment drift fail closed; an exact same-attempt intent can be recovered after an ambiguous create response. Hosted recovery acceptance is tracked in the [acceptance checklist](docs/2026-10-15_ddd_acceptance-checks.md#hosted-recovery-and-archive-operations). Do not clear the pause as a substitute for that reviewed resumption.

The recovery workflow is `.github/workflows/recovery.yml`, with explicit `capture`, `rollback` and `resume` dispatch modes. It uses the same `pages` queue, the protected `github-pages` environment and read-only contents permission; it never updates `student-work`. Ordinary paused workflow events use separate concurrency groups so they cannot displace pending recovery. Its fixture test step clears the pause only for unit tests; recovery commands still require the actual pause.

Before using it, enable the operator pause and wait for any already running publication to finish. Review `.github/archive-rollback.json` containing exactly `expected_archive_commit`, `expected_v2_deployment_id`, `expected_v2_release`, `expected_v1_release` and `capture_sha256` (SHA-256 of the complete bundle's raw `capture.json`). Review `.github/archive-rollback-source.json` containing `version: 1`, `kind: pages` or `capture`, and string `run_id` / `run_attempt`. Artifact names are derived, never wildcard-selected: `course-pages-<run>-<attempt>` for the original V1 artifact or `v1-recovery-<run>-<attempt>` for a previously verified capture. Original Pages locators must match the reviewed V1 release run/attempt. Download is restricted to the course repository; bounded tar intake and capture hashes establish complete byte identity. Merge these reviewed files while paused so an automatic main publication cannot supersede the reviewed V2 point.

`capture` retains the verified bundle as `v1-recovery-<current-run>-<attempt>` for up to 90 days without Pages deployment. Keep a separate maintainer-controlled copy beyond artifact retention; a workflow artifact is not the only recovery copy. `rollback` stages the exact captured bytes, records its separate intent, authorizes the preserved archive/main/V2 point, deploys and verifies every captured file. The pause remains enabled afterward.

For explicit V2 resumption, review `.github/archive-resume.json` with exactly `version: 1`, `operation: resume-v2-after-v1-rollback`, `rollback_deployment_id`, `rollback_payload_sha256`, `expected_archive_commit`, `expected_v2_deployment_id` and `expected_v2_release_sha256`. Payload hashes use the canonical JSON encoder in `publish_site.encode`. The current trusted code commit/tree are derived from the reviewed checkout and bound into state, rather than written into a self-referential main file. `resume` reads the pinned archive through Git, rebuilds the original pair when code matches or explicitly supersedes it with current reviewed code, records/authorizes a regular V2 release and verifies it. It imports no source repositories and writes no archive ref. An exact pending pair can be reconstructed after failure without the original Actions artifact. Clear the pause only after successful served V2 verification. Complete the hosted recovery acceptance check before relying on this procedure as rehearsed recovery.

Resume retries bind the exact regular deployment predecessor. A changed-code retry may supersede that pinned unverified attempt while preserving the reviewed original authority. If the matching resumed V2 release already has successful verification, prepare reconstructs it and rechecks served probes and authorities; the workflow reports completion and skips another deployment. Include hosted retry behavior in the recovery acceptance check.

### 11.3 Explicit identity rebind

Recover pending publication first. Read the exact current archive commit and old group identity. Prepare using the proposed trusted registry, which changes only the intended group identity/settings (unrelated state changes may be included):

```sh
python3 scripts/archive_rebind.py prepare --archive-snapshot <verified-parent-directory> --archive-commit <full-parent-sha> --registry <proposed-groups.json> --slug <group> --old-repository <old-owner/repository> --old-branch <old-branch> --out <new-rebind-directory>
```

Review `candidate/` and `rebind.json`. Include the exact spec as `.github/archive-rebind.json` alongside the proposed registry in a reviewed course-code change. The spec pins the archive parent, old/new identities and deterministic retired slug. With V2 enabled and writes authorized, dispatch `rebind_archive` alone. Both prepare and writer derive the whole expected archive independently from the pinned parent, reviewed spec and registry; retained bytes cannot be replaced. No source read or Pages deployment occurs. Run a normal refresh afterward to accept/publish the new repository; remove the one-time spec in the follow-up review. If the parent advances first, regenerate and review the spec rather than changing its SHA by hand.

### 11.4 Freeze and offline reproduction

Set intended visible groups to `frozen` through the reviewed registry change; leave hidden groups hidden. Publish and verify that frozen release first. In a clean checkout of its exact code commit/tree:

```sh
python3 scripts/archive_freeze.py prepare --release <verified-v2-release.json> --repository <isolated-archive.git> --archive-commit <full-archive-sha> --main-commit <full-main-sha> --main-tree <full-main-tree-sha> --out <new-freeze-record.json>
python3 scripts/archive_freeze.py reproduce --record <freeze-record.json> --repository <isolated-archive.git> --archive-commit <full-archive-sha> --main-commit <full-main-sha> --main-tree <full-main-tree-sha>
python3 scripts/archive_freeze.py create-local-tag --repository <isolated-archive.git> --record <freeze-record.json> --main-commit <full-main-sha> --main-tree <full-main-tree-sha>
python3 scripts/archive_freeze.py verify-local-tag --repository <isolated-archive.git> --main-commit <full-main-sha> --main-tree <full-main-tree-sha>
```

The annotated `final-2026` tag identifies the exact archive commit and the matching main commit/tree, both manifest digests and site digest. Every command checks the clean code checkout; archive bytes are materialized from the pinned local Git commit. Tag verification reproduces the full release and rejects altered annotation digests/code pins. Local creation refuses an existing tag; reproduction reads no student repositories, Pages or media caches. Tag publication/protection requires the reviewed authorized release procedure and live acceptance. The tag covers included archived bytes, not excluded originals or external services.

### 11.5 Unpublishing and history requests

For ordinary unpublishing, set the group to `hidden` through review, publish and verify both its absent entry and absent served content paths. The public archive still retains its bytes.

For a requested history purge, first stop publication and archive writes using their maintainer switches. Inventory the affected paths/blob identities across `student-work`, all freeze tags, Actions artifacts, preserved recovery bundles and the served site. Read-only `git rev-list --objects` and the Actions artifact inventory establish the copies under repository control. Prepare the exact refs/artifacts to change, preserved unaffected release pairs, rollback copies and re-verification steps for specific approval. After an authorized rewrite, rebuild only from the reviewed remaining state, verify the new archive and served inventories, and record which copies were removed. No history rewrite or deletion is automatic. Public clones/caches and excluded originals remain outside that operation.

## 12. Verification

Offline preview builds contain local entries only and must never be deployed over an imported publication. Build `_site` again before browser checks.

```sh
python3 scripts/sync_course_materials.py
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_annotations.js scripts/test_playback.js scripts/test_editor.js
python3 scripts/build_site.py
actionlint .github/workflows/pages.yml .github/workflows/validate.yml .github/workflows/recovery.yml
git diff --check
```

- Browser: `scripts/check_editor_browser.js`, `check_editor_fallback.js`, `check_editor_fidelity.js`, in Chrome and WebKit. Rebuild `_site` first.
- Migration rehearsal: `python3 -m unittest discover -s scripts -p 'test_pilot_migration.py'`. Simulated transports only.
- CI: `validate.yml` runs unit, Node and offline build on PRs. Publication reruns tests before import.

For outstanding hosted, browser and student-account validation, use [acceptance checks](docs/2026-10-15_ddd_acceptance-checks.md). Past test counts and deployment observations are retained in [publication evidence](docs/2026-10-15_ddd_publication-evidence.md); rerun checks for the revision being reviewed.
