# Group repository: student guide

Your group's public repository. You write your text and add your media here. The [course repository](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-making-sense-of-data) collects every group's page and publishes the course website.

**WARNING**: Everything here is public. Upload only material cleared for publication.

## 1. Prerequisites

- A GitHub account and a browser. Nothing to install.
- One repository per group. Open the repository assigned by the course team and accept your GitHub invitation. The course team adds each teammate with Write access. If you have not received a repository link, ask the course team before creating another repository.
- The course team sends you your group identifier (slug) and the website link, and tells you when publication starts.
- Edit only your group repository, never the course repository.
- The text, visual, `media.csv` and `media/example-*` files you start with are demonstrations with invented numbers. Replace them.

## 2. Files

| Path | Role | Rule |
| --- | --- | --- |
| `text.md` | Text | Required. Front matter: title, authors. |
| `media.csv` | Media table | When you use media. First line `name,source,file`; one row per media file used in `text.md`. |
| `visual.svg` | Main visual | Required. 1920 × 1080 SVG. |
| `media/…` | Extra images, audio, video | Optional. Listed in `media.csv`, named in `text.md`. |

Everything else (this README, design sources, HTML, scripts) is ignored and never published.

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

If GitHub offers only a fork or a proposed change, you are in the wrong repository or have not accepted the invitation yet. Ask the course team to check your access. Reference: [GitHub editing guide](https://docs.github.com/en/repositories/working-with-files/managing-files/editing-files).

## 4. Upload main visual

1. Export the whole 1920 × 1080 px frame as SVG, named `visual.svg`. Renaming a PNG or JPG to `.svg` converts nothing.
2. Repository root → **Add file → Upload files** → `visual.svg` → commit to **main**. The same name replaces the previous file.
3. Required attribute: `viewBox="0 0 1920 1080"`. Width and height, if present: 1920 and 1080 px.
4. One main visual only. Delete any `visual.png` or `visual.jpg`: **… → Delete file** → commit to **main**.

`text.md` and `visual.svg` stay at the root. No group folder.

## 5. Add media

1. Open `media/` (or the root) → **Add file → Upload files** → commit to **main**.
2. Add a row to `media.csv` at the root: the name you write in the text, the link to the original source, the file's exact path. Commit.

   ```text
   name,source,file
   interview,https://archive.org/details/example-interview,media/interview.mp3
   lithium mine,https://example.org/photo-of-the-mine,media/mine.jpg
   ```

3. Write the name in double brackets in `text.md`: `[[interview]]`, `[[lithium mine]]`. Commit. The file appears there; a click opens its source.
4. To remove a file: delete its name from `text.md`, the file and its row.

Commit in any order. Until a file is there, your page shows *missing: its name* in its place.

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

Every commit to `main` requests a website refresh. Allow a few minutes for the checks and publication; GitHub can delay the jobs. Open the site and look at your entry: it says which commit is online, or what to fix.

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

Use only after the course team announces it. URL: website link + `editor/`.

1. **Group** → **Open last published version**. New group: **Start from template**.
2. Edit the Markdown. Attach files with their exact destination, e.g. `media/new-photo.png`, a name and a source link; the row goes into `media.csv`. **Insert in text** writes `[[name]]`.
3. **Check and prepare GitHub steps**. Resolve every error.
4. Follow the generated steps on GitHub, in any order: compare current GitHub text, upload media, commit `media.csv`, commit `text.md`, delete removed files.
5. To replace media the text already uses, upload it under a new filename.
6. If shown, **Request publication** after all commits. Read the answer on the issue.

Drafts are saved in this browser only. Restore brings back text and attachments together. If storage is denied or full, the editor lists what was not saved; a text-only backup needs its media reattached or discarded. If saving is unavailable, **Download text.md** before leaving.

Nothing is published until the commits are on GitHub. After the last one, look at your entry on the site (§6).
