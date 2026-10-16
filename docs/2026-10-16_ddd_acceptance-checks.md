# DDD acceptance checks

Open checks for the new course repository, as of 2026-10-16. Procedures are in [the maintainer manual](../MAINTAINERS.md). For each completed check, record the date, the exact code and archive commits, the account or device used, and the result, in maintainer storage outside this repository.

## Before and at the first run

- [ ] `main` ruleset: pull request, the `validate` check, up-to-date branch, resolved threads, no deletion, no non-fast-forward. `student-work` ruleset: no deletion, no non-fast-forward. The `github-pages` environment is restricted to `main`. No Actions variables are set (requests off, not paused).
- [ ] First run (**Run workflow → main**): `student-work` is created as a root commit, the release is recorded and verified, and the site shows the three course-team pages and the editor. A second run reports `unchanged`.

## Student accounts

**Deferred by the user on 2026-10-14:** choosing two non-admin student pilot accounts and a Safari device and version. `lucadra` and `ginevraterenghi` have maintainer rights and cannot show student isolation.

- [ ] With each of two ordinary student accounts: edit the text, upload media, delete an unreferenced file. The entry's status line names the new commit. Commit a broken visual: the entry shows the reason and the previous commit stays online. Fix it: the line clears.
- [ ] Each account has Write on its own group repository and less than Write on every other group repository and on the course repository, inherited grants included.

## Request pilot

- [ ] Enable Issues, the `publication-request` label, `requests_enabled` in `.github/publishing.json` and `DDD_REQUESTS_ENABLED`, through a reviewed change. Pilot eligibility, answers, failed and transient requests, the cooldown and close repair.
- [ ] After the pilot passes, change the schedule from ten minutes to hourly, off the hour, by reviewed PR.

## Devices

- [ ] Safari media behavior, keyboard and touch on the selected devices; record device and version.
- [ ] The editor browser checks (manual §12) in Chrome and WebKit for the revision under review.
