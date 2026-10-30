# Releasing

How a version of GT7 Datalogger is cut. One tag produces two things: the Docker
images, published straight away, and the desktop app, gathered into a **draft** GitHub
release that the maintainer publishes by hand after testing it.

## Where the version lives

The version is kept in five places in four files: `backend/pyproject.toml`,
`backend/app/_version.py`, `frontend/package.json` and the two root entries of
`frontend/package-lock.json`. `scripts/version.py` reads and writes all five, so
nobody edits them by hand:

```bash
python scripts/version.py --print        # the version, e.g. 0.9.0
python scripts/version.py check          # every place agrees (CI runs this)
python scripts/version.py bump 0.9.0     # rewrite all five places
```

It edits the lock file line by line and never re-serialises it, so npm's formatting
and every other entry stay as they were. It needs only the standard library.

A release candidate exists only in its tag, `vX.Y.Z-rc.N`. The files always hold the
plain `X.Y.Z`. In CI, `scripts/version.py stamp` writes the gitignored
`backend/app/_build_info.py`, which adds `-rc.N` (or `+g<sha>` for a build that is
not from a tag) to the version the running app reports.

## Cutting a release

1. **Bump.** On the release branch, run `python scripts/version.py bump X.Y.Z`.
2. **Roll the changelog.** Move everything under `## [Unreleased]` in `CHANGELOG.md`
   into a new `## [X.Y.Z] - <date>` section, merging duplicate headings, and keep an
   empty `## [Unreleased]` above it. The workflow uses this section as the release
   notes, so check that every merged PR has its line.
3. **Merge.** A big release gets its own *Release X.Y.Z* PR; a small one can carry
   the bump as the last commit of a feature or fix PR. PRs are squash-merged.
4. **Tag.** Put a lightweight tag on the squash commit on `main` and push it:

    ```bash
    git tag vX.Y.Z <sha>
    git push origin vX.Y.Z
    ```

    A tag that disagrees with the files fails the version check, and nothing is
    built.

5. **Approve.** The desktop workflow stops at the `desktop-release` environment and
   waits for the maintainer's approval before anything is signed.
6. **Test the draft.** Download the draft's files on each system and go through the
   release QA issue: install, first start, the setup wizard, a lap recorded from the
   console, a phone connecting, quit.
7. **Publish.** Publish the draft on GitHub. Only then does it become the
   `releases/latest` that the [download links](../getting-started/desktop.md#download)
   and the [update check](../getting-started/configuration.md#what-the-app-fetches-on-its-own)
   see.

Rehearse a desktop release with a release candidate: tag `vX.Y.Z-rc.1` on the same
commit. It runs the same jobs and ends as a draft **prerelease**. Only installs with
**Update channel** set to `prerelease` are offered a published one.

## What the tag starts

The `v*.*.*` tag starts two workflows. They run side by side and do not depend on each
other.

### Docker images: `publish.yml`

`publish.yml` builds the image for `linux/amd64` and `linux/arm64` and pushes it to
`ghcr.io/jbhoorasingh/gt7-datalogger` as `X.Y.Z`, `X.Y` and `X`. It also runs on every
push to `main`, which publishes `main`, `latest` and `sha-<short sha>` for `amd64`
only, since emulating arm64 on every push would triple the build time. A release
candidate tag publishes an `X.Y.Z-rc.N` image too; `X.Y` and `X` never point at a
candidate.

Nothing waits for approval here: the images are public as soon as the job ends.

### Desktop app: `desktop.yml`

1. **Plan.** Reads the tag and runs `scripts/version.py check`, which also checks the
   tag against the files.
2. **Build.** Builds the frontend once, then the app on three runners in parallel:
   macOS arm64, Windows x64 and Linux x86_64 (in a manylinux container, so it runs on
   older distributions). Each leg runs `desktop/scripts/build.py` and then
   `desktop/ci/smoke.py` against the frozen app with an empty data folder: it starts,
   `/api/health` reports the tag's version, `/` serves the dashboard, the database
   is at the latest migration, `/ws/live` answers, a killed server process is
   restarted, `--quit` stops it and `--self-test` passes. These jobs see no secrets.
3. **Gate.** The `desktop-release` environment, which only `v*.*.*` tags may use and
   the maintainer must approve.
4. **Sign.** Signs and notarizes the macOS app and DMG, signs every Windows binary,
   the installer and the uninstaller, and builds and signs the Linux AppImage. These
   jobs install nothing and run no npm.
5. **Verify.** Installs the signed macOS and Windows builds and runs the smoke test
   again.
6. **Release.** Writes `release-manifest.json` and `SHA256SUMS.txt`, attests the
   files, and creates a **draft** release with the version's `CHANGELOG.md` section
   as its notes. It never publishes.

The release files carry no version in their names, so
`https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/<name>`
always fetches the latest published one:

| File | What |
| --- | --- |
| `GT7-Datalogger-macOS-arm64.dmg` | macOS, Apple silicon, signed and notarized |
| `GT7-Datalogger-Windows-x64-Setup.exe` | Windows installer, signed |
| `GT7-Datalogger-Windows-x64-Portable.zip` | Windows, no install |
| `GT7-Datalogger-Linux-x86_64.AppImage` and `.AppImage.zsync` | Linux AppImage, and its delta-update file |
| `GT7-Datalogger-Linux-x86_64.tar.gz` | Linux, the same app as a folder |
| `release-manifest.json` | the list of files |
| `SHA256SUMS.txt` | a checksum per file |

Before the desktop app, the maintainer created each GitHub release by hand with
`gh release create`. The workflow's draft replaces that step.

## Building one locally

[Local development › Building the desktop app](../getting-started/local-development.md#building-the-desktop-app-locally)
builds and smoke-tests an unsigned app for the system you are on, with the same
scripts CI runs.

## Going back

A release whose migrations have run on a database cannot be replaced by an older
one on that database: the older version refuses to start and names the version that
wrote it. Each upgrade copies the database to `backups/pre-upgrade-<time>-<revision>/`
first ([Architecture › Schema evolution](architecture.md#schema-evolution)), so a bad
release is fixed with a new patch release, not by asking users to downgrade.
