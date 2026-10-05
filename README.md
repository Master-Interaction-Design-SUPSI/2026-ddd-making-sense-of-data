# Share your Data Driven Design work

Follow these steps to put your group's work on the course website. You need a GitHub account and an invitation from the course team to edit this repository. Ask them for the repository link and the website link. A course maintainer reviews each contribution before it appears on the website.

Open the [`example` folder](example/) to see a complete entry. Its numbers and visual are invented for demonstration; use your own work in your group's folder.

## 1. Prepare your visual

Choose a short, unique name for your group, such as `city-water`. Use lowercase letters, numbers and hyphens. The name will also appear in the link to your work, so choose one you can keep.

Prepare one image at **1920 × 1080 pixels** if possible. Other sizes will fit inside the same wide space on the page. Name it `visual.png`, `visual.jpg`, `visual.jpeg`, `visual.webp` or `visual.gif`, according to its file type. Keep it on your computer for now.

## 2. Write your text on GitHub

1. Sign in to GitHub and open the course repository.
2. Choose **Add file → Create new file**.
3. In the filename box, type your group name, a slash, and `text.md`. For example: `city-water/text.md`. This also creates your group's folder.
4. Paste this example into the large text box and replace its title, names and paragraphs with your own:

   ```text
   ---
   title: City Water
   authors: Ada Rossi, Ben Kaya
   ---

   What changed in the city's water use?

   We compared the records with what we saw in the neighbourhood.
   ```

5. Keep the three dashes above and below the title and authors. Choose **Commit changes** and create a new branch when GitHub offers that choice. Open a pull request for your group’s work.

GitHub calls a saved change a *commit*. You do not need to install software or use a terminal. GitHub's [file creation guide](https://docs.github.com/en/repositories/working-with-files/managing-files/creating-new-files) shows the editor if you need pictures.

## 3. Upload your visual

1. Open the folder you just made on GitHub (`city-water` in this example).
2. Choose **Add file → Upload files** and select your `visual.png` (or the image ending you chose).
3. Check that GitHub shows the image inside your folder, then choose **Commit changes** and save it to the same branch as your text. Your pull request should contain both files.

You can add extra pictures, audio or video to the same folder later if your text uses them. GitHub's [upload guide](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository) shows the upload screen. A single file uploaded through GitHub's browser interface must be under **25 MB**.

Your work appears automatically after a course maintainer approves and merges the pull request containing both `text.md` and the visual. You do not need to edit a list or tell the website where to find your folder.

## 4. Check your work

After the website has updated, open it. The example stays first, followed by group folders in alphabetical order. Find your title in the list, then check your text, visual and any links. If your work does not appear, check that your folder sits beside `example` and contains `text.md` and exactly one image named `visual.png`, `visual.jpg`, `visual.jpeg`, `visual.webp` or `visual.gif`. The spelling and capital letters must match.

## Add links, notes or colour to your text

You can write plain paragraphs in `text.md`. These extras are optional:

| Write this in your text | What a visitor can do |
| --- | --- |
| `[[the original data\|https://example.org/data]]` | Click the highlighted words to open a website. |
| `[[our photograph\|photo.jpg]]` | Click to see `photo.jpg`, if that file is in your folder. Sound and video files work the same way. |
| `[[three visits\|we visited in April, May and June]]` | Click to reveal a short note in the sentence. |
| `[[our photograph\|photo.jpg\|📷]]` | Use your own emoji as the marker. |
| `{crimson\|a key finding}` | Show those words in a colour you choose. |

Put a YouTube, Vimeo, Giphy or Tenor link in place of `photo.jpg` to show a player. For a file in your folder, write only its filename: `photo.jpg`, rather than the folder name and filename. Names must match exactly, including capital letters. A note cannot contain `|`, `[` or `]`.

The site is part of [Data Driven Design at MAInD — SUPSI](https://maind.supsi.ch/master-interaction-design/en).
