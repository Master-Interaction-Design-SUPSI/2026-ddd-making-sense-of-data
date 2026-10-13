# Share your Data Driven Design work

Each group edits its own public GitHub repository. The main course repository collects the registered groups' text and media and publishes them together on the course website.

**Before you begin:** the course team will confirm your group's repository link, its group identifier, and the website link. These instructions apply once that setup is ready. The repository may belong to a student or to the course organization. You need only a GitHub account and a browser.

Edit your contribution in your group's repository. The course team maintains this main repository and its publishing setup. All group repositories are public, so use material intended for public sharing.

The [group template](templates/group/) contains demonstration text and invented numbers. Replace them with your group's work. Keep the repository name and file locations supplied by the course team.

## 1. Write your text

Sign in to GitHub and open **your group's repository**. If another student owns it, accept their invitation first. The owner can edit directly and should invite the other group members with Write access. Check that the branch menu shows **main**.

Open `text.md` and click the pencil icon. Replace the title, authors, and paragraphs with your own work. Keep the three dashes above and below the title and authors:

```text
---
title: Your project title
authors: Your group members
---

What question did you investigate?

Explain what you found and how you worked with the data.
```

Choose **Commit changes**, select **Commit directly to the main branch**, and confirm. A commit is a saved change. You can edit the same file again whenever you need to make a correction.

If GitHub offers only a fork or a proposed change, check that you accepted the invitation and opened the correct repository. Ask the course team to check your access if direct saving is unavailable. GitHub's [editing guide](https://docs.github.com/en/repositories/working-with-files/managing-files/editing-files) shows the editor.

## 2. Upload your main visual

Prepare a **1920 × 1080 pixel** frame or artboard and export the whole frame as SVG. Name it **visual.svg**. Renaming a PNG or JPG file to `.svg` does not convert it.

At the top level of your group's repository, choose **Add file → Upload files**, select `visual.svg`, and commit directly to **main**. Uploading the same filename replaces the previous visual. Keep `text.md` and `visual.svg` together at the top level; you do not need to create a group folder inside this repository.

The SVG should contain `viewBox="0 0 1920 1080"`. If it also specifies width and height, they must be 1920 and 1080 pixels. Keep only one main visual: remove any older `visual.png` or `visual.jpg` using **… → Delete file**, then commit to **main**.

## 3. Check your entry

After the course team has connected your repository and enabled publishing, valid changes appear automatically without content approval. The site checks for changes roughly every ten minutes, but updates can take longer. Open the website link supplied by the course team, find your title, and check your text, visual, and links.

