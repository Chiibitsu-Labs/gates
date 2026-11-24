#!/usr/bin/env python3
# Part of Chiibitsu-Labs/gates by Angeline S. Viray (Chiibitsu Labs). MIT. https://github.com/Chiibitsu-Labs/gates
"""gates init, the standing-orders version check, and the team-safe copy's derivation.

    gates_init.py init   TARGET --sha SHA [--adopt] [--live-standing-orders]
    gates_init.py check  TARGET [--live-standing-orders]
    gates_init.py derive VIBEOS_CHECKOUT
    gates_init.py --selftest

WHAT INIT WRITES. `CLAUDE.md`, `AGENTS.md`, `REVIEW.md` and the three callers
(`.github/workflows/tier1.yml`, `review.yml`, `security.yml`), from the copies in
`init/release/`. One more kind of file can change: an existing workflow that already calls
this toolkit by `uses:` has its gates pins moved, and nothing else in it. init never touches
a `.gitignore`, a hook, a README or a board, never writes through a symlink, and never
commits or pushes: a session reads the diff, runs the gates and opens the PR.

STANDING-ORDERS FILES. Each holds one template-owned region between the two marker lines
below. init rewrites the region and keeps every byte outside it. A file with no region, more
than one marker of either kind, the end before the begin, a marker inside a fenced block, or
a marker that is not alone on its line is reported and left untouched. `--adopt` is the one
exception, for a file with no marker at all: it inserts the region after the file's first
`# ` heading (after any frontmatter when there is no heading) and leaves every existing byte
where it was, so the stale text it duplicates shows up in the diff.

WHEN A PIN MOVES. init moves a gates pin to the SHA of the checkout it runs from, and only
forward: an existing pin moves only if that checkout's history contains it. A pin it cannot
place (newer, unknown, on another line of history, or not a SHA) stops the whole run with
nothing written. The security caller is written only when the repo has none and is never
rewritten.

ALL OR NOTHING. Every file is planned first. Any problem in any file stops the run before
the first write, because a half-applied init leaves the three markers split.

THE CHECK asserts the markers and the pins, never the body: red, with expected, found and
the paragraph to read, for a missing file among the three, a missing or duplicated marker, a
marker behind or ahead of the release, the files split between versions, a missing gates,
gates_ref or review pin, a pin that is not a SHA, or the pins disagreeing. In a repo that
reads the standing orders live, its CLAUDE.md and AGENTS.md must exist and carry no marker
and no region. It edits nothing.
"""
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(HERE)
RELEASE = os.path.join(HERE, "release")

BEGIN = "<!-- gates:template-owned:begin -->"
END = "<!-- gates:template-owned:end -->"
MARKER_KEY = "gates:template-owned:"

# The marker line: `**Protocol version: vibeOS docs/02 v8.2** (this file instantiated `<date>`)`.
MARKER = re.compile(r"\*\*Protocol version: vibeOS docs/02 v(\d+)\.(\d+)(?:\.(\d+))?\*\*")
# Any line that looks like an attempt at one, so a mangled or older-style copy counts as a
# marker that is there and wrong rather than as no marker at all.
MARKER_LOOSE = re.compile(r"\*\*Protocol version:")
DATE = re.compile(r"(\(this file instantiated `)(<YYYY-MM-DD>|\d{4}-\d{2}-\d{2})(`\))")

STANDING = (  # (path in the repo, file in init/release)
    ("CLAUDE.md", "claude-md.md"),
    ("AGENTS.md", "agents-md.md"),
    ("REVIEW.md", "review-md.md"),
)
LIVE_EXEMPT = ("CLAUDE.md", "AGENTS.md")  # a repo that reads the standing orders live
WF = ".github/workflows"
TIER1 = (WF + "/tier1.yml", "tier1-ci.yml")
REVIEWC = (WF + "/review.yml", "review-ci.yml")
SECURITY = (WF + "/security.yml", "security-ci.yml")

SHA40 = re.compile(r"[0-9a-f]{40}")
# Any ref, so a tag or branch pin is SEEN (and refused) rather than invisible to the check.
GATES_USES = re.compile(r"((?i:chiibitsu-labs/gates)/\.github/workflows/(gates|review)\.ya?ml@)"
                        r"([^\s#'\"]+)([ \t]*#[ \t]*v[0-9][0-9.]*)?")
GATES_REF = re.compile(r"(gates_ref:[ \t]*['\"]?)([^\s#'\"]+)(['\"]?)([ \t]*#[ \t]*v[0-9][0-9.]*)?")
LIVE_INPUT = re.compile(r"^[ \t]*standing_orders_live:", re.M)
# The toolkit's name before the move to the org. GitHub does not follow a transfer redirect
# for `uses:`, so a caller still spelled this way is broken, and init will not guess at it.
OLD_NAME = re.compile(r"(?<![\w-])chiibitsu/gates/\.github/workflows/", re.I)
SECURITY_USES = re.compile(r"(?i:chiibitsu-labs/security-checks)/")

POINTER = "the Protocol version paragraph in CLAUDE.md"
POINTER_RELEASE = ("the Protocol version paragraph of init/release/claude-md.md in "
                   "Chiibitsu-Labs/gates at the SHA this repo pins")


class Problem(Exception):
    pass


# ── regions ────────────────────────────────────────────────────────────────────────────

def parse_region(text):
    """Return (begin_line, end_line) indexes of the one well-formed pair, or raise Problem
    naming the malformed shape. Line-based; a fence is a line opening with ``` or ~~~."""
    lines = text.split("\n")
    begins, ends, fence = [], [], None
    for i, line in enumerate(lines):
        s = line.strip()
        if fence is None and (s.startswith("```") or s.startswith("~~~")):
            fence = s[:3]
            if MARKER_KEY in line:
                raise Problem("a region marker sits on a fence line (line %d)" % (i + 1))
            continue
        if fence is not None:
            if s.startswith(fence):
                fence = None
            elif MARKER_KEY in line:
                raise Problem("a region marker sits inside a fenced block (line %d)" % (i + 1))
            continue
        if s == BEGIN:
            begins.append(i)
        elif s == END:
            ends.append(i)
        elif MARKER_KEY in line:
            raise Problem("a region marker is not alone on its line (line %d)" % (i + 1))
    if not begins and not ends:
        raise Problem("no template-owned region")
    if len(begins) > 1 or len(ends) > 1:
        raise Problem("more than one region marker pair (%d begin, %d end)" % (len(begins), len(ends)))
    if len(begins) != len(ends):
        raise Problem("an unpaired region marker")
    if ends[0] < begins[0]:
        raise Problem("the end marker comes before the begin marker")
    return begins[0], ends[0]


def has_any_marker(text):
    return MARKER_KEY in text


def region_body(text):
    b, e = parse_region(text)
    return "\n".join(text.split("\n")[b + 1:e])


def splice(text, body):
    """Replace the region's body, keeping the file's own line ending: a CRLF file gets its
    new region in CRLF too."""
    b, e = parse_region(text)
    lines = text.split("\n")
    new = body.split("\n")
    if lines[b].endswith("\r"):
        new = [l + "\r" for l in new]
    return "\n".join(lines[:b + 1] + new + lines[e:])


def undate(text):
    return DATE.sub(lambda m: m.group(1) + "<YYYY-MM-DD>" + m.group(3), text)


def date_marker(text, today):
    return DATE.sub(lambda m: m.group(1) + today + m.group(3), text, count=1)


def markers(text):
    """Every marker-shaped line, as (line_no, version tuple or None for a mangled one)."""
    out = []
    for i, line in enumerate(text.split("\n")):
        if MARKER_LOOSE.search(line):
            m = MARKER.search(line)
            out.append((i + 1, tuple(int(x or 0) for x in m.groups()) if m else None))
    return out


