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
python scripts/version.py --print        # the version, e.g. 1.0.0
python scripts/version.py check          # every place agrees (CI runs this)
python scripts/version.py bump 1.0.0     # rewrite all five places
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

5. **Test the draft.** The tag is the go-ahead: the desktop workflow signs without
   asking, as Docker publishes. Download the draft's files on each system and go
   through the release QA issue: install, first start, the setup wizard, a lap
   recorded from the console, a phone connecting, quit.
6. **Publish.** Publish the draft on GitHub. Only then does it become the
   `releases/latest` that the [download links](../getting-started/desktop.md#download)
   and the [update check](../getting-started/configuration.md#what-the-app-fetches-on-its-own)
   see, and that installed desktop apps download and
   [install in place](../getting-started/desktop.md#updating).

Rehearse a desktop release with a release candidate: tag `vX.Y.Z-rc.1` on the same
commit. It runs the same jobs and ends as a draft **prerelease**. Only installs with
**Update channel** set to `prerelease` are offered a published one. To try Restart and
update, publish the candidate as a prerelease and let an install of the previous one,
on that channel, update itself to it.

A release can run before Windows signing is set up. Without `WINDOWS_SIGNING`, its
Windows files are unsigned, and without `LINUX_GPG_PRIVATE_KEY` it has no `.asc` files;
the run warns about each, and the release notes should say so. macOS is always signed.
1.0.0 shipped with neither; the Linux key exists since 2026-11-05. The
update key is required: without `UPDATE_SIGNING_KEY` a release candidate has no
`release-manifest.json.sig`, so no install updates itself to it (the run warns), and a
final `vX.Y.Z` tag fails.

## The update key

Installed desktop apps update themselves only to a release whose `release-manifest.json`
carries an Ed25519 signature by a key they were built with
([Desktop app › Updating](../getting-started/desktop.md#updating)). The private key is
the `UPDATE_SIGNING_KEY` secret in the `desktop-release` environment; its public half is
in `UPDATE_KEYS` in `backend/app/self_update.py`. The maintainer makes the key once, from
the main checkout (the script needs the backend's venv) and into a file outside the
repository, so it can never be committed:

```bash
backend/.venv/bin/python desktop/scripts/update_key.py new ~/gt7-update-signing-key.pem
gh secret set UPDATE_SIGNING_KEY --env desktop-release < ~/gt7-update-signing-key.pem
```

The first command prints the public half: add it to `UPDATE_KEYS` and merge that before
the next tag. Keep two offline copies of the `.pem` file, then delete it. The manifest
job refuses to sign with a key whose public half is not in `UPDATE_KEYS`, since no
install would trust what it signed.

To replace the key, add the new public half to `UPDATE_KEYS` and ship a release still
signed with the old key; once installs carry the new one, change the secret and drop the
old public half. A lost key strands nobody's data, but installs built with only that key
can no longer update themselves: their users install the next release by hand, as they
would from Docker.

## The Linux signing key

`sign-linux` signs the AppImage, its `.zsync` and the tarball with a detached `.asc`
each. It needs two things in the `desktop-release` environment: the secret
`LINUX_GPG_PRIVATE_KEY`, the signing subkey with no passphrase (the job signs without a
prompt), and the variable `LINUX_GPG_KEY_ID`, the main key's fingerprint. The key was made
on 2026-11-05 like this, with a main key that only certifies and a signing subkey that
expires after two years:

```bash
gpg --batch --passphrase '' --quick-gen-key "GT7 Datalogger release signing" ed25519 cert never
FPR=$(gpg --list-keys --with-colons "GT7 Datalogger release signing" | awk -F: '/^fpr/{print $10; exit}')
gpg --batch --passphrase '' --quick-add-key "$FPR" ed25519 sign 2y
gpg --armor --export-secret-subkeys "$FPR" > ~/gt7-linux-signing-subkey.asc
gpg --armor --export-secret-keys "$FPR" > ~/gt7-linux-signing-primary.asc
gpg --armor --export "$FPR" > ~/gt7-linux-signing-public.asc
gh secret set LINUX_GPG_PRIVATE_KEY --env desktop-release < ~/gt7-linux-signing-subkey.asc
gh variable set LINUX_GPG_KEY_ID --env desktop-release --body "$FPR"
```

`gt7-linux-signing-primary.asc` is the backup of the whole key: keep it offline, with the
update key. The public half is
[`docs/assets/gt7-datalogger-release-signing.asc`](../assets/gt7-datalogger-release-signing.asc),
and its fingerprint is on [Code signing](code-signing.md#the-linux-signing-key).

The subkey expires on 2028-11-04, and a run with an expired subkey fails to sign.
Before then, import the backup into gpg and extend the subkey, then publish the result:

```bash
gpg --import ~/gt7-linux-signing-primary.asc
FPR=01B857BB952CD5219FA40E6F5E1E38B81FCDDD55
SUB=$(gpg --list-keys --with-colons "$FPR" | awk -F: '/^sub/{s=1; next} s && /^fpr/{print $10; exit}')
gpg --quick-set-expire "$FPR" 2y "$SUB"
```

Then export the subkey and the public key again as above, set the secret again, and
replace `docs/assets/gt7-datalogger-release-signing.asc` in a pull request. If the
subkey leaks, revoke it (`gpg --edit-key "$FPR"`, `key 1`, `revkey`), add a new one with
`--quick-add-key`, and publish the same way: the fingerprint users check stays the same.

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
3. **Gate.** Only a `v*.*.*` tag goes on to signing. The signing jobs run in the
   `desktop-release` environment, which holds the signing secrets and admits only
   those tags.
4. **Sign.** Signs and notarizes the macOS app and DMG, signs every Windows binary,
   the installer and the uninstaller, and builds and signs the Linux AppImage. These
   jobs install nothing and run no npm.
5. **Verify.** Installs the signed macOS and Windows builds and runs the smoke test
   again, then updates each install in place with the signed file
   (`desktop/ci/update_smoke.py`): the same install step Restart and update runs, and
   the new copy must start by itself. The Linux build jobs do the same with the
   AppImage, on every run.
6. **Manifest.** Writes `release-manifest.json` and `SHA256SUMS.txt`, checks that every
   file in `desktop/release-assets.txt` is there, and signs the manifest with
   `UPDATE_SIGNING_KEY` into `release-manifest.json.sig` ([The update
   key](#the-update-key)). It runs in the `desktop-release` environment, so the job
   that publishes holds no key.
7. **Release.** Attests the files and creates a **draft** release with the version's
   `CHANGELOG.md` section as its notes. It never publishes.

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
| `release-manifest.json` | the list of files, with each one's size and SHA-256 |
| `release-manifest.json.sig` | the manifest's Ed25519 signature, which installed apps check before updating |
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
