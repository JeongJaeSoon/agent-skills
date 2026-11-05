#!/usr/bin/env bash
# Upsert a sticky comment on a PR, identified by an HTML marker.
#
# Finds an existing comment containing the marker and PATCHes it; posts a new
# one only when none exists. Result comments therefore never stack up.
#
#   usage: sticky-comment.sh <pr-number> <body-file> [marker]
#          sticky-comment.sh --check <pr-number> [marker]
#
# --check writes nothing: exit 0 when the marker comment names the PR's current
# head sha (its first 7 characters or more), 1 when it is missing or stale.
#
# Run from inside the repository the PR belongs to.
set -euo pipefail

CHECK=
if [ "${1:-}" = "--check" ]; then
  CHECK=1
  shift
fi
PR="${1:?usage: sticky-comment.sh [--check] <pr-number> [<body-file>] [marker]}"
if [ -n "$CHECK" ]; then
  MARKER="${2:-<!-- test-results -->}"
else
  BODY_FILE="${2:?usage: sticky-comment.sh <pr-number> <body-file> [marker]}"
  MARKER="${3:-<!-- test-results -->}"
fi
REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

# --paginate applies --jq per page, so keep the first matching id across all pages.
IDS=$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
  --jq ".[] | select(.body | contains(\"$MARKER\")) | .id")
EXISTING="${IDS%%$'\n'*}"

if [ -n "$CHECK" ]; then
  HEAD="$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)"
  [ -n "$HEAD" ] || { echo "no head sha for #$PR" >&2; exit 1; }
  [ -n "$EXISTING" ] || { echo "missing: #$PR has no $MARKER comment" >&2; exit 1; }
  BODY="$(gh api "repos/$REPO/issues/comments/$EXISTING" --jq .body)"
  case "$BODY" in
    *"${HEAD:0:7}"*) echo "ok: the $MARKER comment on #$PR names head ${HEAD:0:8}" ;;
    *) echo "stale: the $MARKER comment on #$PR does not name head ${HEAD:0:8}; rerun at this head and update it" >&2
       exit 1 ;;
  esac
  exit 0
fi

# A plain assignment, so an unreadable body file stops the script before any write.
CONTENT="$(cat "$BODY_FILE")"
[ -n "$CONTENT" ] || { echo "empty body file: $BODY_FILE" >&2; exit 1; }
BODY="$(printf '%s\n\n%s' "$MARKER" "$CONTENT")"

if [ -n "$EXISTING" ]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$EXISTING" -f body="$BODY" --jq '.html_url' \
    | sed 's/^/updated: /'
else
  gh api -X POST "repos/$REPO/issues/$PR/comments" -f body="$BODY" --jq '.html_url' \
    | sed 's/^/created: /'
fi