If an update does not appear, open [publication results](https://github.com/Master-Interaction-Design-SUPSI/2026-ddd-making-sense-of-data/actions/workflows/pages.yml), choose the latest completed run on **main**, and find **Course publication** in its summary. Use the group identifier supplied by the course team to find your row.

| Status | What it means |
| --- | --- |
| `updated` | Your new files were selected for this publication. |
| `unchanged` | The selected version is the same as before. |
| `retained` | The update could not be used; your previous published version was kept. Read the explanation in your row. |
| `skipped` | This group has no usable published version yet. Read the explanation, correct the files, and save again. |
| `disabled` | Publication for this group has been turned off by the course team. |

A run marked **verified** confirms that its publication completed. **unchanged** means no new deployment was needed. If the overall result is failed, unverified, or still awaiting deployment, ask the course team to check it; an `updated` row alone does not confirm that your changes are online. If your group has no row, or there are no publication results yet, ask the course team to check the setup.

For an incorrect SVG size, export the whole 1920 × 1080 frame again. For a missing media file, upload it or correct its filename in `text.md`. Keep names and capital letters exactly the same. Commit the correction to **main**; a later refresh will try again. An invalid or incomplete update keeps the previous published entry when one exists.

## Optional text and media

These examples use the supported syntax:

| Write in text.md | What a visitor can do |
| --- | --- |
| `*a few words*` | Read italic text. |
| `[[the source\|https://example.org/data]]` | Open a link. Replace the example address with your source. |
| `[[a detail\|A short explanation]]` | Reveal a black italic note marked with `{∗}`. |
| `[[the source\|https://example.org/data\|type=bibliography]]` | Reveal a bibliography reference marked with `(■)`. |
| `[[a note\|More context\|type=note;color=green]]` | Reveal a green italic note with the same braces and marker. |
| `[[a quotation\|Quoted words\|type=quote;style=italic;color=crimson]]` | Reveal a crimson italic quotation. |
| `{crimson\|a key finding}` | Read those words in colour. |
| `[[the sketch\|media/example-detail.svg]]` | See an inline image; hover, focus or tap for a background preview. |
| `![A description](media/example-tall.svg)` | Show a Markdown image inline with the same preview interaction. |
| `[[a tone\|media/example-tone.mp3]]` | Use inline audio controls; audio starts with Play. |
| `[[a video\|media/example-motion.mp4]]` | See a muted inline video with Play/Pause and Sound/Mute. |

The optional third field combines modifiers separated by semicolons: `type=note`, `type=bibliography` or `type=quote`; `style=italic` or `style=normal`; and `color=crimson` or another CSS color such as `#116964`. Their order does not matter. Notes are black and italic by default. An explicit color overrides a surrounding color span; otherwise a surrounding `{crimson|...}` still works. `style=normal` turns off the note's default italics. Plain prose and annotation labels also accept Markdown `*italics*`.

Existing two-field annotations and third-field emoji markers still work. Unknown, repeated or malformed modifiers remain visible as the original text so you can correct them; they do not become styled annotations. Do not include `|`, `[` or `]` inside a field.

To add an extra image, audio clip, or video, open `media`, choose **Add file → Upload files**, and commit to **main**. If there is no `media` folder, you can upload the file beside `text.md` instead. Reference the exact path: `media/interview.mp3` for a file inside `media`, or `interview.mp3` for a file at the top level. For example, after uploading those files:

```text
[[Listen to the interview|media/interview.mp3]]
[[Watch the recording|media/recording.mp4]]
```

Images appear as small thumbnails within the sentence, keeping their aspect ratio. Hover or keyboard-focus an image for an enlarged, faint background preview. Tap or press Enter/Space to keep the preview open; tap again, choose **Close image preview**, press Escape, or tap elsewhere to dismiss. Leaving a hovered image, moving focus away, scrolling or switching entries clears the preview. A Markdown image inside a link keeps that link: hover/focus previews the image and activation follows the link. Audio and uploaded video appear directly within the sentence. Audio starts only when you press its Play control. Uploaded videos try to start muted when at least half visible; use **Play/Pause** and **Sound/Mute** beside the thumbnail. If automatic playback is refused, press **Play**. Videos stop when off screen, when switching entries, while the main image is enlarged, or when the page is hidden. A video you pause stays paused, and a video with sound enabled needs Play after becoming inactive. Reduced-motion preferences use manual playback. External embeds open manually from their annotation; if the player is unavailable, choose **Open media on provider**. Muted autoplay and manual controls passed in Chrome 154 on desktop and a 390px touch emulation. This is an experiment, not a promise for every browser or device. YouTube/Vimeo streaming was not verified; their manual reveal and provider links were checked with simulated provider responses. Preview your own files in the website, because playback depends on their encoding. Extra images keep their own dimensions: **only the main visual must be a 1920 × 1080 SVG**. Supplementary images may be SVG, PNG, JPG/JPEG, WebP, GIF, or AVIF.

Use lowercase filenames without spaces, such as `survey-photo.jpg`; letters, digits, dots, underscores, and hyphens are supported. Keep paths within your repository. A note cannot contain `|`, `[` or `]`. If you remove a media file, remove its reference from `text.md` too.

File limits are 256 KiB for text, 5 MiB per SVG, 20 MiB per other media file, and 100 files / 100 MiB in total. Supported audio: MP3, M4A, WAV, OGG, AAC, FLAC. Supported video: MP4, WebM, MOV. Export SVGs as static, self-contained artwork. Source/design files and README files are not included in your published entry.

The site is part of [Data Driven Design at MAInD — SUPSI](https://maind.supsi.ch/master-interaction-design/en).
