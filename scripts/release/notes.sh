#!/usr/bin/env bash
# Part of Chiibitsu-Labs/gates by Angeline S. Viray (Chiibitsu Labs). MIT. https://github.com/Chiibitsu-Labs/gates
#
# Prints one release's notes from CHANGELOG.md, or fails if they are missing or incomplete.
#
#   scripts/release/notes.sh VERSION [CHANGELOG]     VERSION like v1.2.2
#
# Exit 0 and the section body on stdout; exit 1 and the reason on stderr. Used by the
# release workflow to fill the GitHub release, and by selftest.sh to fail a tree whose
# CITATION.cff version has no notes yet, so a release cannot be tagged without them.
set -uo pipefail

ver="${1:-}"
file="${2:-$(cd -- "$(dirname -- "$0")/../.." && pwd -P)/CHANGELOG.md}"
[[ "$ver" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "notes: version must look like v1.2.3, got '$ver'" >&2; exit 1; }
[ -f "$file" ] || { echo "notes: no changelog at $file" >&2; exit 1; }

# The section runs from its `## vX.Y.Z` heading to the next `## ` heading. The version is
# matched as a whole word so v1.2.2 never picks up v1.2.20.
body="$(awk -v v="$ver" '
  /^## / { if (inside) exit; split($0, w, /[ \t]+/); if (w[2] == v) { inside = 1; next } }
  inside { print }
' "$file")"
[ -n "$(printf '%s' "$body" | tr -d '[:space:]')" ] || { echo "notes: CHANGELOG.md has no section for $ver" >&2; exit 1; }

missing=0
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

printf '%s\n' "$body"
