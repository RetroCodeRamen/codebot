#!/usr/bin/env bash
# Maintainer tool: export the current commit (tracked files only) into a NEW folder as a fresh git repository with a single commit, for
# publishing without the development history. This repository is not changed. Refuses to run with uncommitted changes or an existing target.
#   scripts/make-public-snapshot.sh <target-dir> ["commit message"]
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
target="${1:?usage: scripts/make-public-snapshot.sh <target-dir> [message]}"; msg="${2:-codebot: initial public release}"
[[ -e "$target" ]] && { echo "refusing: $target already exists"; exit 1; }
[[ -z "$(git -C "$HERE" status --porcelain --untracked-files=no)" ]] || { echo "refusing: commit or stash your changes first"; exit 1; }
# a last check for things that must never be published (the repo's own scans in tests/ cover the tree; this guards the snapshot itself)
if git -C "$HERE" grep -nE "codebot-tahlokffwnfdc7|virtualpixelboy|BEGIN (RSA|OPENSSH) PRIVATE KEY" HEAD -- . ':!scripts/make-public-snapshot.sh' >/dev/null; then
  echo "refusing: a private identifier is still in the tracked files:"; git -C "$HERE" grep -nE "codebot-tahlokffwnfdc7|virtualpixelboy|BEGIN (RSA|OPENSSH) PRIVATE KEY" HEAD -- . ':!scripts/make-public-snapshot.sh' | head; exit 1
fi
mkdir -p "$target"
git -C "$HERE" archive HEAD | tar -x -C "$target"
cd "$target"
git init -q -b main
git add -A
git -c user.name="$(git -C "$HERE" config user.name)" -c user.email="$(git -C "$HERE" config user.email)" commit -q -m "$msg"
echo "snapshot of $(git -C "$HERE" rev-parse --short HEAD) in $target: $(git ls-files | wc -l) files, one commit by $(git log -1 --format='%an <%ae>')"
echo "next: create an empty repository on GitHub, then: cd $target && git remote add origin <url> && git push -u origin main"
