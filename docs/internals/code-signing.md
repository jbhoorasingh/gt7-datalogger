# Code signing policy

This page says how the desktop app's downloads are built and signed, and who decides that
a build is released. It applies from the first desktop release, 1.0.0.

## What is signed

| Platform | Download | Signature |
|---|---|---|
| macOS | `GT7-Datalogger-macOS-arm64.dmg` | Apple Developer ID Application certificate, hardened runtime, notarized by Apple and stapled. Every Mach-O file inside the app is signed, from the inside out, and so is the DMG. |
| Windows | `GT7-Datalogger-Windows-x64-Setup.exe`, `-Portable.zip` | Authenticode with an RFC 3161 timestamp on every `.exe`, `.dll` and `.pyd`, on Setup and on the uninstaller. The certificate provider is named in the release notes of the first signed release. |
| Linux | `GT7-Datalogger-Linux-x86_64.AppImage`, `.tar.gz` | A detached OpenPGP signature (`.asc`) for each file, made with the project's signing subkey. |

Every release also carries `SHA256SUMS.txt`, `release-manifest.json` and GitHub build
provenance attestations, which tie each file to the workflow run and commit that built it.

## How a build gets signed

- Only the `desktop.yml` workflow builds release files, from a `v*.*.*` tag on `main`.
  Nothing built on a developer's machine is ever signed or published.
- The build jobs hold no secrets. Signing runs in separate jobs that install nothing and
  only download what the build jobs produced.
- Signing waits for the `desktop-release` environment's approval. Only the maintainer
  can approve it, and administrators cannot bypass it.
- After signing, fresh machines install each signed file and run the smoke test again.
- The workflow ends with a **draft** release. The maintainer publishes it by hand after
  checking it.

## Roles

| Role | Who |
|---|---|
| Author and committer | the maintainer, [@jbhoorasingh](https://github.com/jbhoorasingh); outside contributions are reviewed by reading before they are merged |
| Reviewer | the maintainer, plus the repository's automated security review on every pull request |
| Approver of a signed release | the maintainer, through the `desktop-release` environment |

## Third-party code

The app bundles Python and its dependencies, pinned with hashes in `desktop/locks/`. Those
files are re-signed as part of the app; their authors' own signatures are not relied on.

## Privacy

What the app sends, where to, and how to turn each request off is on the
[Privacy](../reference/privacy.md) page.
