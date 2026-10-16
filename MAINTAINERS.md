# Maintainer manual

Scope: course site, registry, import, publication, editor, archive. Revision: 2026-10-15.

Use this manual for current operating instructions. [Acceptance checks](docs/2026-10-15_ddd_acceptance-checks.md) track unfinished validation; [publication evidence](docs/2026-10-15_ddd_publication-evidence.md) holds dated deployments and test results. Update operating instructions when behavior changes, acceptance checks when validation completes, and the evidence record when preserving an observation.

**WARNING**: irreversible loss of student content or published state.
**CAUTION**: stalled publication or wrong release.

## 1. Operating baseline

Publication goes through the archive (§9.4). On a fresh repository the first run creates `student-work` and the first release (§9.4). Recheck live state before an operation that depends on it.

The working registry (`groups.json`) has live remote groups `materialities-of-ai` and `ddd-abstract`, and hidden pilots `dust` and `pilot-beta`; `example` is local. Registry intent is not proof of what Pages currently serves. The editor request UI is disabled in `.github/publishing.json`. Live Issues, Actions variables and rulesets must be checked independently.

Routine publication uses **Publish course website** on `main`; **Run workflow** takes no inputs. Use the pause switch for maintenance. The ten-minute schedule remains in `pages.yml` until request-pilot acceptance. Hosted recovery, ordinary student-account and device checks remain on the [acceptance checklist](docs/2026-10-15_ddd_acceptance-checks.md).

## 2. Terms

| Term | Meaning |
| --- | --- |
| Group | Student team; owns one public repository. |
| Slug | Permanent group identifier: entry folder, URL fragment, archive path. |
| Registry | `groups.json`. Sole list of groups. |
| Source | Group repository at one exact commit. |
| Snapshot | Complete selected file set of one group, with sizes and digests. |
| Candidate | Built `_site`, not yet served. |
| Release | Identity of one built site: code commit, archive commit, manifest and site digests. |
| Archive | Orphan branch `student-work` in the course repository. Durable copy of accepted snapshots. |
| Accepted | Candidate committed to `student-work`. |
| Attempted | Deployment record names the release. |
| Verified | Served site matches the release. The only state meaning "published". |
| Request | Issue labelled `publication-request`, asking for a refresh. |

## 3. Architecture

### 3.1 Authority

| Datum | Authority |
| --- | --- |
| Code, registry | `main` |
| New student content | Group repository at one exact commit |
| Last-good content | `student-work` |
| Expected release | Deployment record, task `course-publication`, environment `course-publication-check` |
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
| `scripts/release.py`, `deployment_records.py` | Release identity, served manifest and served verification; deployment records. |
| `scripts/publish.py` | Controller: `admit prepare accept build record authorize verify finalize answer`. |
| `scripts/archive_state.py`, `archive_transaction.py`, `archive_git.py`, `archive_release.py` | Archive manifest; candidate transaction; Git transport and writer; release identity. |
| `scripts/publication_requests.py` | Request capture and answers. |
| `.github/workflows/pages.yml` | Publication jobs. |
| `.github/workflows/validate.yml` | PR gate. Offline. |
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
| `state` | Required: `live`, `frozen` or `hidden`. |
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
| Group | ≤ 100 selected files, `media.csv` included | 50 MiB |
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

`media.csv` is required only by `import_group` (`validate_content(require_table=True)`); archive verification checks it when present. The adapted brief (`multimodal_source`) is exempt.

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

Early exit: all inputs equal the verified release → `unchanged`, without rebuilding the site.

Report: `report.json` in each run's job artifacts (§9.2).

| Field | Values |
| --- | --- |
| `status` | `imported`, `unchanged`, `failed`, `disabled` (not `live`) |
| `publication_status` | `updated`, `unchanged`, `retained`, `skipped`, `hidden`, `frozen` |
| `selected_source` | Repository, branch, commit actually published; may differ from attempt. |

Meaning of each group status, for answering students. The student guide sends them to the site itself and to the course team.

| Status | Meaning |
| --- | --- |
| `updated` | New version selected for this run. Published only once the run is verified. |
| `unchanged` | Same version as before. |
| `retained` | Update rejected; previous version kept. The report's `error` gives the reason. |
| `skipped` | No valid published version yet. |
| `hidden`, `frozen` | Publication turned off or fixed by the course team. |

