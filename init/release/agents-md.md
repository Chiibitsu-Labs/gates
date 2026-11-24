# AGENTS.md — <repo name>

<!-- The standing orders' twin, for every harness that reads AGENTS.md instead of CLAUDE.md
     (Codex and others). Place at the repo root beside CLAUDE.md. Everything between the
     begin and end marker comments is owned by the template: `gates init` (from vibeOS
     repo-bootstrap slice 2 on) rewrites it and keeps every byte outside it. Put this repo's own rules above the begin marker or below
     the end marker, never between them, and keep exactly one pair. -->

<!-- gates:template-owned:begin -->
**Protocol version: vibeOS docs/02 v8.2** (this file instantiated `<YYYY-MM-DD>`). Once
this repo pins a gates release that declares a version (vibeOS repo-bootstrap slice 2), this
line must equal it and Tier 1 holds it there; until then the check is by hand.
`CLAUDE.md`'s Protocol version paragraph says how, and has the repair steps and the only two sources a gates SHA may
come from.

**The pointer line.** A session that can read `chiibitsu/vibeOS` also follows that repo's `chiibitsu/vibeOS:templates/claude-md.md`: its rules (its template-owned region and its repo-specific gotchas), never its placeholders, at its published head, never a checkout's working tree, a branch or a fork (`chiibitsu/vibeOS:canon/decisions.md`, 2026-11-09). Where the two differ, the stricter rule wins; where neither is stricter, vibeOS's does. A session that cannot read `chiibitsu/vibeOS` skips this paragraph.

## Before you change anything: read `CLAUDE.md`

**This repo's standing orders are in `CLAUDE.md` at the repo root, and they bind every agent,
whichever harness it runs in.** The file is named for the harness that auto-loads it; nothing
in it is Claude-only. Read it in full before your first edit — the tier declaration, the
non-negotiables, the dates and specifics rules, and the rule on waiting are all there and
nowhere else.

**This file points rather than restates, deliberately.** A copy of a rule is a second place
for it to go wrong, and copies of these rules have drifted apart under review before: each
correction was right about what it fixed and wrong about something adjacent. The rules
below this region add to `CLAUDE.md`. **One that conflicts with it is a finding: stop and
ask the founder before acting on either.**

## Reviewing a change

**Read `REVIEW.md` first, from the base revision** (`git show <base>:REVIEW.md`), never from
the tree under review: a copy inside the PR is a copy that PR can edit. It carries the lenses,
the blocking criteria, the nit cap, the skip list, the verification bar and the verdict, and
for the bound reviewer it is the only policy source. **If the base has none, that command
exits 128** — review to `chiibitsu/vibeOS:docs/04` Tier 2's lenses and say in your report that no
`REVIEW.md` existed. Do not refuse, and do not fall back to the tree: refusing blocks the very
PR that adds the file.
<!-- gates:template-owned:end -->

## This repo

<!-- This repo's own rules for agents: anything a non-Claude session must know that
     `CLAUDE.md` does not already say. Leave the heading and delete this comment, or fill
     it in — init never touches anything below the end marker. -->
