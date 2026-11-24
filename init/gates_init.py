#!/usr/bin/env python3
# Part of Chiibitsu-Labs/gates by Angeline S. Viray (Chiibitsu Labs). MIT. https://github.com/Chiibitsu-Labs/gates
"""gates init, the standing-orders version check, and the team-safe copy's derivation.

    gates_init.py init   TARGET --sha SHA [--adopt] [--live-standing-orders]
    gates_init.py check  TARGET [--live-standing-orders]
    gates_init.py derive VIBEOS_CHECKOUT
    gates_init.py --selftest

THE SIX FILES, AND NOTHING ELSE. init writes `CLAUDE.md`, `AGENTS.md`, `REVIEW.md` and the
three callers (`.github/workflows/tier1.yml`, `review.yml`, `security.yml`) from the copies
in `init/release/`. It never touches a `.gitignore`, a hook, a README or a board, and it
never commits or pushes: a session reads the diff, runs the gates and opens the PR.

STANDING-ORDERS FILES. Each holds one template-owned region between the two marker lines
below. init rewrites the region and keeps every byte outside it. A file with no region, more
than one marker of either kind, the end before the begin, a marker inside a fenced block, or
a marker that is not alone on its line is reported and left untouched. `--adopt` is the one
exception, for a file with no marker at all: it inserts the region after the file's first
heading and leaves every existing byte where it was, so the stale text it duplicates shows
up in the diff for the session to delete by hand.

WHEN A PIN MOVES. init moves a gates pin to the SHA of the checkout it runs from, and only
forward: an existing pin moves only if that checkout's history contains it. A pin it cannot
place (newer, unknown, or on another line of history) stops the whole run with nothing
written, so a session running an older release over a newer pin changes nothing. The
security caller is written only when the repo has none and is never rewritten.

ALL OR NOTHING. Every file is planned first. Any problem in any file stops the run before
the first write, because a half-applied init leaves the three markers split.

THE CHECK asserts the markers and the pin, never the body: red, with expected, found and
the paragraph to read, for a missing file among the three, a missing marker, a duplicated
marker, a marker behind or ahead of the release, the files split between versions, a
missing pin, or the gates-SHA occurrences disagreeing. It edits nothing.
"""
import datetime
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
# Any line that looks like an attempt at one, so a mangled copy counts as a marker that is
# there and wrong rather than as no marker at all.
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

SHA40 = r"[0-9a-f]{40}"
GATES_USES = re.compile(r"(Chiibitsu-Labs/gates/\.github/workflows/(gates|review)\.yml@)(" + SHA40 + r")([ \t]*#[ \t]*v[0-9][0-9.]*)?")
GATES_REF = re.compile(r"(gates_ref:[ \t]*['\"]?)(" + SHA40 + r")(['\"]?)([ \t]*#[ \t]*v[0-9][0-9.]*)?")
# The toolkit's name before the move to the org. GitHub does not follow a transfer redirect
# for `uses:`, so a caller still spelled this way is broken, and init will not guess at it.
OLD_NAME = re.compile(r"(?<![\w-])chiibitsu/gates/\.github/workflows/", re.I)
SECURITY_USES = "Chiibitsu-Labs/security-checks/"

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
    b, e = parse_region(text)
    lines = text.split("\n")
    return "\n".join(lines[:b + 1] + body.split("\n") + lines[e:])


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


# ── pins ───────────────────────────────────────────────────────────────────────────────

def workflow_files(target):
    d = os.path.join(target, WF)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(WF, n) for n in os.listdir(d)
                  if n.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(d, n)))


def pins_in(text):
    """Every gates-SHA occurrence: (kind, sha). kind is 'gates', 'review' or 'gates_ref'."""
    out = [(m.group(2), m.group(3)) for m in GATES_USES.finditer(text)]
    out += [("gates_ref", m.group(2)) for m in GATES_REF.finditer(text)]
    return out


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
    text = GATES_USES.sub(lambda m: m.group(1) + sha + uses_comment, text)
    return GATES_REF.sub(lambda m: m.group(1) + sha + m.group(3) + ref_comment, text)


