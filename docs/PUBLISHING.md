# Upload Device Replacer 1.1.2 using Windows Git Bash

Repository: **FortranFour/device-replacer**. This source package is ready for upload; it has not been published to your account. The public user instructions are in `README.md`.

## 1. Download and extract

Save `device_replacer_github_1_1_2.zip` in your Windows **Downloads** folder. Open Git Bash:

```bash
cd ~/Downloads
powershell.exe -NoProfile -Command "Expand-Archive -LiteralPath 'device_replacer_github_1_1_2.zip' -DestinationPath 'device-replacer-upload-1.1.2'"
cd device-replacer-upload-1.1.2/device-replacer
ls -a
```

Expect `README.md`, `hacs.json`, `custom_components`, `scripts`, `tests` and `.github`. Use a fresh extraction directory. If that destination already exists, choose another destination and adjust the `cd` command; keep any existing working Git repository intact.

Upload this **source package**, not the smaller Home Assistant manual-install ZIP. HACS reads the repository's `custom_components/entity_replacer/` directory.

## 2. Check tools and sign-in

```bash
git --version
gh --version
gh auth status
```

Your existing Git credentials can handle pushes. GitHub CLI (`gh`) has its own login for repository creation and workflow monitoring. If `gh` is missing:

```bash
winget.exe install --id GitHub.cli --exact
```

Close and reopen Git Bash after installation and return to the extracted folder. If the CLI is not signed into **FortranFour**:

```bash
gh auth login --hostname github.com --git-protocol https --web
```

If multiple accounts are already configured:

```bash
gh auth switch --hostname github.com --user FortranFour
gh auth status
```

Check your commit identity:

```bash
git var GIT_AUTHOR_IDENT
```

If missing, initialize this extracted folder and set a local identity:

```bash
git init --initial-branch=main
git config user.name "FortranFour"
git config user.email "YOUR_GITHUB_COMMIT_EMAIL"
```

Replace the email placeholder with your commit email or private commit address from **GitHub Settings → Emails**. Do not use the placeholder literally. An existing valid identity needs no change.

## 3. Create an empty public repository

Check whether it exists:

```bash
gh repo view FortranFour/device-replacer
```

If it does not exist and authentication is working:

```bash
gh repo create FortranFour/device-replacer --public --description "Replace Home Assistant device and entity references with reviewed changes, backups and manual reports."
```

Do not add starter README, license or `.gitignore` options; the package supplies these files. If the repository is already empty, continue. If it contains commits, follow **Existing repository** below. The helper refuses conflicting history and does not force-push.

## 4. Upload and tag

From the extracted `device-replacer` folder:

```bash
bash scripts/publish_git_bash.sh
```

The helper reads version `1.1.2`, initializes `main` if needed, verifies the intended remote, commits the files, pushes `main`, and pushes annotated tag `v1.1.2`. It rejects conflicting local or remote release tags before pushing the branch. A clean rerun with the same commit/tag is safe; changed source needs a new version.

Source: https://github.com/FortranFour/device-replacer

Python and Node are supplied by GitHub Actions; they are not required on your PC merely to upload.

## 5. Check the workflows and release

```bash
gh run list --repo FortranFour/device-replacer --limit 10
```

Find the **Release** run for `v1.1.2`. Replace `RUN_ID` with its numeric ID:

```bash
gh run watch RUN_ID --repo FortranFour/device-replacer --exit-status
gh release view v1.1.2 --repo FortranFour/device-replacer
```

Expect `device_replacer_1.1.2.zip` and `SHA256SUMS.txt`. Tests, hassfest and HACS validation must pass before the Release job publishes. The separate Validate workflow also checks main-branch pushes and pull requests. Live hardware behavior still needs testing in Home Assistant.

To inspect a failed run:

```bash
gh run view RUN_ID --repo FortranFour/device-replacer --log-failed
```

