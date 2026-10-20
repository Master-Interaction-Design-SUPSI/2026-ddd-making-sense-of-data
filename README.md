# Data Driven Design: course website

Source of the [course website](https://master-interaction-design-supsi.github.io/2026-ddd-making-sense-of-data/), part of [Data Driven Design at MAInD — SUPSI](https://maind.supsi.ch/master-interaction-design/en).

Each group edits its own public repository. This repository imports the registered groups and publishes them together. The course team maintains it; students do not edit it.

| Reader | Document |
| --- | --- |
| Student | This README, §1–9. The same guide is the README of every group repository. |
| Maintainer | [Operating manual](MAINTAINERS.md), [acceptance checks](docs/2026-10-16_ddd_acceptance-checks.md). |

**WARNING**: Group repositories are public. Upload only material cleared for publication.

<!-- BEGIN GENERATED STUDENT GUIDE: templates/group/README.md -->

## 1. Prerequisites




- A GitHub account and a browser. Nothing to install.
- Your group has its own project folder on GitHub, called a repository. Open the link assigned by the course team and accept your GitHub invitation. Each teammate needs permission to save changes, called **Write** access. If you have not received a repository link, ask the course team before creating another repository.
- The course team sends you your group identifier, such as `group-01` (also called a slug), and the website link, and tells you when publication starts.
- Edit only your group repository, never the course repository.
- The text, visual, `media.csv` and `media/example-*` files you start with are demonstrations with invented numbers. Replace them.

## 2. Files

Open your repository's **Code** tab. The first file list, where you see `text.md`, is the repository root. Keep `text.md`, `media.csv` and `visual.svg` here; put extra images, audio and video inside the `media` folder.

| Path | Role | Rule |
| --- | --- | --- |
| `text.md` | Text | Required. Title and authors go between the two `---` lines at the top. |
| `media.csv` | Media table | When you use media. First line `name,source,file`; one row per media file used in `text.md`. |
| `visual.svg` | Main visual | Required. 1920 × 1080 SVG. |
| `media/…` | Extra images, audio, video | Optional. Listed in `media.csv`, named in `text.md`. |

The course website ignores other files, including this README, design sources, HTML and scripts. They remain visible in your public GitHub repository.

## 3. Edit text

1. Sign in and open your group's repository after accepting the invitation. On the **Code** tab, check that the branch selector above the file list says **main**. This is the version the website reads.
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

4. Click **Commit changes**. Enter a short description, such as `Update project text`, select **Commit directly to the main branch**, then confirm with **Commit changes**. A commit is a saved version of your changes on GitHub. Use these same steps whenever this guide says to commit.

If GitHub offers only a fork or a proposed change, stop and ask the course team to check the repository link, your invitation, your access and the branch settings. Reference: [GitHub editing guide](https://docs.github.com/en/repositories/working-with-files/managing-files/editing-files).

## 4. Upload main visual

1. In your design application, set the frame or page to 1920 × 1080 px. Select the whole frame or page and export it in **SVG** format as `visual.svg`. Renaming a PNG or JPG to `.svg` does not convert it.
2. Open your repository's **Code** tab. Stay on the file list where you see `text.md`. Choose **Add file → Upload files**, upload `visual.svg`, then commit to **main**. The same name replaces the previous file.
3. Keep one main visual only. If an old `visual.png` or `visual.jpg` is present, open it, choose **… → Delete file**, then commit to **main**.

`text.md` and `visual.svg` stay at the root. No group folder.

<details>
<summary>If publication reports incorrect SVG dimensions</summary>

Open `visual.svg` on GitHub and click the pencil icon to see its code. Near the beginning, find `<svg` and check for `viewBox="0 0 1920 1080"`. This records the drawing's dimensions. If `width` and `height` are present, they must be `1920` and `1080`, optionally followed by `px`.

If these values differ, return to your design application and export the correct frame again; changing the numbers alone does not resize the drawing. Leave the code without saving. Ask the course team if you cannot find these values or the export is rejected again.

</details>

## 5. Add media

1. Give the file a name using lowercase letters, digits and hyphens, such as `mine.jpg`. Open the existing `media` folder in your group repository, choose **Add file → Upload files**, upload the file, then commit to **main**. A file uploaded here as `mine.jpg` has the path `media/mine.jpg`.
2. Return to the repository's **Code** tab, open `media.csv` and click the pencil icon. Keep the first line `name,source,file`. Add one line below it for your file: a short name to use in the text, the original source link, and the exact path. For your own material, leave the source empty but keep both commas: `my photo,,media/my-photo.jpg`. Commit.

   ```text
   name,source,file
   interview,https://archive.org/details/example-interview,media/interview.mp3
   lithium mine,https://example.org/photo-of-the-mine,media/mine.jpg
   ```

3. Write the name in double brackets in `text.md`: `[[interview]]`, `[[lithium mine]]`. Commit. The file appears there; a click opens its source.
4. To remove media, delete its `[[name]]` reference from `text.md` and its row from `media.csv`, committing each change. Then open the file in `media`, choose **… → Delete file**, and commit.

Follow the order above for each new file. If the website refreshes before all your changes are saved, it may temporarily show *missing: its name*. Finish the remaining steps and check publication again.

List your media in `media.csv` when you use media. Names: unique, ignoring case; no `[`, `]` or `|`. Source: a full `https://` link, or empty for your own material. A spreadsheet can save it as **CSV UTF-8**; commas or semicolons both work. Typing in GitHub: put a value containing a comma in double quotes.

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

Every commit to `main` requests a website refresh. Publication takes time for checks and processing; GitHub can delay it. Saving on GitHub does not mean the change is online yet.

Open the course website and select your group. The line under the title identifies the commit currently online. Compare it with the short commit identifier shown beside your latest change on GitHub. If the site says your update was not published, correct the stated problem and commit again; the previous accepted version stays online. If your latest change still does not appear after checking again later, send the course team your repository link and the commit identifier.

## 7. Syntax

Your text becomes one continuous, justified passage: line breaks and blank lines turn into spaces. Headings, lists, quotes and code stay separate. Never break a link or filename across lines.

| Idea | Write in `text.md` | Result |
| --- | --- | --- |
| Research question | `*Where and how is AI computation performed?*` | Sans serif italic. |
| Keyword or key concept | `**Data centres**` | Sans serif bold. |
| Data source | `[Dataset XX](https://example.org/data)` | Sans serif, underlined; opens in a new tab. Use links for data sources; ordinary words have no underline formatting option. |
| Note | `[[a detail\|A short explanation]]` | `{✶ A short explanation}`, open; the label folds it. |
| Citation | `[[Parikka, 2015\|A geology of media\|type=bibliography]]` | Serif, black: `(■ Parikka, 2015. A geology of media)`, without underlining unless linked to its source. For an underlined source citation, use `[[Parikka, 2015. A geology of media\|https://example.org/book\|type=bibliography]]`. |
| Quote | `[[a quotation\|Quoted words\|type=quote]]` | The label reveals the words in quotation marks. |
| Media | `[[interview]]` | The image, audio or video named in `media.csv`. |
| Color | `{crimson\|a key finding}` | Colored text. Any CSS color works, e.g. `{#116964\|…}`. |

Notes, citations and quotes take optional modifiers after a second `|`, separated by `;`: `[[a note|More context|type=note;color=green]]`.

| Modifier | Values |
| --- | --- |
| `type` | `note`, `bibliography`, `quote` |
| `style` | `italic`, `normal` |
| `color` | `crimson` or any CSS color, e.g. `#116964` |

- Never put `|`, `[` or `]` inside a field.
- Anything the site cannot read stays visible as plain text. Correct it.

## 8. Media behavior

| Media | Behavior |
| --- | --- |
| Image | Inline thumbnail; aspect ratio kept. Hover or keyboard focus: enlarged preview. Click or tap: opens the original source in a new tab. |
| Audio | Player with a play symbol and label. Hover or keyboard focus: plays with sound; leaving stops playback. Click or tap: opens the original source in a new tab. |
| Video | Inline, muted and looping; starts when half visible. Hover: enlarged preview with sound, when the browser permits playback. Keyboard focus: preview with sound. Click or tap: opens the original source in a new tab. |
| External embed | Opens from its annotation. An **Open media on provider** link is always shown; playback inside the page may vary. |

A click opens the source in the file's `media.csv` row. With an empty source, it opens the published file itself.

The course [media and sources worksheet](https://docs.google.com/spreadsheets/d/1WP-dlkyGFV2Ki1NTLo7auSYGrvrBxwdUBvVFZjOkV30/edit) is the working resource listed in *DDD-REPO-Commenti&Risorse.pdf*. Copy each source URL into the matching row of `media.csv`.

- Playback stops off screen, on entry switch, while the main image is enlarged, and when the page is hidden.
- With reduced motion turned on, videos stay still and previews do not animate.
- Browsers may refuse automatic playback or sound, and playback depends on the file's encoding. If sound is blocked, choose **Enable sound**, then hover again. The source link always works. Check your entry on the site.

## 9. Editor (preview)

Use the [course editor](https://master-interaction-design-supsi.github.io/2026-ddd-making-sense-of-data/editor/) only after the course team announces it. It lets you draft your text and preview the page before saving your changes on GitHub.

1. Select your group in **Group**, then choose **Open last published version**. Use **Start from template** only if your group has no published version; check GitHub first so you do not overwrite a teammate's work.
2. Edit the text using the formatting examples in §7 (called Markdown). To add media, use **Choose a file**, enter its destination, such as `media/new-photo.png`, a name and a source link, then click **Attach file**. Use a new filename when replacing media the text already uses. The editor adds its row to `media.csv`; **Insert in text** writes `[[name]]`.
3. **Check and prepare GitHub steps**. Resolve every error.
4. Follow the generated steps on GitHub: compare the current GitHub text with your draft and keep your teammates' changes, upload media, commit `media.csv`, commit `text.md`, then delete removed files. Checking a box in the editor only records your progress; you still need to save each change on GitHub.
5. If shown, **Request publication** after all commits. Read the answer on the issue.

Drafts are saved in this browser only. Restore brings back text and attachments together. If storage is denied or full, the editor lists what was not saved; a text-only backup needs its media reattached or discarded. If saving is unavailable, **Download text.md** before leaving.

Nothing is published until the commits are on GitHub. After the last one, look at your entry on the site (§6).

<!-- END GENERATED STUDENT GUIDE -->

## 10. Repository layout

| Path | Content |
| --- | --- |
| `groups.json` | Registry of group repositories. |
| `materialities-of-ai/` | Course-team pages, published as they are. |
| `templates/group/` | Canonical student guide and demonstration. Copy its contents, not the folder. |
| `editor/` | Entry editor. |
| `static/` | Renderer, styles, content contract. |
| `scripts/` | Build, import, publication, tests. |

Edit shared instructions and demonstration files in `templates/group/`, then run `python3 scripts/sync_course_materials.py --write`. CI checks the generated copies; see the [operating manual](MAINTAINERS.md#102-provisioning) for the distribution workflow.

## 11. Prepared group repositories

Each repository contains the sample text, visual, media table, image, audio and video demonstrations, and student guide. The twelve groups are registered as `live`; their sample entries appear on the course website and can be opened in the editor. The course team assigns repository access to group members.

| Group | Repository |
| --- | --- |
| 01 | [2026-ddd-group-01](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-01) |
| 02 | [2026-ddd-group-02](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-02) |
| 03 | [2026-ddd-group-03](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-03) |
| 04 | [2026-ddd-group-04](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-04) |
| 05 | [2026-ddd-group-05](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-05) |
| 06 | [2026-ddd-group-06](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-06) |
| 07 | [2026-ddd-group-07](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-07) |
| 08 | [2026-ddd-group-08](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-08) |
| 09 | [2026-ddd-group-09](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-09) |
| 10 | [2026-ddd-group-10](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-10) |
| 11 | [2026-ddd-group-11](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-11) |
| 12 | [2026-ddd-group-12](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-group-12) |
