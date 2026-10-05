#!/usr/bin/env bash
# Run from Git Bash after creating the empty public GitHub repository.
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_directory/.."

repository="FortranFour/device-replacer"
repository_url="https://github.com/$repository.git"
manifest="custom_components/entity_replacer/manifest.json"
version="$(sed -nE 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*,?[[:space:]]*$/\1/p' "$manifest")"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf '%s\n' 'Could not read the integration version; no files were published.' >&2
  exit 1
fi
tag="v$version"

if ! git ls-remote "$repository_url" >/dev/null; then
  printf '%s\n' \
    'Create the empty PUBLIC repository FortranFour/device-replacer, then run this script again.' \
    'Do not initialize it with a README, license, or .gitignore.' \
    'GitHub: https://github.com/new' >&2
  exit 1
fi

if [[ ! -d .git ]]; then
  # Do not adopt a parent directory's repository.
  git init --initial-branch=main
fi
if [[ "$(git symbolic-ref --short HEAD)" != main ]]; then
  printf '%s\n' 'Run this publishing helper from the main branch.' >&2
  exit 1
fi

if ! git var GIT_AUTHOR_IDENT >/dev/null 2>&1; then
  printf '%s\n' \
    'Set your Git commit name and email, then run the script again:' \
    '  git config user.name "FortranFour"' \
    '  git config user.email "YOUR_GITHUB_COMMIT_EMAIL"' \
    'You can copy your private commit email from GitHub Settings > Emails.' >&2
  exit 1
fi

if git remote get-url origin >/dev/null 2>&1; then
  # Inspect the configured URL before Git applies a user's URL rewrites.
  origin_url="$(git config --get remote.origin.url)"
  if [[ "$origin_url" != "$repository_url" && "$origin_url" != "https://github.com/$repository" && "$origin_url" != "git@github.com:$repository.git" ]]; then
    printf '%s\n' "origin points to a different repository: $origin_url" >&2
    exit 1
  fi
else
  git remote add origin "$repository_url"
fi

if [[ -n "$(git ls-remote --heads origin main)" ]]; then
  git fetch --quiet origin main
  if ! git rev-parse --verify HEAD >/dev/null 2>&1 || ! git merge-base --is-ancestor FETCH_HEAD HEAD; then
    printf '%s\n' \
      'The remote main branch contains commits not present in this folder.' \
      'Use an empty repository for the first upload, or work from your existing clone.' \
      'No remote commits have been overwritten.' >&2
    exit 1
  fi
fi

git add --all
if ! git diff --cached --quiet; then
  git commit -m "Publish Device Replacer $version for HACS"
fi

if git rev-parse --verify "refs/tags/$tag" >/dev/null 2>&1; then
  if [[ "$(git rev-list -n 1 "$tag")" != "$(git rev-parse HEAD)" ]]; then
    printf '%s\n' "Tag $tag already refers to a different commit; bump the version before publishing again." >&2
    exit 1
  fi
fi
# Reject a conflicting release tag before pushing the main branch.
remote_tag_refs="$(git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")"
if [[ -n "$remote_tag_refs" ]]; then
  remote_tag_commit="$(printf '%s\n' "$remote_tag_refs" | awk 'NR == 1 { commit=$1 } /\^\{\}$/ { commit=$1 } END { print commit }')"
  if [[ "$remote_tag_commit" != "$(git rev-parse HEAD)" ]]; then
    printf '%s\n' "Remote tag $tag points to another commit; bump the version before publishing again." >&2
    exit 1
  fi
fi
git push --set-upstream origin main
if ! git rev-parse --verify "refs/tags/$tag" >/dev/null 2>&1; then
  git tag --annotate "$tag" --message "Device Replacer $version"
fi
git push origin "refs/tags/$tag"

printf '%s\n' \
  "Uploaded: https://github.com/$repository" \
  "The $tag tag starts the Release workflow. Check its result here:" \
  "https://github.com/$repository/actions" \
  'Then add the repository in HACS as an Integration.'
