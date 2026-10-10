# Publish Device Replacer 1.1.3 from Git Bash

This update hides the panel header hamburger button on desktop/wide layouts and retains it on Home Assistant mobile/narrow layouts. Home Assistant controls the layout state, including resizing. Replacement capabilities and backups remain unchanged.

1. Save `device_replacer_update_1_1_3.zip` in Windows Downloads.
2. Open Git Bash and run the following. It creates a new clone, so your earlier upload directory remains intact. Use another folder name if `device-replacer-publish-1.1.3` already exists.

```bash
cd ~/Downloads
git clone --branch main https://github.com/FortranFour/device-replacer.git device-replacer-publish-1.1.3
cd device-replacer-publish-1.1.3
git pull --ff-only
unzip ../device_replacer_update_1_1_3.zip
```

The ZIP contains a patch for the changed runtime files and release documents, without a `.git` directory or Home Assistant configuration. It is intended for an unmodified 1.1.2 release. Extraction should show no overwrite prompt because its only entry is a new patch file.

3. Check the patch applies, then apply it:

```bash
git apply --check device_replacer_1.1.3.patch && git apply device_replacer_1.1.3.patch && rm device_replacer_1.1.3.patch && git diff --check
git diff --stat
```

If `git apply --check` fails, stop and share the output; do not force the patch or continue with publishing. It may mean the repository already has newer changes.

4. Keep the repository topics required by HACS, then publish with the existing helper:

```bash
gh repo edit FortranFour/device-replacer --add-topic home-assistant --add-topic hacs --add-topic custom-integration
bash scripts/publish_git_bash.sh
```

The helper commits the changes, pushes main, and creates annotated tag `v1.1.3`. No Python/Node installation is needed on your PC just to publish. It refuses divergent remote history or conflicting release tags.

5. Watch the release. This command lets you select the new run interactively, avoiding a placeholder run ID:

```bash
gh run watch --repo FortranFour/device-replacer --exit-status
```

Choose the **Release** run for `v1.1.3`. When it succeeds:

```bash
gh release view v1.1.3 --repo FortranFour/device-replacer
```

If it fails, use `gh run list --repo FortranFour/device-replacer --limit 5` to obtain its numeric ID, then `gh run view NUMBER --repo FortranFour/device-replacer --log-failed`, replacing NUMBER with that actual ID.

6. Open HACS → Device Replacer and update to 1.1.3 (refresh/check updates if needed). Restart Home Assistant, then refresh your browser or close/reopen the mobile app. Keep the existing integration entry and backup directory. The panel shows version 1.1.3.

Verification: automated Python and frontend logic checks, JavaScript syntax, release version consistency, and wide/narrow property/renderer behavior are checked locally. Full live Home Assistant rendering still needs verification on your desktop and phone. GitHub runs HACS/hassfest checks before publishing.