def comment_of(text, regex, group):
    m = regex.search(text)
    if not m or not m.group(group):
        raise Problem("the release's caller has no version comment beside its pin")
    return m.group(group)


# ── init ───────────────────────────────────────────────────────────────────────────────

def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def plan(target, sha, today, history, release=RELEASE, adopt=False, live=False):
    """Return (writes, notes). writes: [(relpath, new_text, verb)]. Raises Problem with every
    problem found, so a run that cannot finish writes nothing."""
    problems, writes, notes = [], [], []
    rel_version = release_version(release)

    for path, src in STANDING:
        if live and path in LIVE_EXEMPT:
            notes.append("skip    %s (reads the standing orders live; no region)" % path)
            continue
        fresh = read(os.path.join(release, src))
        full = os.path.join(target, path)
        if not os.path.exists(full):
            writes.append((path, date_marker(fresh, today), "wrote"))
            continue
        cur = read(full)
        try:
            parse_region(cur)
        except Problem as p:
            if adopt and not has_any_marker(cur):
                lines = cur.split("\n")
                at = 1 if lines and lines[0].startswith("# ") else 0
                b, e = parse_region(fresh)
                block = fresh.split("\n")[b:e + 1]
                new = "\n".join(lines[:at] + [""] + block + [""] + lines[at:])
                writes.append((path, date_marker(new, today), "adopted"))
                notes.append("adopt   %s: the region went in after the first heading; every "
                             "line already there is still there. Delete the stale copies of "
                             "template rules outside it in this same PR, and read the diff." % path)
                continue
            problems.append("%s: %s. Left untouched.%s" % (
                path, p, " Run with --adopt to wrap it." if not has_any_marker(cur) else ""))
            continue
        have = [v for _, v in markers(region_body(cur)) if v]
        if have and max(have) > rel_version:
            problems.append("%s: its marker (%s) is ahead of this release (%s); run init from "
                            "the release that marker names." % (path, vstr(max(have)), vstr(rel_version)))
            continue
        body = region_body(fresh)
        if undate(region_body(cur)) == undate(body):
            notes.append("same    %s" % path)
            continue
        writes.append((path, splice(cur, date_marker(body, today)), "updated region"))

    # Callers. A repo may carry the toolkit's one-file shape (both jobs in one workflow), so
    # existing occurrences are found wherever they are rather than assumed at tier1.yml.
    wfs = workflow_files(target)
    texts = {p: read(os.path.join(target, p)) for p in wfs}
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
        kinds.update(k for k, _ in found)
        stuck = sorted({s for _, s in found if not is_ancestor(history, s, sha)})
        if stuck:
            problems.append("%s pins %s, which this checkout's history does not contain "
                            "(a newer release, or one it cannot place). init moves a pin only "
                            "forward; nothing was written." % (p, ", ".join(s[:12] for s in stuck)))
            continue
        new = move_pins(t, sha, uses_c, ref_c)
        if new != t:
            writes.append((p, new, "moved pin"))
            notes.append("note    %s: only its pins moved. Its body is this repo's; compare it "
                         "with init/release/%s if the release changed the caller." % (
                             p, TIER1[1] if "gates" in {k for k, _ in found} else REVIEWC[1]))
        else:
            notes.append("same    %s" % p)
    for (path, src), kind in ((TIER1, "gates"), (REVIEWC, "review")):
        if kind in kinds:
            continue
        if path in texts:
            problems.append("%s exists but does not call the toolkit's %s.yml; left untouched." % (path, kind))
            continue
        writes.append((path, move_pins(read(os.path.join(release, src)), sha, uses_c, ref_c), "wrote"))
    if SECURITY[0] in texts or any(SECURITY_USES in t for t in texts.values()):
        notes.append("same    %s (a security caller exists; init never rewrites its pin)" % SECURITY[0])
    else:
        writes.append((SECURITY[0], read(os.path.join(release, SECURITY[1])), "wrote"))

    if problems:
        raise Problem("\n".join(problems))
    return writes, notes


