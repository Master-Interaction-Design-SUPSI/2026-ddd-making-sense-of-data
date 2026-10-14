# Data Driven Design: course website

Source of the [course website](https://master-interaction-design-supsi.github.io/2026-ddd-making-sense-of-data/), part of [Data Driven Design at MAInD — SUPSI](https://maind.supsi.ch/master-interaction-design/en).

Each group edits its own public repository. This repository imports the registered groups and publishes them together. The course team maintains it; students do not edit it.

| Reader | Document |
| --- | --- |
| Student | This README, §1–9. The same guide is the README of every group repository. |
| Maintainer | [Maintainer manual](MAINTAINERS.md). |

**WARNING**: Group repositories are public. Upload only material cleared for publication.

## 1. Prerequisites

- GitHub account and browser. Nothing to install.
- From the course team: group repository link, group identifier (slug), website link, invitation if another student owns the repository.
- Publication starts when the course team confirms setup.
- Edit only your group repository, never this one.
- Group repositories start from [`templates/group/`](templates/group/). Its text, visual and `media/example-*` files are demonstrations with invented numbers. Replace them. Delete a file only after removing its reference from `text.md`.

## 2. Files

| Path | Role | Rule |
| --- | --- | --- |
| `text.md` | Text | Required. Front matter: title, authors. |
| `visual.svg` | Main visual | Required unless the course team exempts your group. 1920 × 1080 SVG. |
| `media/…` | Extra images, audio, video | Optional. Referenced from `text.md`. |

Everything else (READMEs, design sources, HTML, scripts) is ignored and never published.

## 3. Edit text

1. Sign in. Open your group's repository. Accept the invitation first if another student owns it. Branch menu: **main**.
2. Open `text.md`; click the pencil icon.
3. Replace title, authors and paragraphs. Keep both `---` lines:

   ```text
   ---
   title: Your project title
   authors: Your group members
   ---

   What question did you investigate?

   Explain what you found and how you worked with the data.
   ```

4. **Commit changes** → **Commit directly to the main branch** → confirm.

GitHub offers only a fork or a proposed change: wrong repository, or invitation not accepted. Ask the course team to check your access. Reference: [GitHub editing guide](https://docs.github.com/en/repositories/working-with-files/managing-files/editing-files).

## 4. Upload main visual

1. Export the whole 1920 × 1080 px frame as SVG, named `visual.svg`. Renaming a PNG or JPG to `.svg` converts nothing.
2. Repository root → **Add file → Upload files** → `visual.svg` → commit to **main**. The same name replaces the previous file.
3. Required attribute: `viewBox="0 0 1920 1080"`. Width and height, if present: 1920 and 1080 px.
4. One main visual only. Delete any `visual.png` or `visual.jpg`: **… → Delete file** → commit to **main**.

`text.md` and `visual.svg` stay at the root. No group folder.

## 5. Add media

1. Open `media/` (or the root) → **Add file → Upload files** → commit to **main**.
2. Reference the exact path in `text.md`:

   ```text
   [[Listen to the interview|media/interview.mp3]]
   [[Watch the recording|media/recording.mp4]]
   ```

3. To remove a file: delete its reference, commit, then delete the file.

Filenames: lowercase letters, digits, `.`, `_`, `-`. First character a letter or digit. No spaces. Example: `survey-photo.jpg`. References must match exactly, including case.

| Class | Formats | Maximum |
| --- | --- | --- |
| Text | `text.md` | 256 KiB |
| SVG | `.svg` | 5 MiB each |
| Image | PNG, JPG/JPEG, WebP, GIF, AVIF | 20 MiB each |
| Audio | MP3, M4A, WAV, OGG, AAC, FLAC | 20 MiB each |
| Video | MP4, WebM, MOV | 20 MiB each |
| Group | ≤ 100 files | 50 MiB total |

SVG: static, self-contained. No scripts, links, animation or external files. Only the main visual must be 1920 × 1080; extra images keep their own dimensions.

## 6. Check publication

Valid commits publish automatically, without approval. The site refreshes every ten minutes; delivery can take longer.

1. Open the website. Find your title. Check text, visual, links and media.
2. Not updated: open [publication results](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-making-sense-of-data/actions/workflows/pages.yml) → latest completed run on **main** → summary **Course publication** → row with your group identifier.

| Status | Meaning |
| --- | --- |
| `updated` | New version selected for this run. |
| `unchanged` | Same version as before. |
| `retained` | Update rejected; previous version kept. Read the explanation. |
| `skipped` | No valid published version yet. Correct and commit again. |
| `disabled`, `hidden`, `frozen` | Publication turned off or fixed by the course team. |

Published = run marked **verified** or **unchanged**. `updated` alone proves nothing. Run failed, unverified or pending; no row; no runs: contact the course team.

| Fault | Action |
| --- | --- |
| Wrong SVG size | Re-export the whole 1920 × 1080 frame. |
| Missing media | Upload the file, or correct its path in `text.md`. Match case exactly. |
| Rejected update | Correct; commit to **main**. The next refresh retries. The previous version stays online meanwhile. |

## 7. Syntax

Prose: line breaks and blank lines between paragraphs become spaces, forming one continuous justified passage. Two trailing spaces or a trailing backslash also become a space. Headings, lists, blockquotes and code blocks stay separate; code keeps its line breaks. Never break a URL or filename across lines.

| Write in `text.md` | Result |
| --- | --- |
| `*a few words*` | Italic. |
| `[[the source\|https://example.org/data]]` | Link. |
| `[[a detail\|A short explanation]]` | Note: `{✶ A short explanation}`. |
| `[[the source\|https://example.org/data\|type=bibliography]]` | Bibliography: `(■ …)`. |
| `[[a note\|More context\|type=note;color=green]]` | Green note. |
| `[[a quotation\|Quoted words\|type=quote;style=italic;color=crimson]]` | Crimson italic quotation. |
| `{crimson\|a key finding}` | Colored text. |
| `[[the sketch\|media/example-detail.svg]]` | Inline image. |
| `![A description](media/example-tall.svg)` | Inline image (Markdown). |
| `[[a tone\|media/example-tone.mp3]]` | Inline audio. |
| `[[a video\|media/example-motion.mp4]]` | Inline video. |

Annotation form: `[[label|target|modifiers]]`. Modifiers are optional, separated by `;`, in any order.

| Modifier | Values |
| --- | --- |
| `type` | `note`, `bibliography`, `quote` |
| `style` | `italic`, `normal` |
| `color` | `crimson` or any CSS color, e.g. `#116964` |

- Note: open by default; `{✶ text}`; black italic. `style=normal` removes italic. The label toggles it.
- Bibliography: open by default; inline, black, `(■ Author, year. Title)`. URLs stay links. Black overrides every color. The label toggles it.
- Quote: quotation marks on reveal.
- An explicit `color` overrides a surrounding `{color|…}` span; otherwise the span applies.
- `{color|✶ text}` renders in braces.
- Markdown `*italics*` works in prose and labels.
- Two-field annotations and emoji markers in the third field still work.
- Unknown, repeated or malformed modifiers stay visible as literal text. Correct them.
- Never put `|`, `[` or `]` inside a field.

## 8. Media behavior

| Media | Behavior |
| --- | --- |
| Image | Inline thumbnail; aspect ratio kept. Hover or focus: large opaque preview, expanding from the thumbnail. Tap or Enter/Space: pin. Dismiss: tap again, **Close media preview**, Escape, tap elsewhere, scroll, switch entry. Linked image: hover previews, activation follows the link. |
| Audio | Native controls. Hover plays; leaving pauses. |
| Video | Inline, muted, no buttons. Starts when half visible. Hover or focus: enlarged preview with sound; leaving stops it. Click or tap: pin. |
| External embed | Opens manually from its annotation. Fallback: **Open media on provider**. YouTube/Vimeo playback unverified. |

- Playback stops off screen, on entry switch, while the main image is enlarged, and when the page is hidden.
- Reduced motion: no autoplay, no preview animation.
- Browsers may require a click before sound. Refused autoplay leaves the video still.
- Playback depends on file encoding and browser. Check your entry on the site.

## 9. Editor (preview)

Use only after the course team announces it. URL: website link + `editor/`.

1. **Group** → **Open last published version**. New group: **Start from template**.
2. Edit the Markdown. Attach files with their exact destination, e.g. `media/new-photo.png`.
3. **Check and prepare GitHub steps**. Resolve every error.
4. Execute the generated steps in order: compare current GitHub text, upload media, commit `text.md`, delete removed files.
5. Replacing referenced media: use a new filename.
6. If shown, **Request publication** after all commits. Read the answer on the issue.

Drafts are saved in this browser only. Restore brings back text and attachments together. Storage denied or full: the editor lists what was not saved; a text-only backup needs its media reattached or discarded. Saving unavailable: **Download text.md** before leaving.

**WARNING**: A browser draft is not a commit. A completed checklist proves neither commit nor publication.

## 10. Repository layout

| Path | Content |
| --- | --- |
| `groups.json` | Registry of group repositories. |
| `example/` | Demonstration entry. |
| `templates/group/` | Group repository template. Copy its contents, not the folder. |
| `editor/` | Entry editor. |
| `static/` | Renderer, styles, content contract. |
| `scripts/` | Build, import, publication, tests. |
