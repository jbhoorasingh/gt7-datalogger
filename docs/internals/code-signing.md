# Code signing policy

This page says how the desktop app's downloads are built and signed, and who decides that
a build is released. It applies from the first desktop release, 1.0.0.

## What is signed

| Platform | Download | Signature |
|---|---|---|
| macOS | `GT7-Datalogger-macOS-arm64.dmg` | Apple Developer ID Application certificate, hardened runtime, notarized by Apple and stapled. Every Mach-O file inside the app is signed, from the inside out, and so is the DMG. |
| Windows | `GT7-Datalogger-Windows-x64-Setup.exe`, `-Portable.zip` | Authenticode with an RFC 3161 timestamp on every `.exe`, `.dll` and `.pyd`, on Setup and on the uninstaller. The certificate provider is named in the release notes of the first signed release. |
| Linux | `GT7-Datalogger-Linux-x86_64.AppImage`, its `.zsync`, `.tar.gz` | A detached OpenPGP signature (`.asc`) for each file, made with the project's signing subkey ([The Linux signing key](#the-linux-signing-key)). From 1.0.1. |

Every release also carries `SHA256SUMS.txt`, `release-manifest.json` and GitHub build
provenance attestations, which tie each file to the workflow run and commit that built it.

The manifest is signed too: `release-manifest.json.sig` is an Ed25519 signature made with
the project's update key. An installed desktop app checks that signature, and each
file's SHA-256 against the manifest, before it installs an update, on top of the
platform's own signature check ([Desktop app ›
Updating](../getting-started/desktop.md#updating)).

Until Windows signing is set up, the Windows files are unsigned; the release notes say
so. 1.0.0 and 1.0.1 are such releases, and the 1.0.0 Linux files have no `.asc` either:
the Linux key was made after it. Check those downloads against `SHA256SUMS.txt` and the build
provenance attestations. The manifest signature is different: a release candidate may
ship its manifest without one, a release never does.

## The Linux signing key

| | |
|---|---|
| User ID | `GT7 Datalogger release signing` |
| Fingerprint | `01B8 57BB 952C D521 9FA4  0E6F 5E1E 38B8 1FCD DD55` |
| Signing subkey | `59B2 C4A1 E374 2705 3273  6F61 C3B2 8F93 8124 5155`, expires 2028-11-04 |
| Public key | [`gt7-datalogger-release-signing.asc`](../assets/gt7-datalogger-release-signing.asc) |

The main key only certifies: it never signs a download, and the maintainer keeps it
offline. Releases are signed with the subkey, which is the only private part the release
workflow holds. When the subkey is renewed or replaced, the fingerprint above stays the
same and the public key here is updated, so import it again after a change.
[Desktop app › Download](../getting-started/desktop.md#download) shows how to check a
file.

## How a build gets signed

- Only the `desktop.yml` workflow builds release files, from a `v*.*.*` tag on `main`.
  Nothing built on a developer's machine is ever signed or published.
- The build jobs hold no secrets. Signing runs in separate jobs that install nothing and
  only download what the build jobs produced.
- Signing runs only for a `v*.*.*` tag, which only the maintainer can push. The
  signing secrets, the update key among them, live in the `desktop-release`
  environment, which admits only those tags.
- After signing, fresh machines install each signed file and run the smoke test again.
- The workflow ends with a **draft** release. The maintainer publishes it by hand after
  checking it.

## Roles

| Role | Who |
|---|---|
| Author and committer | the maintainer, [@jbhoorasingh](https://github.com/jbhoorasingh); outside contributions are reviewed by reading before they are merged |
| Reviewer | the maintainer, plus the repository's automated security review on every pull request |
| Approver of a signed release | the maintainer, who pushes the tag and publishes the draft release |

## Third-party code

The app bundles Python and its dependencies, pinned with hashes in `desktop/locks/`. Those
files are re-signed as part of the app; their authors' own signatures are not relied on.

## Privacy

What the app sends, where to, and how to turn each request off is on the
[Privacy](../reference/privacy.md) page.