Enable GitHub Actions if disabled by your account/repository settings. The release job requests `contents: write` for the built-in `GITHUB_TOKEN`; it requires no personal access token secret. Fix source errors with a new version/tag. For a transient infrastructure failure:

```bash
gh run rerun RUN_ID --repo FortranFour/device-replacer --failed
```

If a push rejects workflow files because the Git authentication token lacks `workflow` scope, update the relevant credential. For GitHub CLI OAuth authentication:

```bash
gh auth refresh --hostname github.com --scopes workflow
gh auth setup-git
```

Then rerun the helper. Git and GitHub CLI credentials may otherwise be separate.

## 6. Verify HACS installation

1. Open **HACS → ⋮ → Custom repositories** in Home Assistant.
2. Add `https://github.com/FortranFour/device-replacer`, type **Integration**.
3. Find **Device Replacer**, download the latest release and restart Home Assistant.
4. New users: **Settings → Devices & services → Add integration → Device Replacer**.
5. Existing Entity Replacer users: keep the existing entry. Device Replacer upgrades it in place, retaining domain `entity_replacer` and its backups.
6. Open the administrator sidebar page. Check the cyan icon, named device lists and Single entity mode.
7. Scan and download a report before applying. Test a small reviewed replacement and restore on a backed-up installation.

Custom-repository installation does not require inclusion in HACS's default catalog. Home Assistant 2026.3+ supports the bundled local brand images; no separate brands submission is needed for this custom integration. Some HACS catalog versions can show a generic icon even when Home Assistant and the panel display the local artwork.

## Existing repository

Start from a new clone to preserve existing history:

```bash
cd ~/Downloads
git clone https://github.com/FortranFour/device-replacer.git device-replacer-existing
cp -R device-replacer-upload-1.1.2/device-replacer/. device-replacer-existing/
cd device-replacer-existing
git status --short
git diff --stat
```

This copies prepared files into a **new clone**, preserving its `.git` directory. Review changes and obsolete files before using the publishing helper. Do not copy Home Assistant configuration into this folder. If `v1.1.2` already points to different source, choose the next unused version and update the fields below. The helper requires `main`; if your existing default branch differs, deliberately select/migrate the branch before publishing.

## Future releases

Work in the existing clone and pull current history before editing. Update:

- `custom_components/entity_replacer/manifest.json`: `version`.
- `custom_components/entity_replacer/const.py`: `VERSION`.
- `custom_components/entity_replacer/frontend/panel.js`: version comment and fallback display.
- `README.md`, `CHANGELOG.md`, `RELEASE_NOTES.md` and versioned publishing examples.

For example, the next release might be `1.1.3`, tagged `v1.1.3`. Build checks require consistent manifest/constants/panel/tag versions. If Python and Node are available, validate locally:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node tests/frontend_unit.cjs
node --check custom_components/entity_replacer/frontend/panel.js
python scripts/build_release.py --check-only
```

Then run `bash scripts/publish_git_bash.sh`. Never move a published tag to changed source.

## Contents and credits

The package contains integration source, synthetic examples/tests, icon/logo assets, documentation, MIT licensing and publishing workflows. It includes no personal entities/devices, Home Assistant configuration exports, reports, credentials or replacement backups. Your Git commit uses the identity you configure locally.

Concept, requirements, design decisions, field testing and maintenance: FortranFour. Substantial implementation, tests, documentation, packaging and icon assistance: OpenAI ChatGPT/Codex. Thanks to the Home Assistant and HACS maintainers and community.

## Official references

- [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create)
- [GitHub CLI login](https://cli.github.com/manual/gh_auth_login)
- [GitHub CLI workflow monitoring](https://cli.github.com/manual/gh_run_watch)
- [HACS integration requirements](https://www.hacs.dev/docs/publish/integration/)
- [HACS custom repositories](https://www.hacs.dev/docs/faq/custom_repositories/)
- [Home Assistant local brand images](https://developers.home-assistant.io/docs/core/integration/brand_images/)