Requirements: Python 3.9+, Node. No packages. Optional `GITHUB_TOKEN` raises API quota; never sent to raw or Pages hosts.

## 9. Publication

Workflow **Publish course website** (`.github/workflows/pages.yml`). Pushes to `main`, the schedule `7,17,27,37,47,57 * * * *`, and **Run workflow → main** refresh the site. Issue-opened events are admitted only when requests are enabled and eligible. Publication uses concurrency group `pages`, with `cancel-in-progress: false`.

GitHub may delay or drop scheduled runs. If schedules stop after 60 days without repository activity, use **Actions → Publish course website → Enable workflow**, then **Run workflow → main**.

An unverified release is retried from the recorded code/archive pair before any new import (§9.3). On a fresh repository the first run creates the archive (§9.4).

### 9.1 Switches

| Switch | Effect when set |
| --- | --- |
| Variable `DDD_REQUESTS_ENABLED=true` | Issue requests admitted; job `answer` runs. Stop switch for requests. |
| Variable `DDD_PUBLICATION_PAUSED=true` | Stops publication jobs and controller mutations/answers. Changing it is an operator action. |
| `.github/publishing.json` `requests_enabled` | Editor shows the request step. UI only. |

**CAUTION**: Disabling the issue form does not block API-created issues. Use `DDD_REQUESTS_ENABLED`.

### 9.2 Jobs

| Job | Permissions | Function |
| --- | --- | --- |
| `pin` | contents read, pages read | Pin latest `main` at execution. |
| `admit` | contents read, issues read, deployments write | Choose mode; capture request batch at cutoff; reserve cooldown. |
| `prepare` | contents read, deployments read | Run tests; read archive; import; emit candidate or retry pair. |
| `accept` | contents write, deployments read | Revalidate registry, parent and verified release; expected-parent fast-forward of `student-work`. |
| `publish` | contents read, deployments write, pages write, id-token write | Build, record, authorize, deploy, verify, finalize. |
| `answer` | contents read, issues write | One bot comment per request; close where final. |

All checkouts at the pinned commit, `persist-credentials: false`. Imported content never runs.

Artifacts: `{admission,prepared,accepted,result}-<run>-<attempt>`, `pages-<run>-<attempt>`; 7 days. The envelope binds kind, repository, main commit and tree, run, producing attempt, registry digest, file digests. Members: `metadata.json`, `batch.json`, `report.json`, optional `candidate.tar`. Extra members and links rejected. Reports ≤ 8 MiB; metadata ≤ 2 MiB. `EXPECTED_PRODUCER_ATTEMPT` admits an earlier successful attempt of the same run only. Artifacts transport; Git refs authorize.

### 9.3 Modes

| Mode | Condition | Action |
| --- | --- | --- |
| `refresh` | Eligible event; also the first run | Import live groups → accept → build → deploy → verify. |
| `unchanged` | Archive, `main` and site digest equal verified release | No write, no deployment. |
| `pending` | Accepted archive ahead of release, same `main` | Build accepted pair; no import. |
| `retry` | Latest attempt unverified, same code | Reconstruct pair from refs; redeploy. |
| `repair` | Latest attempt already served | Record verification; no deployment. |
| `deferred` | Requests off, issue ineligible, cooldown, or quota ≤ 200 | No work. Requests stay open. |
| `invalid` | Malformed Group field | No import. Answer; close. |
| `superseded` | `main` moved | Stop. No answer. |

### 9.4 Archive

- Orphan branch `student-work`. Layout `<slug>/…` plus manifest v2 `publication.json` (≤ 8 MiB).
- Manifest records per group: state, repository, branch, source commit and tree, contract version, registry settings, files (size, SHA-256, Git blob SHA), excluded paths and blob IDs when observed. Unknown IDs stay explicit unknowns.
- Commit only on content, provenance or state change. Expected parent; no force; sole writable ref `student-work`. Push credentials ephemeral, GitHub-only.
- First run: no `student-work` branch and no deployment record. The archive starts empty and the first accept writes a root commit, even for an empty registry, so every release names an archive commit. The writer requires the branch to be absent, so a concurrent first run cannot replace it. A deployment record without the branch stops the run. A branch without a record (a first run that failed after accepting) is published by the next ordinary refresh.
- Commit trailer `Main-Commit:` records the accepting code. Protection against branch deletion/non-fast-forward remains a required live invariant.
- Read: pinned shallow fetch into an isolated bare repository; `git ls-tree -r -z -l --full-tree` preflight (regular blobs, unique paths, exact inventory); then bounded expansion and hash/content validation. Transfer cap: 900 MiB + 8 MiB + 16 MiB.
- Pages receives visible groups only. The archive manifest never enters `_site`. Hidden = absent from Pages, readable in the public archive.

