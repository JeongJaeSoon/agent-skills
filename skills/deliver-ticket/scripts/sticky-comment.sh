#!/usr/bin/env bash
# Upsert a sticky comment on a PR, identified by an HTML marker.
#
# Finds an existing comment containing the marker and PATCHes it; posts a new
# one only when none exists. Result comments therefore never stack up.
#
#   usage: sticky-comment.sh <pr-number> <body-file> [marker]
#
# Run from inside the repository the PR belongs to.
set -euo pipefail

PR="$1"
BODY_FILE="$2"
MARKER="${3:-<!-- test-results -->}"
REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

BODY="$(printf '%s\n\n%s' "$MARKER" "$(cat "$BODY_FILE")")"

EXISTING=$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
  --jq "[.[] | select(.body | contains(\"$MARKER\"))] | first | .id // empty")

if [ -n "$EXISTING" ]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$EXISTING" -f body="$BODY" --jq '.html_url' \
    | sed 's/^/updated: /'
else
  gh api -X POST "repos/$REPO/issues/$PR/comments" -f body="$BODY" --jq '.html_url' \
    | sed 's/^/created: /'
fi
