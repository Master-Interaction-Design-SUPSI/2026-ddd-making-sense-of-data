# Maintaining the course site

Review each student pull request before merging it into `main`. Check the full **Files changed** list as well as the rendered entry: collaborators can propose changes to site code and the publishing workflow, not only their group folder. Keep the `main` ruleset requiring review.

GitHub Pages should use **GitHub Actions** as its publishing source. The workflow in `.github/workflows/pages.yml` builds and publishes the site after changes reach `main`. After configuring Pages, check the published URL and one entry.

The renderer treats `text.md` as data: it escapes submitted HTML and rejects unsafe link and media URL schemes. Keep those safeguards when changing the Markdown or annotation code. The vendored Marked license is at `static/vendor/LICENSE.marked.md` and is included in the published site.