def vstr(v):
    return "v%d.%d" % v[:2] + (".%d" % v[2] if v[2] else "")


def release_version(release=RELEASE):
    with open(os.path.join(release, "PROTOCOL"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"\s*protocol\s+v(\d+)\.(\d+)(?:\.(\d+))?\s*$", line)
            if m:
                return tuple(int(x or 0) for x in m.groups())
    raise Problem("init/release/PROTOCOL declares no `protocol vX.Y` line")


# ── paths ──────────────────────────────────────────────────────────────────────────────

def guard(target, rel):
    """Refuse a path init would write if it, or any directory between it and the target,
    is a symlink. A link could send the write outside the repo, or onto another of the six
    files (`CLAUDE.md -> AGENTS.md` flips both on every run)."""
    p = target
    for part in rel.split("/"):
        p = os.path.join(p, part)
        if os.path.islink(p):
            raise Problem("%s: %s is a symlink; init never writes through one. Left untouched."
                          % (rel, os.path.relpath(p, target)))
    return os.path.join(target, rel)


# ── pins ───────────────────────────────────────────────────────────────────────────────

def workflow_files(target):
    d = os.path.join(target, WF)
    if not os.path.isdir(d):
        return []
    return sorted(WF + "/" + n for n in os.listdir(d)
                  if n.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(d, n)))


def pins_in(text):
    """Every gates pin in a workflow that calls the toolkit: (kind, ref), kind 'gates',
    'review' or 'gates_ref'. A `gates_ref:` key in a workflow that does not call the toolkit
    by `uses:` is someone else's and is not counted."""
    uses = [(m.group(2), m.group(3)) for m in GATES_USES.finditer(text)]
    if not uses:
        return []
    return uses + [("gates_ref", m.group(2)) for m in GATES_REF.finditer(text)]


def is_ancestor(history, old, new):
    """True when `old` is `new` or an ancestor of it in the git repo at `history`."""
    if old == new:
        return True
    if subprocess.run(["git", "-C", history, "cat-file", "-e", old + "^{commit}"],
                      capture_output=True).returncode != 0:
        return False
    return subprocess.run(["git", "-C", history, "merge-base", "--is-ancestor", old, new],
                          capture_output=True).returncode == 0


def move_pins(text, sha, uses_comment, ref_comment):
    if not GATES_USES.search(text):
        return text
    text = GATES_USES.sub(lambda m: "Chiibitsu-Labs/gates/.github/workflows/%s.yml@%s%s"
                          % (m.group(2), sha, uses_comment), text)
    return GATES_REF.sub(lambda m: m.group(1) + sha + m.group(3) + ref_comment, text)


def add_live_input(text):
    """Put `standing_orders_live: true` under the gates job's `with:`, beside gates_ref."""
    if LIVE_INPUT.search(text) or not GATES_REF.search(text):
        return text
    return re.sub(r"^([ \t]*)(gates_ref:[^\n]*)$", lambda m: "%s%s\n%sstanding_orders_live: true"
                  % (m.group(1), m.group(2), m.group(1)), text, count=1, flags=re.M)


def comment_of(text, regex, group):
    m = regex.search(text)
    if not m or not m.group(group):
        raise Problem("the release's caller has no version comment beside its pin")
    return m.group(group)


# ── init ───────────────────────────────────────────────────────────────────────────────

def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def adopt_point(lines):
    """Where --adopt puts the region: after the first `# ` heading; with none, after a
    leading frontmatter block; with neither, at the top."""
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    for i in range(start, len(lines)):
        if lines[i].startswith("# "):
            return i + 1
    return start


def plan(target, sha, today, history, release=RELEASE, adopt=False, live=False):
    """Return (writes, notes). writes: [(relpath, new_text, verb)]. Raises Problem with every
    problem found, so a run that cannot finish writes nothing."""
    problems, writes, notes = [], [], []
    rel_version = release_version(release)

    def guarded(rel):
        try:
            return guard(target, rel)
        except Problem as p:
            problems.append(str(p))
            return None

    for path, src in STANDING:
        full = guarded(path)
        if full is None:
            continue
        if live and path in LIVE_EXEMPT:
            if not os.path.isfile(full):
                problems.append("%s: a repo that reads the standing orders live keeps its own "
                                "%s, which init does not write; none exists." % (path, path))
            elif has_any_marker(read(full)) or markers(read(full)):
                problems.append("%s: a repo that reads the standing orders live carries no marker "
                                "and no region here, and this one carries one. Left untouched." % path)
            else:
                notes.append("skip    %s (reads the standing orders live; no region)" % path)
            continue
        fresh = read(os.path.join(release, src))
        if not os.path.exists(full):
            writes.append((path, date_marker(fresh, today), "wrote"))
            continue
        cur = read(full)
        try:
            parse_region(cur)
        except Problem as p:
            if adopt and not has_any_marker(cur):
                lines = cur.split("\n")
                at = adopt_point(lines)
                b, e = parse_region(fresh)
                block = fresh.split("\n")[b:e + 1]
                new = "\n".join(lines[:at] + [""] + block + [""] + lines[at:])
                writes.append((path, date_marker(new, today), "adopted"))
                notes.append("adopt   %s: the region went in at line %d; every line already there "
                             "is still there. Text outside the region that repeats a template rule "
                             "is a stale copy, not a repo rule: delete it in this same PR. Keep what "
                             "you cannot tell apart, and list it in the PR for the founder."
                             % (path, at + 2))
                for n, _ in markers(cur):
                    notes.append("adopt   %s:%d carries an old Protocol version line outside the "
                                 "region; delete it in this PR, or the check is red "
                                 "(duplicate-marker)." % (path, n))
                continue
            hint = ""
            if not has_any_marker(cur):
                hint = (" Run with --adopt to wrap it; or, only where the founder has ruled that "
                        "this repo reads the standing orders live, with --live-standing-orders.")
            problems.append("%s: %s. Left untouched.%s" % (path, p, hint))
            continue
        have = [v for _, v in markers(region_body(cur)) if v]
        if have and max(have) > rel_version:
            problems.append("%s: its marker (%s) is ahead of this release (%s). Either the marker "
                            "was raised by hand, so lower it to %s, or the repo is on a newer "
                            "release, so run init from that release's SHA, taken from the caller's "
                            "pin on the default branch or from the founder."
                            % (path, vstr(max(have)), vstr(rel_version), vstr(rel_version)))
            continue
        body = region_body(fresh)
        if undate(region_body(cur)).replace("\r", "") == undate(body):
            notes.append("same    %s" % path)
            continue
        writes.append((path, splice(cur, date_marker(body, today)), "updated region"))

    # Callers. A repo may carry the toolkit's one-file shape (both jobs in one workflow), so
    # existing pins are found wherever they are rather than assumed at tier1.yml.
    if guarded(WF) is None:
        raise Problem("\n".join(problems))
    texts = {}
    for p in workflow_files(target):
        if guarded(p) is not None:
            texts[p] = read(os.path.join(target, p))
    for p, t in texts.items():
        if OLD_NAME.search(t):
            problems.append("%s names the toolkit as chiibitsu/gates, its name before the "
                            "move; GitHub does not follow that redirect for `uses:`. Rename it "
                            "to Chiibitsu-Labs/gates by hand first." % p)
    t1 = read(os.path.join(release, TIER1[1]))
    uses_c = comment_of(t1, GATES_USES, 4)
    ref_c = comment_of(t1, GATES_REF, 4)
    kinds = set()
    for p, t in texts.items():
        found = pins_in(t)
        if not found:
            continue
        here = {k for k, _ in found}
        kinds.update(here)
        loose = sorted({r for _, r in found if not SHA40.fullmatch(r)})
        stuck = sorted({r for _, r in found if SHA40.fullmatch(r) and not is_ancestor(history, r, sha)})
        if loose:
            problems.append("%s pins the toolkit by %s, not by a 40-character SHA; pin it by SHA by "
                            "hand first." % (p, ", ".join(loose)))
            continue
        if stuck:
            problems.append("%s pins %s, which this checkout's history does not contain "
                            "(a newer release, or one it cannot place). init moves a pin only "
                            "forward; nothing was written." % (p, ", ".join(s[:12] for s in stuck)))
            continue
        new = move_pins(t, sha, uses_c, ref_c)
        if live:
            new = add_live_input(new)
        if new != t:
            writes.append((p, new, "moved pin"))
            copies = [s for k, s in (("gates", TIER1[1]), ("review", REVIEWC[1])) if k in here]
            notes.append("note    %s: only its pins moved%s. Its body is this repo's; compare it "
                         "with init/release/%s if the release changed the caller." % (
                             p, " and the live input went in" if live and LIVE_INPUT.search(new)
                             and not LIVE_INPUT.search(t) else "", " and ".join(copies)))
        else:
            notes.append("same    %s" % p)
    for (path, src), kind in ((TIER1, "gates"), (REVIEWC, "review")):
        if kind in kinds:
            continue
        if path in texts:
            problems.append("%s exists but does not call the toolkit's %s.yml; left untouched." % (path, kind))
            continue
        if guarded(path) is None:
            continue
        new = move_pins(read(os.path.join(release, src)), sha, uses_c, ref_c)
        writes.append((path, add_live_input(new) if live else new, "wrote"))
    if SECURITY[0] in texts or any(SECURITY_USES.search(t) for t in texts.values()):
        notes.append("same    %s (a security caller exists; init never rewrites its pin)" % SECURITY[0])
    elif guarded(SECURITY[0]) is not None:
        writes.append((SECURITY[0], read(os.path.join(release, SECURITY[1])), "wrote"))

    if problems:
        raise Problem("\n".join(problems))
    return writes, notes


