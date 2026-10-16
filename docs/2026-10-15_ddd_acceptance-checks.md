# DDD acceptance checks

Outstanding validation as of 2026-10-15. V2 deployment and archive cutover are recorded as completed; the checks below remain open. Use [the maintainer manual](../MAINTAINERS.md) for procedures and [publication evidence](2026-10-15_ddd_publication-evidence.md) for past results. Local tests, served verification and student acceptance establish different things.

## Before a publication or settings operation

- [ ] Read the live release and deployment records; compare the code/archive pair and served verification with the intended operation. Preserve the checked identities and date.
- [ ] Recheck the effective `main` and `student-work` protections, the `github-pages` restriction to `main`, and publication/request switches. The recorded Actions-token pilot passed; confirm its protection assumptions still hold.
- [ ] Review controller/workflow security against actual Actions permissions, the pinned `main`, rerun artifact admission, concurrency and issue answers. Record the reviewed revision and findings.
- [ ] Run the manual's local verification commands for the exact revision under review and retain their results.

## Hosted recovery and archive operations

- [ ] Rehearse capture → V1 rollback → V2 resume through `recovery.yml`: preserve the reviewed release/capture identities, verify every captured served file, prove the archive head is unchanged, and clear the pause only after served V2 verification. Include pending-attempt and changed-code retries; local fixture coverage is recorded separately.
- [ ] Rehearse source loss and offline reproduction. Verify retained accepted snapshots and reproduce the full pinned code/archive release without source repositories or media caches.
- [ ] Publish and protect the reviewed `final-2026` freeze tag; verify the tag reproduces the exact full release pair and rejects altered pins/digests. Local tag creation alone does not complete this check.
- [ ] Rehearse a reviewed identity rebind against the exact archive parent. Verify old bytes remain under the hidden retired slug, then verify the new identity through an ordinary refresh.
- [ ] Exercise deployment races and superseded attempts; confirm stale authority cannot deploy or answer requests.

## Capacity and browser checks

- [ ] Measure hosted Git transfer/writer costs, wire usage and runner timing near the group/site limits. `archive_transfer_bytes` measures `.git/objects` as a proxy; the recorded fixture measurements do not settle these costs. Review history growth and the cost of idle runs.
- [ ] Obtain stable Chrome fidelity results at 1280 and 390 px for the current CSS. Investigate the previously inconsistent 23 native-audio-edge pixels without adding tolerance or masking to conceal the mismatch.
- [ ] Check lifecycle, storage denial/quota, security, inventory and capacity behavior for the current release. Rebuild `_site` before browser checks.
- [ ] Check actual Safari media behavior, keyboard and touch on the selected devices; retain device/version and results.

## Ordinary student accounts and request rollout

**Deferred by the user on 2026-10-14:** selection of two non-admin student pilot accounts and a Safari device/version. Do not request these details again during the current implementation. `lucadra` and `ginevraterenghi` have maintainer privileges and cannot establish student isolation. No pilot access changes were made for this deferred item.

- [ ] With each of two ordinary student accounts, edit text, upload media, delete an unreferenced file and submit a publication request. Verify the published revision and access to the **Course publication** summary.
- [ ] Verify each pilot has Write access to its own group and less than Write to every other group repository and the course repository, including inherited grants.
- [ ] Pilot issue eligibility, answers, failed/transient requests, cooldown and close repair. Enable Issues, label, editor UI policy and request switches only through the authorized settings change.
- [ ] After pilot acceptance, complete the access cutover by reviewing/removing student course-repository grants. Retain the effective-access audit.
- [ ] After request-pilot acceptance, change the recovery schedule from ten minutes to hourly, off the hour, through review. Record the accepted pilot and resulting workflow revision.

## Evidence to retain

For each completed check, record the date, exact code/archive/release identities, environment or account/device used, expected and observed result, and evidence location. Keep evidence in maintainer-controlled storage beyond Actions retention. `.scratch/` and `/tmp` are local working locations, not durable backups. History rewrites or deletions require their own specifically authorized operation; they are not acceptance prerequisites.
