# Changelog

One section per release, newest first. Each section is copied word for word into the
GitHub release when its tag is pushed (`.github/workflows/release.yml`), and the workflow
adds the commit SHA to pin, which this file cannot hold: the file lives in the commit being
tagged, and a commit cannot contain its own hash.

Write each paragraph or bullet on one line: GitHub shows every line break in a release as a
real break.

Every section answers the same four questions, under these exact headings, and
`selftest.sh` fails if the version in `CITATION.cff` has no section or a section is
missing one:

- **What changed**: in plain words.
- **Should I upgrade**: yes, no, or only if something applies to you.
- **What to pin**: which version, and where the SHA comes from.
- **Does anything break**: what a caller has to change, or "nothing".

The full history, including releases before this file existed, is the Releases table in
`README.md`. That table stays the authority on which versions are safe to use.

## v1.2.2 (2026-10-28)

### What changed
- The toolkit moved from `chiibitsu/gates` to `Chiibitsu-Labs/gates`. The shared workflow now checks itself out under the new name.
- The npm-audit gate is removed. It was never in a release; dependency auditing lives in `Chiibitsu-Labs/security-checks`, so security has one home.
- The gates themselves are otherwise identical to v1.2.1.

### Should I upgrade
Yes. Every caller should name the toolkit at its new address.

### What to pin
v1.2.2. Pin the commit SHA shown at the bottom of this release, never the tag name.

### Does anything break
Callers must change `chiibitsu/gates` to `Chiibitsu-Labs/gates` in their `uses:` lines. GitHub Actions does not follow a renamed repository for `uses:`, so the old name stops working. Nothing else changes.