def apply(target, writes):
    for path, text, _ in writes:
        full = os.path.join(target, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def git(*args):
    r = subprocess.run(["git", "-C", TOOLKIT] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def cmd_init(target, sha, adopt, live):
    if not re.fullmatch(SHA40, sha or ""):
        print("init: --sha must be the 40-character SHA you were given for this release "
              "(the Tier 1 caller's pin on the default branch, or one the founder states).")
        return 2
    rc, head = git("rev-parse", "HEAD")
    if rc != 0 or head != sha:
        print("init: this toolkit checkout is at %s, not the --sha you named (%s). Clone at "
              "that SHA and confirm `git rev-parse HEAD` before running init." % (head or "?", sha))
        return 1
    rc, dirty = git("status", "--porcelain", "--untracked-files=no")
    if rc != 0 or dirty:
        print("init: this toolkit checkout has local changes; run init only from a clean "
              "checkout of the SHA you named.")
        return 1
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        writes, notes = plan(target, sha, today, TOOLKIT, adopt=adopt, live=live)
    except Problem as p:
        for line in str(p).split("\n"):
            print("PROBLEM " + line)
        print("init: nothing was written.")
        return 1
    apply(target, writes)
    for path, _, verb in writes:
        print("%-7s %s" % (verb.split()[0], path) + ("" if verb in ("wrote",) else "  (%s)" % verb))
    for n in notes:
        print(n)
    print("init: done. Read the diff, run the gates, and open a PR; init never commits or pushes.")
    return 0


# ── check ──────────────────────────────────────────────────────────────────────────────

def check(target, release=RELEASE, live=False):
    """Return a list of (code, message). Empty means green."""
    rel = release_version(release)
    fails, seen = [], {}
    claude_marked = (not live and os.path.isfile(os.path.join(target, "CLAUDE.md"))
                     and any(v for _, v in markers(read(os.path.join(target, "CLAUDE.md")))))
    ptr = POINTER if claude_marked else POINTER_RELEASE
    for path, _ in STANDING:
        if live and path in LIVE_EXEMPT:
            continue
        full = os.path.join(target, path)
        if not os.path.isfile(full):
            fails.append(("missing-file", "%s: expected a standing-orders file at %s, found none. "
                          "See %s." % (path, vstr(rel), ptr)))
            continue
        ms = markers(read(full))
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
    if not occ:
        fails.append(("pin-missing", "expected the toolkit pinned by SHA in %s, found no "
                      "Chiibitsu-Labs/gates pin. See %s." % (WF, ptr)))
    elif len({s for _, _, s in occ}) > 1:
        fails.append(("pin-split", "expected every gates-SHA occurrence to name one commit, found %s. "
                      "See %s." % ("; ".join("%s %s %s" % (p, k, s[:12]) for p, k, s in occ), ptr)))
    return fails


def cmd_check(target, live):
    fails = check(target, live=live)
    for code, msg in fails:
        print("FAIL [standing-orders] %s: %s" % (code, msg))
    if fails:
        return 1
    print("ok [standing-orders] markers equal the pinned release (%s) and the gates pins agree"
          % vstr(release_version()))
    return 0


# ── the team-safe copy ─────────────────────────────────────────────────────────────────
#
# Derived from vibeOS's templates by fixed edits, each asserted to match exactly as often as
# expected, so a template that moved under an edit stops the derivation instead of letting
# the private text through. The shape checks below then run over the result; they match
# SHAPES and an allowlist, never a list of the private strings, which would publish them.

POINTER_WORD = "pointer"  # PROPOSED, the founder's word to pick: see the PR.
POINTER_PARAGRAPH = (
    "**The %s line.** A session that can read `chiibitsu/vibeOS` also follows that repo's "
    "`chiibitsu/vibeOS:templates/claude-md.md`: its rules (its template-owned region and its "
    "repo-specific gotchas), never its placeholders, at its published head, never a "
    "checkout's working tree, a branch or a fork (`chiibitsu/vibeOS:canon/decisions.md`, "
    "2026-11-09). Where the two differ, the stricter rule wins; where neither is stricter, "
    "vibeOS's does. A session that cannot read `chiibitsu/vibeOS` skips this paragraph."
) % POINTER_WORD

IN_REPO_DIRS = "canon|docs|ops|templates|team|product|patterns|scripts"


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


def _drop_section(text, heading):
    rx = re.compile(r"^## " + re.escape(heading) + r"\n.*?(?=^## |^<!-- gates:template-owned:end -->)",
                    re.M | re.S)
    return _sub(text, rx, "", name="section '%s'" % heading)


def _founder(text):
    text = re.sub(r"\bChii's\b", "the founder's", text)
    text = re.sub(r"\bChii\b", "the founder", text)
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
    t = _sub(t, "- Deploy: previews per PR. On Chii's Vercel a merge to main does not deploy "
                "itself: see \"Deploy after merge, then check it is live\" below.",
             "- Deploy: previews per PR.")
    t = _drop_section(t, "Deploy after merge, then check it is live")
    t = _drop_section(t, "Log the work — the Time Dividend ledger")
    t = _sub(t, re.compile(r"^- \*\*\d{4}-\d{2}-\d{2} — A session holding the [^\n]*vault MCP "
                           r"connector.*?(?=^- )", re.M | re.S), "", name="the vault-memory gotcha")
    t = _sub(t, "Chii is `Asia/Manila` (UTC+8); containers run UTC. That offset is the stable "
                "fact worth having here; the current time is not, and writing one into this "
                "file would be a lie by the time anyone read it.",
             "Containers run UTC. This copy does not carry the founder's timezone, and a\n"
             "current time written here would be a lie by the time anyone read it.")
    t = _sub(t, "The offset is above and no bedtime is written anywhere in this repo, so that "
                "clause resolves by defaulting rather than guessing.",
             "No offset and no bedtime is written in this copy, so that clause resolves by\n"
             "defaulting rather than guessing.")
    t = _sub(t, re.compile(r"The run they were taken from is written up in `chiibitsu/[^`]+`, "
                           r"which states which figures are measured and which estimated — "
                           r"check it there rather than trusting these numbers because they "
                           r"are written down\."),
             "The run they were taken from is written up in the founder's own records, which "
             "state which figures are measured and which estimated; where they cannot be read, "
             "treat these numbers as reported, not measured.", name="the cost-report citation")
    t = _sub(t, re.compile(r"\(`chiibitsu/[^`]+` and the apps\s+made from it"),
             "(the founder's Next.js app template and the apps\nmade from it",
             name="the app template's name")
    t = _sub(t, "vibeOS `docs/03`'s \"Working with Chii\" holds",
             "vibeOS `docs/03`'s section on working with the founder holds")
    t = _sub(t, "never copy from vibeOS `main`, which no marker-scheme repo reads.",
             "never copy from vibeOS `main`: a marker-scheme repo reads it only through its %s "
             "line, below, and never copies from it." % POINTER_WORD)
    return t


def derive_text(name, text):
    if name == "claude-md.md":
        text = derive_claude(text)
    if name.endswith(".md"):
        text = _locators(text)
        text = _founder(text)
        text = _insert_pointer(text)
    else:
        text = _founder(text)
    return text


# Shapes the team-safe copy must not carry. Each is a shape or an allowlist; none names a
# private string.
LOCATOR_ALLOW = {"chiibitsu/vibeos", "chiibitsu-labs/gates", "chiibitsu-labs/security-checks"}
OWNER_QUALIFIED = re.compile(r"(?<![\w./-])(chiibitsu[\w-]*)/([A-Za-z0-9._-]*[A-Za-z0-9_-])", re.I)
COLON_LOCATOR = re.compile(r"(?<![\w./:-])([A-Za-z0-9][\w-]*)/([\w.-]+)(?=:[\w./-]|#\d)")
GITHUB_URL = re.compile(r"github\.com/([\w-]+)/([\w.-]*[\w-])", re.I)
IANA = re.compile(r"\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific|Etc)/[A-Z][\w+-]+")
OFFSET = re.compile(r"\b(?:UTC|GMT)\s*[+\u2212-]\s*\d{1,2}(?::?\d{2})?\b")
NAME = re.compile(r"\bchii\b", re.I)
LEDGER_HEADING = re.compile(r"^#{1,6}\s.*\bledger\b", re.I | re.M)
VAULT_LEAD = re.compile(r"\*\*[^*\n]*\bvault\b[^*\n]*\bconnector\b[^*\n]*\*\*", re.I)


def leaks(text):
    """Every omitted shape the text still carries, as human-readable strings."""
    out = []
    for rx in (OWNER_QUALIFIED, COLON_LOCATOR, GITHUB_URL):
        for m in rx.finditer(text):
            if rx is COLON_LOCATOR and re.fullmatch(IN_REPO_DIRS + r"|\.github|gates|fixtures|init",
                                                    m.group(1)):
                continue  # an in-repo path with a line number or anchor, not a locator
            if ("%s/%s" % (m.group(1), m.group(2))).lower() not in LOCATOR_ALLOW:
                out.append("an owner/repo locator outside the allowlist: %s" % m.group(0))
    for rx, what in ((IANA, "a timezone name"), (OFFSET, "a UTC offset"),
                     (NAME, "the founder's name"), (LEDGER_HEADING, "the time-ledger heading"),
                     (VAULT_LEAD, "the vault-memory gotcha's bold lead")):
        out += ["%s: %s" % (what, m.group(0).strip()) for m in rx.finditer(text)]
    return out


RELEASE_FILES = ("claude-md.md", "agents-md.md", "review-md.md",
                 "tier1-ci.yml", "review-ci.yml", "security-ci.yml")


def protocol_of(vibeos):
    """The version docs/02 declares, which every template marker must equal."""
    text = read(os.path.join(vibeos, "docs", "02-vibecoding-protocol.md"))
    m = re.search(r"^\*\*Protocol version: v(\d+)\.(\d+)(?:\.(\d+))?\.\*\*", text, re.M)
    if not m:
        raise Problem("derive: docs/02 declares no `**Protocol version: vX.Y.**` line")
    return tuple(int(x or 0) for x in m.groups())


def cmd_derive(vibeos, out=RELEASE):
    try:
        want = protocol_of(vibeos)
        texts = {}
        for name in RELEASE_FILES:
            src = read(os.path.join(vibeos, "templates", name))
            if name.endswith(".md"):
                ms = markers(src)
                if [v for _, v in ms] != [want]:
                    raise Problem("derive: templates/%s carries %s; docs/02 declares %s" % (
                        name, [vstr(v) if v else "?" for _, v in ms], vstr(want)))
            texts[name] = derive_text(name, src)
            bad = leaks(texts[name])
            if bad:
                raise Problem("derive: %s still carries %s" % (name, "; ".join(bad)))
        rc = subprocess.run(["git", "-C", vibeos, "rev-parse", "HEAD"], capture_output=True, text=True)
        source = rc.stdout.strip() if rc.returncode == 0 else "unknown"
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
    print("derive: wrote %d files to %s at %s from chiibitsu/vibeOS@%s"
          % (len(texts), os.path.relpath(out), vstr(want), source[:12]))
    return 0


# ── selftest ───────────────────────────────────────────────────────────────────────────

def selftest():
    failed = []

    def ok(cond, what):
        print(("ok  " if cond else "FAIL ") + what)
        if not cond:
            failed.append(what)

    def snapshot(root):
        out = {}
        for d, _, fs in os.walk(root):
            if "/.git" in d + "/" and os.path.relpath(d, root).split(os.sep)[0] == ".git":
                continue
            for f in fs:
                p = os.path.join(d, f)
                out[os.path.relpath(p, root)] = read(p)
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
    strip = lambda t: GATES_REF.sub("gates_ref: X", GATES_USES.sub("USES@X", t))
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
    old = cur.replace("docs/02 " + relv, "docs/02 v%d.%d" % (rel[0], rel[1] - 1) if rel[1] else "docs/02 v%d.9" % (rel[0] - 1), 1)
    old = splice(old, region_body(old) + "\nan obsolete template rule")
    above, below = "# CLAUDE.md — acme\n\nRepo rule above. \u00e9\r\n", "\n## Ours\n- keep me\n"
    bb, be = parse_region(old)
    lines = old.split("\n")
    old = above + "\n".join(lines[bb:be + 1]) + below
    write(b, "CLAUDE.md", old)
    do(b)
    new = read(os.path.join(b, "CLAUDE.md"))
    ok(new.startswith(above) and new.endswith(below), "a one-version-behind file keeps every byte outside its region")
    ok("an obsolete template rule" not in new and undate(region_body(new)) == region_body(cur),
       "and its region is the release's")

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

    # 5. adoption wraps a region into a file with none, keeping every existing byte.
    d = fresh("adopt")
    legacy = "# CLAUDE.md — chibie-like\n\n## Old rules\n- a stale copy of a template rule\n"
    write(d, "CLAUDE.md", legacy)
    do(d, adopt=True)
    new = read(os.path.join(d, "CLAUDE.md"))
    ok(all(l in new.split("\n") for l in legacy.split("\n")) and parse_region(new),
       "--adopt inserts one region into a file with none and keeps every existing line")
    two = fresh("adopt-malformed")
    write(two, "CLAUDE.md", shapes["two pairs"])
    ok(problem(two, adopt=True) is not None, "--adopt never touches a file that already has markers")

    # 6. an existing security caller keeps its pin.
    d = fresh("security")
    mine = read(os.path.join(RELEASE, SECURITY[1])).replace(
        re.search(SHA40, read(os.path.join(RELEASE, SECURITY[1]))).group(0), "1" * 40)
    write(d, SECURITY[0], mine)
    do(d)
    ok(read(os.path.join(d, SECURITY[0])) == mine, "an existing security caller keeps its pin")

    # 7. pins move only forward.
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
    t = read(os.path.join(d, TIER1[0])).replace(N, "2" * 40)
    write(d, TIER1[0], t)
    ok(problem(d, sha=N1) is not None, "a pin the checkout's history does not contain stops the run")
    d = fresh("marker-ahead")
    do(d, sha=N)
    write(d, "CLAUDE.md", read(os.path.join(d, "CLAUDE.md")).replace(
        "docs/02 " + relv, "docs/02 v%d.%d" % (rel[0], rel[1] + 1), 1))
    before = snapshot(d)
    ok(problem(d, sha=N) is not None and snapshot(d) == before,
       "a marker ahead of this release stops the run, so init never downgrades a region")
    d = fresh("one-file-caller")
    write(d, WF + "/gates.yml",
          "jobs:\n  gates:\n    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s # v1.2.2\n"
          "    with:\n      gates_ref: %s # v1.2.2\n  review:\n"
          "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s # v1.2.2\n" % (N, N, N))
    do(d, sha=N1)
    ok(not os.path.exists(os.path.join(d, TIER1[0])) and not os.path.exists(os.path.join(d, REVIEWC[0]))
       and {s for _, s in pins_in(read(os.path.join(d, WF + "/gates.yml")))} == {N1},
       "a one-file caller has its three pins moved and gains no second caller")
    d = fresh("old-name")
    write(d, WF + "/gates.yml", "    uses: chiibitsu/gates/.github/workflows/gates.yml@%s\n" % N)
    ok(problem(d) is not None, "a caller still naming chiibitsu/gates stops the run")

    # 8. a live-read repo gets no CLAUDE.md or AGENTS.md region, and its check skips them.
    d = fresh("live")
    do(d, live=True)
    ok(not os.path.exists(os.path.join(d, "CLAUDE.md")) and not os.path.exists(os.path.join(d, "AGENTS.md"))
       and os.path.exists(os.path.join(d, "REVIEW.md")),
       "--live-standing-orders writes REVIEW.md and no CLAUDE.md or AGENTS.md")
    ok(check(d, live=True) == [], "and the live check is green on it")
    ok({c for c, _ in check(d)} == {"missing-file"}, "while the full check still wants the two files")

    # 9. the check is red for each failure, and only that failure's code fires.
    def checked(mutate):
        d = fresh("check-%d" % len(os.listdir(work)))
        do(d)
        mutate(d)
        return {c for c, _ in check(d)}

    def edit(path, f):
        return lambda d: write(d, path, f(read(os.path.join(d, path))))

    behind = "v%d.%d" % (rel[0], rel[1] - 1) if rel[1] else "v%d.9" % (rel[0] - 1)
    ahead = "v%d.%d" % (rel[0], rel[1] + 1)
    every = lambda f: (lambda d: [edit(p, f)(d) for p, _ in STANDING])
    cases = {
        "missing-file": (lambda d: os.remove(os.path.join(d, "REVIEW.md")), {"missing-file"}),
        "missing-marker": (edit("AGENTS.md", lambda t: MARKER.sub("", t)), {"missing-marker"}),
        "duplicate-marker": (edit("CLAUDE.md", lambda t: t + "\n" + MARKER.search(t).group(0) + "\n"),
                             {"duplicate-marker"}),
        "behind": (every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1)), {"behind"}),
        "ahead": (every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + ahead, 1)), {"ahead"}),
        "split": (lambda d: (edit("REVIEW.md", lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1))(d)),
                  {"behind", "split"}),
        "pin-split": (edit(REVIEWC[0], lambda t: t.replace(N, N1)), {"pin-split"}),
        "pin-missing": (lambda d: [os.remove(os.path.join(d, p)) for p in (TIER1[0], REVIEWC[0])],
                        {"pin-missing"}),
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
    for name in RELEASE_FILES:
        text = read(os.path.join(RELEASE, name))
        ok(leaks(text) == [], "init/release/%s carries none of the omitted shapes" % name)
        if name.endswith(".md"):
            ok(text.count(POINTER_PARAGRAPH) == 1 and POINTER_PARAGRAPH in region_body(text),
               "init/release/%s carries the %s line once, inside its region" % (name, POINTER_WORD))
            ok([v for _, v in markers(text)] == [rel], "init/release/%s carries one marker, at %s" % (name, relv))
    probes = {
        "chiibitsu/example-private": True, "`chiibitsu/example:path/x.md`": True,
        "acme/widget:src/x.py": True, "acme/widget#12": True, "github.com/acme/widget": True,
        "Asia/Tokyo": True, "UTC+8": True, "GMT -05:00": True, "Chii's call": True, "chii": True,
        "## Log it: the ledger": True, "**A vault MCP connector rule**": True,
        "Chiibitsu Labs": False, "Chiibitsu-Labs/gates": False, "chiibitsu/vibeOS": False,
        "`docs/04:12`": False, "containers run UTC": False, "the ledger, unheaded": False,
    }
    for probe, bad in probes.items():
        ok(bool(leaks(probe)) == bad, "the shape check %s %r" % ("catches" if bad else "passes", probe))

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
        target, rest = os.path.abspath(argv[2]), argv[3:]
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
