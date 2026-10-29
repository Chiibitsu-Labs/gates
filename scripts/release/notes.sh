#!/usr/bin/env bash
# Part of Chiibitsu-Labs/gates by Angeline S. Viray (Chiibitsu Labs). MIT. https://github.com/Chiibitsu-Labs/gates
#
# Prints one release's notes from CHANGELOG.md, or fails if they are missing or incomplete.
#
#   scripts/release/notes.sh VERSION [CHANGELOG]           VERSION like v1.2.2
#   scripts/release/notes.sh --title VERSION [CHANGELOG]   prints "VERSION: Headline"
#
# Exit 0 and the section body (or the title) on stdout; exit 1 and the reason on stderr. Used by the
# release workflow to fill the GitHub release, and by selftest.sh to fail a tree whose
# CITATION.cff version has no notes yet, so a release cannot be tagged without them.
set -uo pipefail

mode=body
[ "${1:-}" = --title ] && { mode=title; shift; }
ver="${1:-}"
file="${2:-$(cd -- "$(dirname -- "$0")/../.." && pwd -P)/CHANGELOG.md}"
[[ "$ver" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "notes: version must look like v1.2.3, got '$ver'" >&2; exit 1; }
[ -f "$file" ] || { echo "notes: no changelog at $file" >&2; exit 1; }

# The heading must be exactly `## vX.Y.Z (YYYY-MM-DD): Headline`. The headline is what follows
# the first ": ", and it becomes the GitHub release title.
heading="$(awk -v v="$ver" '/^## / { split($0, w, /[ \t]+/); sub(/:$/, "", w[2]); if (w[2] == v) { print; exit } }' "$file")"
headline=""
re="^## ${ver//./\\.} \\([0-9]{4}-[0-9]{2}-[0-9]{2}\\): (.*[^[:space:]])[[:space:]]*$"
[[ "$heading" =~ $re ]] && headline="${BASH_REMATCH[1]}"

# The section runs from its `## vX.Y.Z` heading to the next `## ` heading. The version is
# matched as a whole word so v1.2.2 never picks up v1.2.20.
body="$(awk -v v="$ver" '
  /^## / { if (inside) exit; split($0, w, /[ \t]+/); sub(/:$/, "", w[2]); if (w[2] == v) { inside = 1; next } }
  inside { print }
' "$file")"
[ -n "$(printf '%s' "$body" | tr -d '[:space:]')" ] || { echo "notes: CHANGELOG.md has no section for $ver" >&2; exit 1; }

missing=0
[ -n "$headline" ] || { echo "notes: $ver heading must be '## $ver (YYYY-MM-DD): Headline', got '$heading'" >&2; missing=1; }
for h in "What changed" "Should I upgrade" "What to pin" "Does anything break"; do
  # The heading must be followed by some text before the next heading, or it answers nothing.
  printf '%s\n' "$body" | awk -v h="### $h" '
    $0 == h { found = 1; next }
    found && /^### / { exit }
    found && NF { text = 1 }
    END { exit !(found && text) }
  ' || { echo "notes: $ver is missing '### $h' or it is empty" >&2; missing=1; }
done
[ "$missing" = 0 ] || exit 1

if [ "$mode" = title ]; then printf '%s: %s\n' "$ver" "$headline"; else printf '%s\n' "$body"; fi
