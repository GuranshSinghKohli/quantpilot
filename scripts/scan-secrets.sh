#!/bin/sh
# Fail if the worktree contains a common secret marker. Does not call a network scanner.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
if grep -R -n -E 'AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY' \
  --binary-files=without-match \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=.venv-py39 \
  --exclude-dir=node_modules \
  --exclude-dir=.terraform \
  --exclude-dir=.next \
  --exclude-dir=.impeccable \
  --exclude=scan-secrets.sh \
  .; then
  echo "secret marker found" >&2
  exit 1
fi
echo "no secret markers"
