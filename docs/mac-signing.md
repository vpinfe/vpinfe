# Mac signing

This document explains how the release workflow signs the Mac builds, what happens when signing is unavailable, and how to turn it off or remove it.

Signing is an optional layer on top of `.github/workflows/build.yml`. Without it the workflow makes the same ad-hoc signed Mac builds it always did.

## How it works

The certificate and the notarization key never exist in this repository. A separate signing repository holds them and does the signing.

1. The `build` job makes both Mac builds (full and slim), then hands each one over as an artifact: `mac-sign-input-full` and `mac-sign-input-slim`. Each holds `VPinFE.app.zip` and a `dmg/` folder with the icon, the window background and `layout.json` (the disk image window, from `packaging/dmg-layout.json`). Each zip carries a build provenance attestation.
2. The `request-mac-signing` job calls `.github/workflows/mac-signing.yml`, which starts a run in the signing repository, waits for it, and downloads the result from a draft release in this repository.
3. The signed disk images are checked again here (`codesign --verify --strict`, `spctl`, `stapler`, and the Team ID) and uploaded in place of the ad-hoc builds, under the same names (`build-macos-arm64` and `build-macos-arm64-slim`), so a run lists one Mac download per variant, like the other platforms. The hand-over artifacts are deleted, and so is the draft release.
4. `release_bundle` zips the Mac builds like the others, so the signed ones are what it finds. The zip names do not change. `checksums.txt` and `manifest.json` are made after the Mac zips exist, and `manifest.json` marks every Mac asset with `"mac_signed": true` or `false`.

Signing runs for a pushed `v*` tag, and for a run started by hand with **sign_mac** ticked. A run started by hand leaves its signed builds as run artifacts and never reaches a release. The release itself carries the signed zips for good. Pull requests never request signing.

## Settings

| Setting | Kind | What it does |
| --- | --- | --- |
| `MAC_SIGNING_TOKEN` | secret | Lets the workflow start a run in the signing repository. A fine-grained token with Actions write on the signing repository only. |
| `MAC_SIGNING_REPO` | secret | The signing repository, as `owner/name`. A secret, so GitHub hides it in every log. Not set means signing is withdrawn. |
| `MAC_SIGNING` | variable | Set to `0` to skip signing without removing anything. |

## The three outcomes

- **Signed.** The release carries the signed builds.
- **Withdrawn.** Signing is switched off, or the signing repository cannot be reached or has withdrawn (the dispatch is refused, no run appears within 10 minutes, or the run reports it has no signing credentials). The release is published with the ad-hoc builds, a warning annotation and job summary say so, the release notes end with "Mac builds in this release are unsigned", and `manifest.json` says `"mac_signed": false`.
- **Rejected.** The signing run failed, ran past 60 minutes, or returned something that does not check out. The request job fails and nothing is published. The artifacts handed over for signing stay, so that "Re-run failed jobs" can ask again. Rejection is never treated as a reason to fall back, because it means something is wrong that a person should look at.

Every warning and failure message ends with how to disable signing.

## Token expiry

`MAC_SIGNING_TOKEN` expires. When it does the dispatch is refused with 401 and the outcome is withdrawn, so a release still goes out, unsigned, with the warning. Renew the token and replace the secret before the date, and note the new date in the release checklist.

## Remove Mac signing

To skip signing for now, set the repository variable `MAC_SIGNING` to `0`. Nothing else changes, and removing the variable (or setting it to anything else) turns signing back on.

To remove it permanently:

1. Delete `.github/workflows/mac-signing.yml`.
2. In `.github/workflows/build.yml`, delete the `request-mac-signing` job.
3. In the same file, remove `request-mac-signing` from the `needs` of `release_bundle` and `publish_release`, and from the `if` conditions that name it. The `release_bundle` download already reads `matrix.artifact` alone.
4. In the `build` job, delete the "Hand the build to Mac signing", upload and attest steps for both Mac builds, the `MAC_SIGN_REQUESTED` variable, and the `id-token` and `attestations` permissions. The ad-hoc "Package the .app as an ad-hoc signed DMG" steps stay as they are.
5. In `publish_release`, delete the "Prepare the release notes" step and go back to `--notes-file RELEASE_NOTES.md`. The `mac_signed` line in the manifest step can stay or go; nothing reads it.
6. Delete `scripts/mac_signing.py`, `packaging/dmg-layout.json` and `tests/invariants/test_mac_signing_outcomes.py`, and the matching exception in `.gitignore`.
7. Delete the secrets `MAC_SIGNING_TOKEN` and `MAC_SIGNING_REPO` and the variable `MAC_SIGNING`.

Each step is safe on its own: what is left after any of them still builds and publishes the ad-hoc Mac builds.
