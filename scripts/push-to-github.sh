#!/bin/bash
# Push QuantPilot to https://github.com/GuranshSinghKohli/quantpilot
set -e
cd "$(dirname "$0")/.."

echo "=== QuantPilot → GitHub push ==="
echo "Remote: $(git remote get-url origin)"
echo "Local commits:"
git log --oneline -3
echo ""

if ! git remote get-url origin | grep -q "GuranshSinghKohli/quantpilot"; then
  git remote remove origin 2>/dev/null || true
  git remote add origin https://github.com/GuranshSinghKohli/quantpilot.git
fi

echo "Pushing to GitHub (sign in as GuranshSinghKohli when prompted)..."
echo "Password = Personal Access Token: https://github.com/settings/tokens"
echo ""

git push --force-with-lease origin main

echo ""
echo "Done! Check: https://github.com/GuranshSinghKohli/quantpilot"