def apply(target, writes):
    for path, text, _ in writes:
        full = guard(target, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        guard(target, path)  # again, after the directories exist
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def git(where, *args):
    r = subprocess.run(["git", "-C", where] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def cmd_init(target, sha, adopt, live):
    if not SHA40.fullmatch(sha or ""):
        print("init: --sha must be the 40-character SHA you were given for this release "
              "(the Tier 1 caller's pin on the default branch, or one the founder states).")
        return 2
    rc, head = git(TOOLKIT, "rev-parse", "HEAD")
    if rc != 0 or head != sha:
        print("init: this toolkit checkout is at %s, not the --sha you named (%s). Clone at "
              "that SHA and confirm `git rev-parse HEAD` before running init." % (head or "?", sha))
        return 1
    rc, dirty = git(TOOLKIT, "status", "--porcelain", "--untracked-files=normal")
    if rc != 0 or dirty:
        print("init: this toolkit checkout has local changes or untracked files; run init only "
              "from a clean checkout of the SHA you named.")
        return 1
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        writes, notes = plan(target, sha, today, TOOLKIT, adopt=adopt, live=live)
        apply(target, writes)
    except Problem as p:
        for line in str(p).split("\n"):
            print("PROBLEM " + line)
        print("init: nothing was written.")
        return 1
    for path, _, verb in writes:
        print("%-7s %s" % (verb.split()[0], path) + ("" if verb == "wrote" else "  (%s)" % verb))
    for n in notes:
        print(n)
    print("init: done. Read the diff, run the gates, and open a PR; init never commits or pushes.")
    return 0


# ── check ──────────────────────────────────────────────────────────────────────────────

def check(target, release=RELEASE, live=False):
    """Return a list of (code, message). Empty means green."""
    rel = release_version(release)
    fails, seen = [], {}
    claude = os.path.join(target, "CLAUDE.md")
    claude_marked = (not live and os.path.isfile(claude)
                     and any(v for _, v in markers(read(claude))))
    ptr = POINTER if claude_marked else POINTER_RELEASE
    for path, _ in STANDING:
        full = os.path.join(target, path)
        if not os.path.isfile(full):
            fails.append(("missing-file", "%s: expected a standing-orders file%s, found none. "
                          "See %s." % (path, "" if live and path in LIVE_EXEMPT else " at " + vstr(rel), ptr)))
            continue
        text = read(full)
        ms = markers(text)
        if live and path in LIVE_EXEMPT:
            if ms or has_any_marker(text):
                fails.append(("live-marked", "%s: expected no marker and no region in a repo that "
                              "reads the standing orders live, found %s. See %s." % (
                                  path, "a marker" if ms else "a region marker", ptr)))
            continue
        if not ms:
            fails.append(("missing-marker", "%s: expected a Protocol version marker at %s, found "
                          "none. See %s." % (path, vstr(rel), ptr)))
            continue
        if len(ms) > 1:
            fails.append(("duplicate-marker", "%s: expected one Protocol version marker, found %d "
                          "(lines %s); no copy wins. See %s." % (
                              path, len(ms), ", ".join(str(n) for n, _ in ms), ptr)))
            continue
        n, v = ms[0]
        if v is None:
            fails.append(("missing-marker", "%s:%d: expected a marker reading `**Protocol version: "
                          "vibeOS docs/02 %s**`, found one this check cannot read. See %s." % (
                              path, n, vstr(rel), ptr)))
            continue
        seen[path] = v
        if v < rel:
            fails.append(("behind", "%s:%d: expected %s, the version the pinned release declares; "
                          "found %s, which is behind it. See %s." % (path, n, vstr(rel), vstr(v), ptr)))
        elif v > rel:
            fails.append(("ahead", "%s:%d: expected %s, the version the pinned release declares; "
                          "found %s, which is ahead of it. See %s." % (path, n, vstr(rel), vstr(v), ptr)))
    if len(set(seen.values())) > 1:
        fails.append(("split", "the standing-orders files disagree: expected one version, found %s. "
                      "See %s." % (", ".join("%s %s" % (p, vstr(v)) for p, v in sorted(seen.items())), ptr)))
    occ = []
    for p in workflow_files(target):
        occ += [(p, k, s) for k, s in pins_in(read(os.path.join(target, p)))]
    lacking = [k for k in ("gates", "gates_ref", "review") if k not in {k for _, k, _ in occ}]
    if lacking:
        fails.append(("pin-missing", "expected the toolkit's gates.yml, gates_ref and review.yml "
                      "pins in %s, found no %s pin. See %s." % (WF, " or ".join(lacking), ptr)))
    loose = [(p, k, s) for p, k, s in occ if not SHA40.fullmatch(s)]
    if loose:
        fails.append(("pin-not-sha", "expected every gates pin to be a 40-character SHA, found %s. "
                      "See %s." % ("; ".join("%s %s %s" % x for x in loose), ptr)))
    if len({s for _, _, s in occ}) > 1:
        fails.append(("pin-split", "expected every gates pin to name one commit, found %s. "
                      "See %s." % ("; ".join("%s %s %s" % (p, k, s[:12]) for p, k, s in occ), ptr)))
    return fails


def cmd_check(target, live):
    fails = check(target, live=live)
    for code, msg in fails:
        print("FAIL [standing-orders] %s: %s" % (code, msg))
    if fails:
        return 1
    print("ok [standing-orders] markers equal the pinned release (%s), and the gates, gates_ref "
          "and review pins name one SHA" % vstr(release_version()))
    return 0


# ── the team-safe copy ─────────────────────────────────────────────────────────────────
#
# Derived from vibeOS's templates by fixed edits, each asserted to match exactly as often as
# expected, so a template that moved under an edit stops the derivation instead of letting
# the private text through. No edit and no check below spells out a private string: each
# finds its text by structure (a heading, a paragraph's shape, a bold lead), and the
# founder's name is matched by its SHA-256 digest. Spelling them out here would publish them.

POINTER_WORD = "pointer"  # PROPOSED, the founder's word to pick: see the PR.
POINTER_PARAGRAPH = (
    "**The %s line.** A session that can read `chiibitsu/vibeOS` also follows that repo's "
    "`chiibitsu/vibeOS:templates/claude-md.md`: its rules (its template-owned region and its "
    "repo-specific gotchas), never its placeholders, at its published head, never a "
    "checkout's working tree, a branch or a fork (`chiibitsu/vibeOS:canon/decisions.md`, "
    "2026-11-09). Where the two differ, the stricter rule wins; where neither is stricter, "
    "vibeOS's does. A session that cannot read `chiibitsu/vibeOS` skips this paragraph."
) % POINTER_WORD
TEMPLATE = "`chiibitsu/vibeOS:templates/claude-md.md`"

NAME_DIGEST = "821e63c5081b93f995d20ab9f57f38eaf8eee8dc61bf5a9d6aef92c224a06485"
IN_REPO_DIRS = "canon|docs|ops|templates|team|product|patterns|scripts"
IN_REPO_FIRST = re.compile(r"(?:%s|\.github|gates|fixtures|init|src|app|supabase|tests)" % IN_REPO_DIRS)


def _flex(old):
    """A literal whose whitespace runs match any whitespace run, so a rewrapped line still
    matches and a reworded one does not."""
    return re.compile(r"\s+".join(re.escape(w) for w in old.split()))


def _sub(text, old, new, count=1, name=None):
    rx = _flex(old) if isinstance(old, str) else old
    found = len(rx.findall(text))
    if found != count:
        raise Problem("derive: expected %d match(es) for %s, found %d; the template moved, so "
                      "re-read it and update this edit." % (count, name or repr(old[:60]), found))
    return rx.sub(new.replace("\\", "\\\\") if isinstance(new, str) else new, text)


def _drop_section(text, heading_rx, name):
    rx = re.compile(r"^## " + heading_rx + r"[^\n]*\n.*?(?=^## |^<!-- gates:template-owned:end -->)",
                    re.M | re.S)
    return _sub(text, rx, "", name="the section " + name)


def _is_name(word):
    return hashlib.sha256(word.lower().encode()).hexdigest() == NAME_DIGEST


def _founder(text):
    def swap(m):
        if not _is_name(m.group(1)):
            return m.group(0)
        return "the founder" + (m.group(2) or "")
    text = re.sub(r"\b([A-Za-z]+)('s)?\b", swap, text)
    # Capitalise only where it now opens a sentence or a bold lead. A line start is not a
    # sentence start: a wrapped line begins mid-sentence.
    return re.sub(r"(?:(?<=[.!?]\s)|(?<=[.!?]\*\*\s)|(?<=^\*\*))the founder", "The founder",
                  text, flags=re.M)


def _locators(text):
    """`vibeOS \\`docs/04\\`` and `vibeOS's \\`canon/x.md\\`` become `chiibitsu/vibeOS:...`
    locators: a bare path resolves against the repo it is read in, and that repo has no such
    file."""
    return re.sub(r"(?:the )?vibeOS(?: repo)?(?:'s)? `((?:%s)/[^`\n]*)`" % IN_REPO_DIRS,
                  lambda m: "`chiibitsu/vibeOS:%s`" % m.group(1), text)


def _paragraphs_with(text, rx, new, name):
    """Replace the one paragraph (a run of non-blank lines) that matches `rx`."""
    paras = text.split("\n\n")
    hits = [i for i, p in enumerate(paras) if rx.search(p)]
    if len(hits) != 1:
        raise Problem("derive: expected one paragraph holding %s, found %d" % (name, len(hits)))
    paras[hits[0]] = new
    return "\n\n".join(paras)


def _in_paragraph(text, lead, edits):
    """Apply `edits` [(old, new)] inside the one paragraph opening with the bold `lead`."""
    paras = text.split("\n\n")
    hits = [i for i, p in enumerate(paras) if p.startswith(lead)]
    if len(hits) != 1:
        raise Problem("derive: expected one paragraph opening %r, found %d" % (lead, len(hits)))
    p = paras[hits[0]]
    for old, new, count in edits:
        p = _sub(p, old, new, count=count, name="%r in the paragraph %r" % (old, lead))
    paras[hits[0]] = p
    return "\n\n".join(paras)


def _insert_pointer(text):
    b, e = parse_region(text)
    lines = text.split("\n")
    for i in range(b + 1, e):
        if MARKER.search(lines[i]):
            j = i
            while j < e and lines[j].strip():
                j += 1
            return "\n".join(lines[:j] + ["", POINTER_PARAGRAPH] + lines[j:])
    raise Problem("derive: no marker line inside the region to put the %s line after" % POINTER_WORD)


def derive_claude(t):
    # The name has already become "the founder" here (derive_text runs _founder first).
    t = _sub(t, re.compile(r"^(- Deploy: previews per PR\.) [^\n]*$", re.M), lambda m: m.group(1),
             name="the Deploy line's hosting note")
    t = _drop_section(t, r"Deploy after merge\b", "on deploying after merge")
    t = _drop_section(t, r"Log the work\b[^\n]*\bledger$", "on the time ledger")
    t = _sub(t, re.compile(r"^- \*\*\d{4}-\d{2}-\d{2} — A session holding the [^\n]*vault MCP "
                           r"connector.*?(?=^- )", re.M | re.S), "", name="the vault-memory gotcha")
    t = _paragraphs_with(t, IANA, "Containers run UTC. This copy does not carry the founder's "
                         "timezone, and a\ncurrent time written here would be a lie by the time "
                         "anyone read it.", "a timezone name")
    t = _sub(t, "The offset is above and no bedtime is written anywhere in this repo, so that "
                "clause resolves by defaulting rather than guessing.",
             "No offset and no bedtime is written in this copy, so that clause resolves by\n"
             "defaulting rather than guessing.")
    t = _sub(t, re.compile(r"The run they were taken from is written up in `[\w-]+/[^`]+`, "
                           r"which states which figures are measured and which estimated — "
                           r"check it there rather than trusting these numbers because they "
                           r"are written down\."),
             "The run they were taken from is written up in the founder's own records, which "
             "state which figures are measured and which estimated; where they cannot be read, "
             "treat these numbers as reported, not measured.", name="the cost-report citation")
    t = _in_paragraph(t, "**Except a repo that reads this file live**", [
        (re.compile(r"\(`[\w-]+/[^`]+` and the apps\s+made from it"),
         "(the founder's Next.js app template and the apps\nmade from it, which the founder "
         "names; no repo qualifies by resembling one", 1),
        ("**Except a repo that reads this file live**",
         "**Except a repo that reads %s live**" % TEMPLATE, 1),
        ("never re-instantiated from this file", "never re-instantiated from it", 1),
        ("read this file as the current standing orders", "read that file as the current standing orders", 1),
    ])
    t = _in_paragraph(t, "**And a repo with no `CLAUDE.md` or `AGENTS.md` of its own**", [
        ("is meant to take this file, at", "is meant to take %s, at" % TEMPLATE, 1),
        ("once they point here.", "once they point there.", 1),
    ])
    t = _in_paragraph(t, "**vibeOS reads this file as its own standing orders too**", [
        ("**vibeOS reads this file as its own standing orders too**: its `CLAUDE.md` and `AGENTS.md` "
         "point here rather than copying it. So a rule changed here changes in vibeOS as well.",
         "**vibeOS reads that template, the source of this copy, as its own standing orders**: its "
         "`CLAUDE.md` and `AGENTS.md` point there rather than copying it.", 1),
        ("which of this file's product-repo rules", "which of the template's product-repo rules", 1),
    ])
    t = _sub(t, "vibeOS `docs/03`'s \"Working with the founder\" holds",
             "vibeOS `docs/03`'s section on working with the founder holds")
    t = _sub(t, "never copy from vibeOS `main`, which no marker-scheme repo reads.",
             "never copy from vibeOS `main`: a marker-scheme repo reads it only through its %s "
             "line, below, and never copies from it." % POINTER_WORD)
    return t


def derive_text(name, text, version_comment):
    text = _founder(text)
    if name == "claude-md.md":
        text = derive_claude(text)
    if name.endswith(".md"):
        text = _locators(text)
        text = _insert_pointer(text)
    else:
        # The callers' pin label names the release being cut (CITATION.cff), never the one
        # vibeOS's template was last bumped to.
        text = GATES_USES.sub(lambda m: m.group(1) + m.group(3) + " # " + version_comment, text)
        text = GATES_REF.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + " # " + version_comment, text)
    return text


# Shapes the team-safe copy must not carry. Each is a shape or an allowlist; none names a
# private string. What they catch, exactly: an owner-qualified name whose owner is this
# company's (`chiibitsu…/x`, bare or anywhere); any `owner/repo:path` or `owner/repo#N`;
# any github.com or raw.githubusercontent.com URL's owner/repo; a backticked `owner/repo`
# whose first segment is not an in-repo directory; a region-prefixed IANA zone name; a UTC
# or GMT offset, and a bare `+08:00`/`+0800`-shaped offset; the founder's name, by digest; a
# heading naming the ledger; a bold lead naming a vault connector. An unbackticked
# `acme/widget` in prose and a zone abbreviation are NOT caught.
LOCATOR_ALLOW = {"chiibitsu/vibeos", "chiibitsu-labs/gates", "chiibitsu-labs/security-checks"}
OWNER_QUALIFIED = re.compile(r"(?<![\w.-])(chiibitsu[\w-]*)/([A-Za-z0-9._-]*[A-Za-z0-9_-])", re.I)
COLON_LOCATOR = re.compile(r"(?<![\w./:-])([A-Za-z0-9][\w-]*)/([\w.-]+)(?=:[\w./-]|#\d)")
GITHUB_URL = re.compile(r"(?:github\.com|raw\.githubusercontent\.com)/([\w-]+)/([\w.-]*[\w-])", re.I)
BACKTICK_PAIR = re.compile(r"`([\w-]+)/([\w.-]+)`")
IANA = re.compile(r"\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|"
                  r"Pacific|Etc|US|Canada|Brazil|Chile|Mexico)/[A-Z][\w+-]+")
OFFSET = re.compile(r"\b(?:UTC|GMT)\s*[+\u2212-]\s*\d{1,2}(?::?\d{2})?\b"
                    r"|(?:(?<=\d)|(?<![\w:.+\u2212-]))[+\u2212-](?:0\d|1[0-4]):?[0-5]\d(?![\d:])")
LEDGER_HEADING = re.compile(r"^#{1,6}\s.*\bledger\b", re.I | re.M)
VAULT_LEAD = re.compile(r"\*\*[^*\n]*\bvault\b[^*\n]*\bconnector\b[^*\n]*\*\*", re.I)


def leaks(text):
    """Every omitted shape the text still carries, as human-readable strings."""
    out = []
    for rx in (OWNER_QUALIFIED, COLON_LOCATOR, GITHUB_URL, BACKTICK_PAIR):
        for m in rx.finditer(text):
            if rx in (COLON_LOCATOR, BACKTICK_PAIR) and IN_REPO_FIRST.fullmatch(m.group(1)):
                continue  # an in-repo path, not a locator
            if ("%s/%s" % (m.group(1), m.group(2))).lower() not in LOCATOR_ALLOW:
                out.append("an owner/repo locator outside the allowlist: %s" % m.group(0))
    for rx, what in ((IANA, "a timezone name"), (OFFSET, "a UTC offset"),
                     (LEDGER_HEADING, "the time-ledger heading"),
                     (VAULT_LEAD, "the vault-memory gotcha's bold lead")):
        out += ["%s: %s" % (what, m.group(0).strip()) for m in rx.finditer(text)]
    out += ["the founder's name (by digest)" for w in re.findall(r"[A-Za-z]+", text) if _is_name(w)]
    return out


RELEASE_FILES = ("claude-md.md", "agents-md.md", "review-md.md",
                 "tier1-ci.yml", "review-ci.yml", "security-ci.yml")


def citation_version(toolkit=TOOLKIT):
    m = re.search(r'^version:\s*"?([0-9][0-9.]*)"?\s*$', read(os.path.join(toolkit, "CITATION.cff")), re.M)
    if not m:
        raise Problem("derive: CITATION.cff declares no version")
    return "v" + m.group(1)


def protocol_of(vibeos):
    """The version docs/02 declares, which every template marker must equal."""
    text = read(os.path.join(vibeos, "docs", "02-vibecoding-protocol.md"))
    m = re.search(r"^\*\*Protocol version: v(\d+)\.(\d+)(?:\.(\d+))?\.\*\*", text, re.M)
    if not m:
        raise Problem("derive: docs/02 declares no `**Protocol version: vX.Y.**` line")
    return tuple(int(x or 0) for x in m.groups())


def published(vibeos):
    """The commit derive may name: the checkout must be clean, untracked files included, and
    its HEAD must equal origin/main as last fetched. Anything else is rules no vibeOS merge
    approved, labelled as a published commit."""
    rc, dirty = git(vibeos, "status", "--porcelain", "--untracked-files=all")
    if rc != 0:
        raise Problem("derive: %s is not a git checkout" % vibeos)
    if dirty:
        raise Problem("derive: the vibeOS checkout has local changes or untracked files; derive "
                      "only from a clean checkout of its published head")
    rc, head = git(vibeos, "rev-parse", "HEAD")
    rc2, main = git(vibeos, "rev-parse", "--verify", "-q", "refs/remotes/origin/main")
    if rc != 0 or rc2 != 0 or not main:
        raise Problem("derive: the vibeOS checkout has no origin/main to compare with; fetch it")
    if head != main:
        raise Problem("derive: the vibeOS checkout is at %s, not origin/main (%s); check out the "
                      "published head (fetch first)" % (head[:12], main[:12]))
    return head


def cmd_derive(vibeos, out=RELEASE, toolkit=TOOLKIT):
    try:
        source = published(vibeos)
        want = protocol_of(vibeos)
        label = citation_version(toolkit)
        texts = {}
        for name in RELEASE_FILES:
            src = read(os.path.join(vibeos, "templates", name))
            if name.endswith(".md"):
                ms = markers(src)
                if [v for _, v in ms] != [want]:
                    raise Problem("derive: templates/%s carries %s; docs/02 declares %s" % (
                        name, [vstr(v) if v else "?" for _, v in ms], vstr(want)))
            texts[name] = derive_text(name, src, label)
            bad = leaks(texts[name])
            if bad:
                raise Problem("derive: %s still carries %s" % (name, "; ".join(bad)))
    except Problem as p:
        print(p)
        return 1
    os.makedirs(out, exist_ok=True)
    for name, text in texts.items():
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    with open(os.path.join(out, "PROTOCOL"), "w", encoding="utf-8") as fh:
        fh.write("# The protocol version this release's standing-orders copy carries, and the\n"
                 "# vibeOS commit it was derived from by `init/gates_init.py derive`.\n"
                 "protocol %s\nsource chiibitsu/vibeOS@%s\n" % (vstr(want), source))
    print("derive: wrote %d files to %s at %s from chiibitsu/vibeOS@%s, pins labelled %s"
          % (len(texts), os.path.relpath(out), vstr(want), source[:12], label))
    return 0


# ── selftest ───────────────────────────────────────────────────────────────────────────

def selftest():
    global NAME_DIGEST
    failed = []

    def ok(cond, what):
        print(("ok  " if cond else "FAIL ") + what)
        if not cond:
            failed.append(what)

    def snapshot(root):
        out = {}
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in fs:
                p = os.path.join(d, f)
                out[os.path.relpath(p, root)] = os.readlink(p) if os.path.islink(p) else read(p)
        return out

    def write(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def run(cmd, cwd):
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    work = tempfile.mkdtemp(prefix="gates-init-selftest.")
    hist = os.path.join(work, "history")
    os.makedirs(hist)
    env = ["-c", "user.name=selftest", "-c", "user.email=selftest@invalid", "-c", "commit.gpgsign=false"]
    run(["git", "init", "-q"], hist)
    run(["git"] + env + ["commit", "-q", "--allow-empty", "-m", "release N"], hist)
    N = run(["git", "rev-parse", "HEAD"], hist)
    run(["git"] + env + ["commit", "-q", "--allow-empty", "-m", "release N+1"], hist)
    N1 = run(["git", "rev-parse", "HEAD"], hist)
    today = "2026-01-02"
    rel = release_version()
    relv = vstr(rel)
    behind = "v%d.%d" % (rel[0], rel[1] - 1) if rel[1] else "v%d.9" % (rel[0] - 1)
    ahead = "v%d.%d" % (rel[0], rel[1] + 1)

    def fresh(name):
        d = os.path.join(work, name)
        os.makedirs(d)
        return d

    def do(target, sha=N, **kw):
        writes, notes = plan(target, sha, today, hist, **kw)
        apply(target, writes)
        return writes, notes

    def problem(target, sha=N, **kw):
        try:
            plan(target, sha, today, hist, **kw)
        except Problem as p:
            return str(p)
        return None

    all_files = [p for p, _ in STANDING] + [TIER1[0], REVIEWC[0], SECURITY[0]]
    srcs = dict(STANDING + (TIER1, REVIEWC, SECURITY))
    strip = lambda t: GATES_REF.sub("gates_ref: X", GATES_USES.sub("USES@X", t))

    # 1. an empty directory gets the six files, equal to the release copies bar dates and pins.
    a = fresh("empty")
    do(a)
    got = snapshot(a)
    ok(sorted(got) == sorted(all_files), "init in an empty directory writes exactly the six files")
    same = all(undate(got[p]) == read(os.path.join(RELEASE, srcs[p])) for p, _ in STANDING)
    ok(same, "the three standing-orders files equal the release copies except the date")
    ok(all(DATE.search(got[p]).group(2) == today for p, _ in STANDING),
       "each standing-orders marker carries the instantiation date")
    shas = [s for p in (TIER1[0], REVIEWC[0]) for _, s in pins_in(got[p])]
    ok(len(shas) == 3 and set(shas) == {N}, "the three gates-SHA occurrences equal each other and the running SHA")
    ok(all(strip(got[p]) == strip(read(os.path.join(RELEASE, s))) for p, s in (TIER1, REVIEWC)),
       "the two gates callers equal the release copies except the pins")
    ok(got[SECURITY[0]] == read(os.path.join(RELEASE, SECURITY[1])), "the security caller equals the release copy")
    ok(check(a) == [], "the check is green on a freshly initialised tree")

    # 2. a second run changes nothing, on a later day too.
    before = snapshot(a)
    writes, _ = plan(a, N, "2026-02-03", hist)
    apply(a, writes)
    ok(writes == [] and snapshot(a) == before, "a second run, on a later day, changes nothing")

    # 3. a file one version behind keeps every byte outside its region.
    b = fresh("behind")
    cur = read(os.path.join(RELEASE, "claude-md.md"))
    old = cur.replace("docs/02 " + relv, "docs/02 " + behind, 1)
    old = splice(old, region_body(old) + "\nan obsolete template rule")
    above, below = "# CLAUDE.md — acme\n\nRepo rule above. \u00e9\r\n", "\n## Ours\n- keep me\n"
    bb, be = parse_region(old)
    old = above + "\n".join(old.split("\n")[bb:be + 1]) + below
    write(b, "CLAUDE.md", old)
    do(b)
    new = read(os.path.join(b, "CLAUDE.md"))
    ok(new.startswith(above) and new.endswith(below), "a one-version-behind file keeps every byte outside its region")
    ok("an obsolete template rule" not in new and undate(region_body(new)) == region_body(cur),
       "and its region is the release's")

    # 3b. a CRLF file keeps CRLF in its new region, and a second run is a no-op.
    c = fresh("crlf")
    write(c, "AGENTS.md", read(os.path.join(RELEASE, "agents-md.md")).replace(
        "docs/02 " + relv, "docs/02 " + behind, 1).replace("\n", "\r\n"))
    do(c)
    t = read(os.path.join(c, "AGENTS.md"))
    ok(t.count("\n") == t.count("\r\n"), "a CRLF file's new region is CRLF too, so no line ending is mixed")
    writes, _ = plan(c, N, today, hist)
    ok(not [w for w in writes if w[0] == "AGENTS.md"], "and a second run leaves the CRLF file alone")

    # 4. each malformed shape is reported and the run writes nothing at all.
    good = read(os.path.join(RELEASE, "agents-md.md"))
    gb, ge = parse_region(good)
    gl = good.split("\n")
    shapes = {
        "none": "# AGENTS.md\nno markers\n",
        "two pairs": good + "\n" + BEGIN + "\nx\n" + END + "\n",
        "reversed": "\n".join(gl[:gb] + [END] + gl[gb + 1:ge] + [BEGIN] + gl[ge + 1:]),
        "fenced": "# AGENTS.md\n```\n" + BEGIN + "\nx\n" + END + "\n```\n",
        "unpaired": "\n".join(gl[:ge] + gl[ge + 1:]),
        "inline": "# AGENTS.md\nsee " + BEGIN + " here\n",
    }
    says = {"none": "no template-owned region", "two pairs": "more than one",
            "reversed": "before the begin", "fenced": "inside a fenced block",
            "unpaired": "unpaired", "inline": "not alone on its line"}
    for shape, text in shapes.items():
        d = fresh("malformed-" + shape.replace(" ", "-"))
        write(d, "AGENTS.md", text)
        before = snapshot(d)
        msg = problem(d)
        ok(msg is not None and msg.startswith("AGENTS.md: ") and says[shape] in msg,
           "a %s region is reported as %r" % (shape, says[shape]))
        ok(snapshot(d) == before, "and the %s case writes nothing, AGENTS.md or otherwise" % shape)

    # 4b. init never writes through a symlink, inside the tree or out of it.
    outside = fresh("outside")
    d = fresh("symlink-out")
    os.symlink(os.path.join(outside, "planted.md"), os.path.join(d, "CLAUDE.md"))
    os.makedirs(os.path.join(d, ".github"))
    os.symlink(outside, os.path.join(d, WF))
    msg = problem(d)
    ok(msg is not None and "CLAUDE.md is a symlink" in msg and ".github/workflows is a symlink" in msg
       and os.listdir(outside) == [], "a symlinked file or workflows directory stops the run, and nothing lands outside")
    d = fresh("symlink-twin")
    do(d)
    os.remove(os.path.join(d, "CLAUDE.md"))
    os.symlink("AGENTS.md", os.path.join(d, "CLAUDE.md"))
    before = snapshot(d)
    ok(problem(d) is not None and snapshot(d) == before, "CLAUDE.md -> AGENTS.md stops the run rather than flipping both")

    # 5. adoption wraps a region into a file with none, keeping every existing byte.
    d = fresh("adopt")
    legacy = "# CLAUDE.md — legacy\n\n## Old rules\n- a stale copy of a template rule\n"
    write(d, "CLAUDE.md", legacy)
    _, notes = do(d, adopt=True)
    new = read(os.path.join(d, "CLAUDE.md"))
    ok(all(l in new.split("\n") for l in legacy.split("\n")) and new.split("\n")[0] == legacy.split("\n")[0]
       and parse_region(new), "--adopt inserts one region after the first heading and keeps every existing line")
    d = fresh("adopt-frontmatter")
    fm = "---\nname: x\n---\nintro, no heading\n"
    write(d, "CLAUDE.md", fm)
    do(d, adopt=True)
    ok(read(os.path.join(d, "CLAUDE.md")).startswith("---\nname: x\n---\n\n" + BEGIN),
       "--adopt on a file with frontmatter and no heading puts the region after the frontmatter")
    d = fresh("adopt-old-marker")
    write(d, "CLAUDE.md", "# CLAUDE.md\n**Protocol version: `docs/02` v8.1.**\n")
    _, notes = do(d, adopt=True)
    ok(any("old Protocol version line" in n for n in notes) and
       {c for c, _ in check(d)} == {"duplicate-marker"},
       "--adopt names an old marker line it leaves outside, and the check stays red on it")
    two = fresh("adopt-malformed")
    write(two, "CLAUDE.md", shapes["two pairs"])
    ok(problem(two, adopt=True) is not None, "--adopt never touches a file that already has markers")

    # 6. an existing security caller keeps its pin.
    d = fresh("security")
    sec = read(os.path.join(RELEASE, SECURITY[1]))
    mine = sec.replace(SHA40.search(sec).group(0), "1" * 40)
    write(d, SECURITY[0], mine)
    do(d)
    ok(read(os.path.join(d, SECURITY[0])) == mine, "an existing security caller keeps its pin")

    # 7. pins move only forward, only in workflows that call the toolkit.
    d = fresh("pinned-ahead")
    do(d, sha=N1)
    before = snapshot(d)
    msg = problem(d, sha=N)
    ok(msg is not None and snapshot(d) == before,
       "run from release N on a repo pinned to N+1, init moves no pin and writes nothing")
    d = fresh("pinned-behind")
    do(d, sha=N)
    do(d, sha=N1)
    ok({s for p in (TIER1[0], REVIEWC[0]) for _, s in pins_in(read(os.path.join(d, p)))} == {N1},
       "run from release N+1 on a repo pinned to N, init moves all three pins")
    d = fresh("pinned-unknown")
    do(d, sha=N)
    write(d, TIER1[0], read(os.path.join(d, TIER1[0])).replace(N, "2" * 40))
    ok(problem(d, sha=N1) is not None, "a pin the checkout's history does not contain stops the run")
    d = fresh("pinned-by-tag")
    write(d, WF + "/ci.yml", "jobs:\n  g:\n    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@v1.2.2\n"
          "  r:\n    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@main\n")
    msg = problem(d)
    ok(msg is not None and "not by a 40-character SHA" in msg, "a caller pinned by tag or branch stops the run, so no twin caller is written")
    d = fresh("marker-ahead")
    do(d, sha=N)
    write(d, "CLAUDE.md", read(os.path.join(d, "CLAUDE.md")).replace("docs/02 " + relv, "docs/02 " + ahead, 1))
    before = snapshot(d)
    ok(problem(d, sha=N) is not None and snapshot(d) == before,
       "a marker ahead of this release stops the run, so init never downgrades a region")
    d = fresh("one-file-caller")
    write(d, WF + "/gates.yml",
          "jobs:\n  gates:\n    uses: chiibitsu-labs/gates/.github/workflows/gates.yml@%s # v1.2.2\n"
          "    with:\n      gates_ref: %s # v1.2.2\n  review:\n"
          "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s # v1.2.2\n" % (N, N, N))
    do(d, sha=N1)
    t = read(os.path.join(d, WF + "/gates.yml"))
    ok(not os.path.exists(os.path.join(d, TIER1[0])) and not os.path.exists(os.path.join(d, REVIEWC[0]))
       and {s for _, s in pins_in(t)} == {N1} and "chiibitsu-labs/gates" not in t,
       "a one-file caller, owner in any case, has its three pins moved and gains no second caller")
    d = fresh("foreign-gates-ref")
    other = "jobs:\n  n:\n    uses: someone/else/.github/workflows/y.yml@%s\n    with:\n      gates_ref: %s\n" % ("a" * 40, N)
    write(d, WF + "/nightly.yml", other)
    do(d, sha=N1)
    ok(read(os.path.join(d, WF + "/nightly.yml")) == other, "a gates_ref in a workflow that does not call the toolkit is left alone")
    d = fresh("old-name")
    write(d, WF + "/gates.yml", "    uses: chiibitsu/gates/.github/workflows/gates.yml@%s\n" % N)
    ok(problem(d) is not None, "a caller still naming chiibitsu/gates stops the run")

    # 8. a live-read repo: no region for CLAUDE.md or AGENTS.md, the input set, the check strict.
    def live_repo():
        d = fresh("live-%d" % len(os.listdir(work)))
        write(d, "CLAUDE.md", "# CLAUDE.md\nRead vibeOS templates/claude-md.md live.\n")
        write(d, "AGENTS.md", "# AGENTS.md\nRead CLAUDE.md.\n")
        return d
    d = live_repo()
    keep = {p: read(os.path.join(d, p)) for p in LIVE_EXEMPT}
    do(d, live=True)
    ok(all(read(os.path.join(d, p)) == keep[p] for p in LIVE_EXEMPT) and os.path.exists(os.path.join(d, "REVIEW.md")),
       "--live-standing-orders writes REVIEW.md and leaves CLAUDE.md and AGENTS.md as they are")
    ok(LIVE_INPUT.search(read(os.path.join(d, TIER1[0]))) is not None,
       "and the tier1 caller it writes sets standing_orders_live: true")
    ok(check(d, live=True) == [], "and the live check is green on it")
    ok({c for c, _ in check(d)} == {"missing-marker"}, "while the full check still wants the two markers")
    d = live_repo()
    write(d, "AGENTS.md", read(os.path.join(RELEASE, "agents-md.md")))
    ok(problem(d, live=True) is not None, "--live-standing-orders refuses a CLAUDE.md or AGENTS.md that carries a region")
    d = live_repo()
    do(d, live=True)
    write(d, "CLAUDE.md", "# CLAUDE.md\n**Protocol version: vibeOS docs/02 v7.0** stale\n")
    ok({c for c, _ in check(d, live=True)} == {"live-marked"}, "the live check is red on a marked CLAUDE.md, so the flag cannot hide a stale one")
    d = live_repo()
    do(d, live=True)
    os.remove(os.path.join(d, "AGENTS.md"))
    ok({c for c, _ in check(d, live=True)} == {"missing-file"}, "the live check is red on a missing AGENTS.md")
    d = live_repo()
    os.remove(os.path.join(d, "AGENTS.md"))
    ok(problem(d, live=True) is not None, "--live-standing-orders refuses a repo with no AGENTS.md of its own")
    d = live_repo()
    t = "    with:\n      gates_ref: %s # v1.2.2\n" % N
    write(d, WF + "/gates.yml", "    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s # v1.2.2\n%s"
          "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s # v1.2.2\n" % (N, t, N))
    do(d, sha=N1, live=True)
    ok("      standing_orders_live: true\n" in read(os.path.join(d, WF + "/gates.yml")),
       "--live-standing-orders adds the input to an existing caller, beside gates_ref")

    # 9. the check is red for each failure, and only that failure's code fires.
    def checked(mutate):
        d = fresh("check-%d" % len(os.listdir(work)))
        do(d)
        mutate(d)
        return {c for c, _ in check(d)}

    def edit(path, f):
        return lambda d: write(d, path, f(read(os.path.join(d, path))))

    every = lambda f: (lambda d: [edit(p, f)(d) for p, _ in STANDING])
    cases = {
        "missing-file": (lambda d: os.remove(os.path.join(d, "REVIEW.md")), {"missing-file"}),
        "missing-marker": (edit("AGENTS.md", lambda t: MARKER.sub("", t)), {"missing-marker"}),
        "duplicate-marker": (edit("CLAUDE.md", lambda t: t + "\n" + MARKER.search(t).group(0) + "\n"),
                             {"duplicate-marker"}),
        "behind": (every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1)), {"behind"}),
        "ahead": (every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + ahead, 1)), {"ahead"}),
        "split": (edit("REVIEW.md", lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1)),
                  {"behind", "split"}),
        "pin-split": (edit(REVIEWC[0], lambda t: t.replace(N, N1)), {"pin-split"}),
        "pin-split, owner in another case": (edit(REVIEWC[0], lambda t: t.replace(
            "Chiibitsu-Labs/gates", "chiibitsu-labs/gates").replace(N, N1)), {"pin-split"}),
        "pin-missing (no callers)": (lambda d: [os.remove(os.path.join(d, p)) for p in (TIER1[0], REVIEWC[0])],
                                     {"pin-missing"}),
        "pin-missing (review only)": (lambda d: os.remove(os.path.join(d, REVIEWC[0])), {"pin-missing"}),
        "pin-not-sha": (edit(TIER1[0], lambda t: GATES_REF.sub(r"gates_ref: ${{ vars.X }}", t)),
                        {"pin-not-sha", "pin-split"}),
    }
    for name, (mutate, want) in cases.items():
        got = checked(mutate)
        ok(got == want, "the check is red for %s, and reports %s" % (name, ", ".join(sorted(want))))
    d = fresh("pointer-without-claude")
    do(d)
    os.remove(os.path.join(d, "CLAUDE.md"))
    ok(all(POINTER_RELEASE in m for _, m in check(d)),
       "with no CLAUDE.md marker to point at, the check points at the release's copy instead")

    # 10. the committed release copy is the team-safe copy.
    label = citation_version()
    for name in RELEASE_FILES:
        text = read(os.path.join(RELEASE, name))
        ok(leaks(text) == [], "init/release/%s carries none of the omitted shapes" % name)
        if name.endswith(".md"):
            ok(text.count(POINTER_PARAGRAPH) == 1 and POINTER_PARAGRAPH in region_body(text),
               "init/release/%s carries the %s line once, inside its region" % (name, POINTER_WORD))
            ok([v for _, v in markers(text)] == [rel], "init/release/%s carries one marker, at %s" % (name, relv))
        elif name != SECURITY[1]:
            ok({(m.group(4) or "").split("#")[-1].strip() for rx in (GATES_USES, GATES_REF)
                for m in rx.finditer(text)} == {label},
               "init/release/%s labels every gates pin %s, this release's CITATION.cff version" % (name, label))
    real = NAME_DIGEST
    NAME_DIGEST = hashlib.sha256(b"zorblat").hexdigest()  # a stand-in, so no probe names her
    probes = {
        "chiibitsu/example-private": True, "`chiibitsu/example:path/x.md`": True,
        "acme/widget:src/x.py": True, "acme/widget#12": True, "github.com/acme/widget": True,
        "https://raw.githubusercontent.com/chiibitsu/example/abc/x.py": True,
        "`acme/client-portal`": True, "Asia/Tokyo": True, "US/Pacific": True, "UTC+5": True,
        "GMT -05:00": True, "2026-11-20T09:00+05:00": True, "(+0530)": True,
        "Zorblat's call": True, "zorblat": True, "## Log it: the ledger": True,
        "**A vault MCP connector rule**": True,
        "Zorblatty": False, "Chiibitsu Labs": False, "Chiibitsu-Labs/gates": False,
        "chiibitsu/vibeOS": False, "`docs/04:12`": False, "`canon/decisions.md`": False,
        "containers run UTC": False, "the ledger, unheaded": False, "2026-11-09": False,
        "v8.2, PR #208": False,
    }
    for probe, bad in probes.items():
        ok(bool(leaks(probe)) == bad, "the shape check %s %r" % ("catches" if bad else "passes", probe))
    ok(_founder("Ask zorblat. Zorblat's call.") == "Ask the founder. The founder's call.",
       "the name swap works by digest and capitalises at a sentence start")
    NAME_DIGEST = real

    # 11. derive names only a published vibeOS commit.
    origin = fresh("vibeos-origin")
    run(["git", "init", "-q", "--bare"], origin)
    clone = os.path.join(work, "vibeos-clone")
    run(["git", "clone", "-q", origin, clone], work)
    run(["git"] + env + ["commit", "-q", "--allow-empty", "-m", "published"], clone)
    run(["git", "push", "-q", "origin", "HEAD:main"], clone)
    run(["git", "fetch", "-q", "origin"], clone)
    head = run(["git", "rev-parse", "HEAD"], clone)
    try:
        ok(published(clone) == head, "derive accepts a clean checkout at origin/main")
    except Problem as p:
        ok(False, "derive accepts a clean checkout at origin/main (%s)" % p)

    def refuses(what):
        try:
            published(clone)
        except Problem:
            return True
        return False
    write(clone, "templates/x.md", "an unmerged rule\n")
    ok(refuses("dirty"), "derive refuses a checkout with an untracked or edited file")
    os.remove(os.path.join(clone, "templates/x.md"))
    run(["git"] + env + ["commit", "-q", "--allow-empty", "-m", "unpushed"], clone)
    ok(refuses("ahead"), "derive refuses a checkout whose HEAD is not origin/main")

    shutil.rmtree(work, ignore_errors=True)
    if failed:
        print("\nselftest: %d check(s) FAILED" % len(failed))
        return 1
    print("\nselftest: every init and check case passed")
    return 0


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if len(argv) >= 3 and argv[1] in ("init", "check", "derive"):
        target, rest = os.path.realpath(argv[2]), argv[3:]
        live = "--live-standing-orders" in rest
        if argv[1] == "check" and set(rest) <= {"--live-standing-orders"}:
            return cmd_check(target, live)
        if argv[1] == "derive" and not rest:
            return cmd_derive(target)
        if argv[1] == "init":
            sha = rest[rest.index("--sha") + 1] if "--sha" in rest[:-1] else ""
            known = {"--sha", sha, "--adopt", "--live-standing-orders"}
            if set(rest) <= known:
                return cmd_init(target, sha, "--adopt" in rest, live)
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