| State | Source read | Pages | Archive bytes |
| --- | --- | --- | --- |
| `live` | Yes, one commit per run | Latest accepted; retained on failure | Kept |
| `frozen` | No | Pinned snapshot | Kept; snapshot required |
| `hidden` | No | Absent | Kept if present |

- The archive mirrors the registry. A snapshot belongs to its row's repository and branch; change either by reviewed PR and the snapshot starts over (the group is skipped until its first good import). A `frozen` group's source cannot change. Remove the row and the entry leaves the manifest. Old bytes stay in `student-work` history.
- Unreachable source: last accepted snapshot kept.

### 9.5 Release

Identity v2: `main` commit, archive commit, tree, manifest and site digests. Site digest excludes `release.json`.

- Archive write fails: no deployment.
- Deployment fails: archive kept; next run retries the pair before any new import.
- `authorize` rechecks `main` and latest attempt before the Pages mutation.
- A newer archive or release supersedes an older accepted run; a run never rolls the site back.
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

- Rename, transfer or branch change: update the row by reviewed PR and keep the slug. The snapshot starts over: the group is skipped until the first good import from the new source.
- Membership: verify username; invite to the correct repository only. Review direct and inherited grants of departing members. Removing access does not unpublish.
- Unpublish: set `hidden` by reviewed PR; confirm the next verified release omits the entry. Never delete `text.md` or `visual.svg` to unpublish: an incomplete update retains the previous snapshot.
- Failed import: students correct content from the run summary; maintainers correct address, branch, quota and publication faults.

### 10.4 Course repository

Review every PR to `main`, including the full **Files changed** list. The required protection baseline is a PR and the GitHub Actions `validate` check, with an up-to-date branch and resolved review threads. The recorded configuration has zero required approvals and no bypass actors; recheck effective settings before relying on it. `main` and `student-work` reject deletion and non-fast-forward updates. The `github-pages` environment is restricted to `main`. PR validation is offline, read-only, fixture-based.

## 11. Recovery

### 11.1 Ordinary recovery

- Pending or unverified release: next run selects `retry` or `repair`. No manual action.
- Shadow build:

  ```sh
  python3 scripts/build_site.py --archive-snapshot <verified-folder> --archive-commit <full-sha>
  ```

  Uses code and registry from the current checkout; check out the recorded `main` commit for exact reproduction. No import, no Pages read, no publication. Does not prove the folder's origin.

### 11.2 Unpublishing and history requests

For ordinary unpublishing, set the group to `hidden` through review, publish and verify both its absent entry and absent served content paths. The public archive still retains its bytes.

For a requested history purge, first set the pause switch. Inventory the affected paths/blob identities across `student-work`, Actions artifacts and the served site. Read-only `git rev-list --objects` and the Actions artifact inventory establish the copies under repository control. Prepare the exact refs/artifacts to change, preserved unaffected release pairs and re-verification steps for specific approval. After an authorized rewrite, rebuild only from the reviewed remaining state, verify the new archive and served inventories, and record which copies were removed. No history rewrite or deletion is automatic. Public clones/caches and excluded originals remain outside that operation.

## 12. Verification

Offline preview builds contain local entries only and must never be deployed over an imported publication. Build `_site` again before browser checks.

```sh
python3 scripts/sync_course_materials.py
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_annotations.js scripts/test_playback.js scripts/test_editor.js
python3 scripts/build_site.py
actionlint .github/workflows/*.yml
git diff --check
```

- Browser: `scripts/check_editor_browser.js`, `check_editor_fallback.js`, `check_editor_fidelity.js`, in Chrome and WebKit. Rebuild `_site` first.
- CI: `validate.yml` runs unit, Node and offline build on PRs. Publication reruns tests before import.

For outstanding hosted, browser and student-account validation, use [acceptance checks](docs/2026-10-15_ddd_acceptance-checks.md). Past test counts and deployment observations are retained in [publication evidence](docs/2026-10-15_ddd_publication-evidence.md); rerun checks for the revision being reviewed.
