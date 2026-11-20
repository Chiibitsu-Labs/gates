# CLAUDE.md — gates

## What this is
Deterministic CI gates that must prove they can fail before they count. The toolkit every
other Chiibitsu Labs repo calls. See `README.md`.

**Active scope** (vibeOS docs/06 registry): `<scope-id>`

## Stack & commands
- Stack: shell gates, a reusable GitHub Actions workflow, no npm packages.
- Test: `./selftest.sh .`

## Standing orders
This repo has no standing orders of its own. It follows `templates/claude-md.md` in
`chiibitsu/vibeOS`, at vibeOS's published head, including its "Credit on GitHub" section.
Read that file; it is not copied here.

## Repo-specific gotchas
- This repo is public: commits and PRs carry no Claude session link (sessionUrl false).
