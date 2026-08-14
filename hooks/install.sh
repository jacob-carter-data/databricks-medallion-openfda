#!/usr/bin/env bash
#
# Installs the guard hooks into .git/hooks/.
#
# WHY THIS SCRIPT EXISTS
#   Git hooks live in .git/hooks/, which is not part of the repository. It is not
#   committed, not cloned, and destroyed by a re-init that deletes .git. The
#   master copies are tracked here, in the working tree, where nothing that
#   happens to .git can reach them.
#
#   RUN THIS AFTER CLONING, AND IMMEDIATELY AFTER ANY `git init`, BEFORE THE
#   FIRST `git add`.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"

[[ -d "${REPO}/.git" ]] || { echo "[FAIL] ${REPO} is not a git repository" >&2; exit 1; }

mkdir -p "${REPO}/.git/hooks"
for h in pre-commit pre-push; do
  cp "${HERE}/${h}" "${REPO}/.git/hooks/${h}"
  chmod +x "${REPO}/.git/hooks/${h}"
  echo "[ok]   installed ${h}"
done

# --- prove they are live, do not assume -------------------------------------
# An installed-but-not-working hook is worse than no hook, because it is
# believed. The decoy below is an obviously fake AWS key id: it matches the
# credential pattern, so a working hook must refuse it.
#
# It is assembled at runtime rather than written literally, because a literal
# would make THIS file match the pattern and the hook would block its own
# installer. That is not hypothetical -- it happened, immediately after the same
# mistake was made one layer up in the hook itself.
cd "$REPO"

TESTFILE=".hook-selftest"
CLEANUP() {
  git restore --staged "$TESTFILE" 2>/dev/null || true
  rm -f "$TESTFILE"
}
trap CLEANUP EXIT

DECOY="AKI""A$(printf 'A%.0s' $(seq 1 16))"
echo "$DECOY" > "$TESTFILE"
git add -f "$TESTFILE" 2>/dev/null

if .git/hooks/pre-commit >/dev/null 2>&1; then
  echo "[FAIL] pre-commit did NOT block a staged credential -- hooks are not working" >&2
  exit 1
else
  echo "[ok]   verified: pre-commit blocks staged credentials"
fi

echo
echo "[ok]   hooks installed and verified in ${REPO}/.git/hooks/"
