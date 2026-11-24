#!/usr/bin/env python3
# Part of Chiibitsu-Labs/gates by Angeline S. Viray (Chiibitsu Labs). MIT. https://github.com/Chiibitsu-Labs/gates
"""gates init, the standing-orders version check, and the team-safe copy's derivation.

    gates_init.py init   TARGET --sha SHA
    gates_init.py check  TARGET
    gates_init.py derive VIBEOS_CHECKOUT
    gates_init.py --selftest

WHAT INIT WRITES. `CLAUDE.md`, `AGENTS.md`, `REVIEW.md` and the three callers
(`.github/workflows/tier1.yml`, `review.yml`, `security.yml`), from the copies in
`init/release/`. One more kind of file can change: a workflow that already calls this
toolkit has its gates pin lines moved, and nothing else in it. init never touches a
`.gitignore`, a hook, a README or a board, and never commits or pushes.

ONE RULE FOR EVERY MATCH: DETECT WIDE, ACCEPT NARROW, REFUSE THE REST. A line is DETECTED by
a loose test (a region marker's text anywhere on it; the toolkit's workflow path anywhere on
it) and ACCEPTED only in the one exact form init itself writes. Detected and not accepted is
refused by init and red in the check, so a spelling init can see is never guessed at. Two
shapes it cannot see by reading lines are named in the README's Known gaps.

STANDING-ORDERS FILES. Each holds one template-owned region between the two marker lines
below, each alone on its line at column 0. init rewrites the region and keeps every byte
outside it. Any other shape is refused and the file left untouched: no region, more than
one marker of either kind, the end before the begin, a marker inside a fenced block, or the
marker text anywhere else on a line. A file with no region is NOT adopted by this release,
and a repo that reads the standing orders live is not handled: both are a later slice's.

WHEN A PIN MOVES. To the SHA of the checkout init runs from, which must be a full clone at
a tagged release, and only forward: an existing pin moves only if that checkout's history
contains it. Anything else stops the whole run.

ALL OR NOTHING. Every file is planned first and any problem stops the run before a write.
Writes go to new, randomly named temporary files first and are then moved into place.

THE CHECK asserts the markers and the pins, never the body. It reads the expected version
from its own checkout's PROTOCOL: in CI that checkout is the pinned release. It edits nothing.

EVERY REFUSAL AND EVERY RED IS NAMED in REFUSALS and REDS below, and `--selftest` fails
unless each one fired at least once during the run and the two lists match the code, by
name, both ways. That proves each one can happen; it does not prove every condition inside
every guard, which the selftest's cases cover only as far as each ok-line says.
"""
import contextlib
import datetime
import hashlib
import io
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
MARKER = re.compile(r"\*\*Protocol version: vibeOS docs/02 v([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\*\*")
# Detect wide: any line that starts a Protocol version claim counts as a marker line, so an
# older-style or mangled one is a marker that is there and wrong, never "no marker".
MARKER_LOOSE = re.compile(r"\*\*Protocol version:")
DATE = re.compile(r"(\(this file instantiated `)(<YYYY-MM-DD>|[0-9]{4}-[0-9]{2}-[0-9]{2})(`\))")

STANDING = (  # (path in the repo, file in init/release)
    ("CLAUDE.md", "claude-md.md"),
    ("AGENTS.md", "agents-md.md"),
    ("REVIEW.md", "review-md.md"),
)
WF = ".github/workflows"
TIER1 = (WF + "/tier1.yml", "tier1-ci.yml")
REVIEWC = (WF + "/review.yml", "review-ci.yml")
SECURITY = (WF + "/security.yml", "security-ci.yml")

SHA40 = re.compile(r"[0-9a-f]{40}")
# Detect wide: the toolkit's (or its old name's) workflow path anywhere on a line, any case.
TOOLKIT_MENTION = re.compile(r"(?i)chiibitsu(?:-labs)?/gates/\.github/workflows/")
SECURITY_MENTION = re.compile(r"(?i)chiibitsu-labs/security-checks/\.github/workflows/")
# ...and the word `gates_ref` anywhere on a line before any `#`, in any letter case, in a
# file that mentions the toolkit: an anchored, tagged, quoted or flow-style key included.


def ref_mention(line):
    return "gates_ref" in line.split("#", 1)[0].lower()
# Accept narrow: the whole line, exactly as init writes it. Any trailing comment is allowed
# and is replaced with the release's label when the pin moves.
USES_LINE = re.compile(r"( +uses: Chiibitsu-Labs/gates/\.github/workflows/(gates|review)\.yml@)"
                       r"([0-9a-f]{40})((?:[ \t]+#.*)?)")
REF_LINE = re.compile(r"( +gates_ref: )([0-9a-f]{40})((?:[ \t]+#.*)?)")
SECURITY_LINE = re.compile(r" +uses: Chiibitsu-Labs/security-checks/\.github/workflows/security\.yml@"
                           r"[0-9a-f]{40}(?:[ \t]+#.*)?")
LABEL = re.compile(r"[ \t]+# (v[0-9][0-9.]*)")

POINTER = "the Protocol version paragraph in CLAUDE.md"
POINTER_RELEASE = ("the Protocol version paragraph of init/release/claude-md.md in "
                   "Chiibitsu-Labs/gates at the SHA this repo pins")

REFUSALS = (
    "sha-shape", "sha-not-head", "sha-not-release", "toolkit-shallow", "toolkit-unclean",
    "target-not-dir",
    "symlink", "not-a-directory", "not-a-file", "hard-link", "unreadable", "mixed-eol",
    "odd-line-break",
    "region-none", "region-many", "region-unpaired", "region-reversed", "region-fenced",
    "region-fence-unclear", "region-not-col0", "marker-shape", "marker-ahead",
    "caller-noncanonical", "pin-unpaired", "pin-not-ancestor", "caller-unrelated",
    "security-noncanonical",
    "write-prepare-failed", "write-failed", "release-no-protocol", "release-no-label",
    "derive-edit-count", "derive-paragraph", "derive-bare-path", "derive-no-marker",
    "derive-caller-form", "derive-leak", "derive-version-mismatch", "derive-no-protocol",
    "derive-no-citation", "derive-unclean", "derive-origin", "derive-no-main", "derive-not-main",
)
REDS = (
    "missing-file", "unreadable-file", "odd-line-break", "missing-marker", "unreadable-marker",
    "duplicate-marker", "region-unreadable", "marker-outside-region", "behind", "ahead", "split",
    "caller-noncanonical", "security-noncanonical", "caller-unreadable", "pin-missing", "pin-unpaired",
    "pin-split",
)
FIRED = set()  # every refusal and red that actually happened in this process


class Problem(Exception):
    pass


def refuse(site, message):
    """Where a Problem is made. `site` must be one of REFUSALS."""
    assert site in REFUSALS, site
    FIRED.add("refuse:" + site)
    return Problem(message)


def gathered(problems):
    """The refusals plan() collected, each already made by refuse(), as one Problem."""
    return Problem("\n".join(problems))


def red(fails, code, message):
    """The only way the check records a failure. `code` must be one of REDS."""
    assert code in REDS, code
    FIRED.add("red:" + code)
    fails.append((code, message))


# ── files ──────────────────────────────────────────────────────────────────────────────

class Doc:
    """A text file read once: UTF-8, its byte-order mark and its line ending set aside, so
    every rule below sees plain LF text and the file is written back as it came."""

    def __init__(self, text, eol="\n", bom=""):
        self.text, self.eol, self.bom = text, eol, bom

    def dump(self, text):
        return self.bom + (text.replace("\n", "\r\n") if self.eol == "\r\n" else text)


# Line breaks other than LF and CRLF. A YAML or Markdown reader may split a line on one of
# these where this script, reading by LF, would not, so a file that carries one is refused
# by init and red in the check rather than read two ways.
ODD_BREAK = re.compile("\r(?!\n)|[\x0b\x0c\x85\u2028\u2029]")


def show(name):
    """A path as it is safe to print: a file name is the repo's to choose, a log line is not."""
    return name.encode("unicode_escape").decode("ascii")


def load(path, rel):
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise refuse("unreadable", "%s cannot be read as UTF-8 text (%s). Left untouched."
                     % (rel, e.__class__.__name__))
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    raw = raw[len(bom):]
    if ODD_BREAK.search(raw):
        raise refuse("odd-line-break", "%s carries a line break that is neither LF nor CRLF (a "
                     "lone carriage return, or a Unicode line separator), which another reader "
                     "may split differently. Left untouched." % rel)
    crlf, lf = raw.count("\r\n"), raw.count("\n")
    if "\r" not in raw:
        return Doc(raw, "\n", bom)
    if crlf == lf:
        return Doc(raw.replace("\r\n", "\n"), "\r\n", bom)
    raise refuse("mixed-eol", "%s mixes line endings (%d CRLF, %d LF-only); give it one, then "
                 "run init again. Left untouched." % (rel, crlf, lf - crlf))


def read_raw(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def read(path):
    """Loose read, for release files and the selftest: a byte-order mark dropped, CRLF folded."""
    return read_raw(path).lstrip("\ufeff").replace("\r\n", "\n")


def guard(target, rel, leaf=True):
    """Refuse a path init would write if it, or a directory on the way, is a symlink; if a
    directory on the way is a file; if the path is not a regular file; or if it is a file
    with a second hard link."""
    p, parts = target, rel.split("/")
    for i, part in enumerate(parts):
        p = os.path.join(p, part)
        if os.path.islink(p):
            raise refuse("symlink", "%s: %s is a symlink; init never writes through one. Left "
                         "untouched." % (rel, "/".join(parts[:i + 1])))
        if (i < len(parts) - 1 or not leaf) and os.path.exists(p) and not os.path.isdir(p):
            raise refuse("not-a-directory", "%s: %s is not a directory, so init cannot write "
                         "there. Left untouched." % (rel, "/".join(parts[:i + 1])))
    if not leaf:
        return p
    if os.path.exists(p) and not os.path.isfile(p):
        raise refuse("not-a-file", "%s exists and is not a regular file. Left untouched." % rel)
    if os.path.isfile(p) and os.stat(p).st_nlink > 1:
        raise refuse("hard-link", "%s has another hard link; init never writes through one. "
                     "Left untouched." % rel)
    return p


# ── regions ────────────────────────────────────────────────────────────────────────────

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
FENCE_ANYWHERE = re.compile(r"`{3,}|~{3,}")


def fence_map(lines):
    """For each line, True when it opens, sits inside or closes a fenced block. A fence
    closes only on a line of the same character, at least as long, and nothing else."""
    out, open_ = [], None
    for l in lines:
        if open_ is None:
            m = FENCE.match(l)
            if m and not (m.group(1)[0] == "`" and "`" in l[m.end():]):
                open_ = m.group(1)
                out.append(True)
                continue
            out.append(False)
        else:
            out.append(True)
            m = FENCE.match(l)
            if m and m.group(1)[0] == open_[0] and len(m.group(1)) >= len(open_) and not l[m.end():].strip():
                open_ = None
    return out


def parse_region(text, rel="the file"):
    """Return (begin_line, end_line) indexes of the one well-formed pair, or refuse, naming
    the shape. `text` is LF text. Detect wide: MARKER_KEY anywhere on a line. Accept narrow:
    the line is exactly BEGIN or END, at column 0, outside any fence."""
    lines = text.split("\n")
    begins, ends, unclear, in_comment = [], [], 0, False
    for i, (line, fenced) in enumerate(zip(lines, fence_map(lines))):
        if MARKER_KEY not in line:
            # fence_map reads fences the plain way. A Markdown renderer reads a fence run
            # differently when it is not a plain fence line (after a list marker, in prose,
            # indented four or more) or sits inside an HTML comment. One such line above a
            # marker and the two readings may disagree about whether that marker is fenced.
            runs = FENCE_ANYWHERE.findall(line)
            if runs and not begins and (in_comment or len(runs) != 1 or not FENCE.match(line)):
                unclear = unclear or i + 1
            if "<!--" in line or in_comment:
                in_comment = "-->" not in line.split("<!--")[-1]
            continue
        if line in (BEGIN, END) and not fenced and unclear:
            raise refuse("region-fence-unclear", "%s: line %d carries a code-fence run that is not "
                         "a plain fence line (or sits in an HTML comment), so init cannot tell "
                         "whether the marker at line %d is inside a fenced block. Left "
                         "untouched." % (rel, unclear, i + 1))
        if line not in (BEGIN, END):
            raise refuse("region-not-col0", "%s: line %d carries region marker text but is not "
                         "exactly one marker, alone, at column 0. Left untouched." % (rel, i + 1))
        if fenced:
            raise refuse("region-fenced", "%s: a region marker sits inside a fenced block (line "
                         "%d). Left untouched." % (rel, i + 1))
        (begins if line == BEGIN else ends).append(i)
    if not begins and not ends:
        raise refuse("region-none", "%s: no template-owned region. This release's init does not "
                     "adopt a file that has none, and does not handle a repo that reads the "
                     "standing orders live; both are a later slice's. Left untouched." % rel)
    if len(begins) > 1 or len(ends) > 1:
        raise refuse("region-many", "%s: more than one region marker pair (%d begin, %d end). "
                     "Left untouched." % (rel, len(begins), len(ends)))
    if len(begins) != len(ends):
        raise refuse("region-unpaired", "%s: an unpaired region marker. Left untouched." % rel)
    if ends[0] < begins[0]:
        raise refuse("region-reversed", "%s: the end marker comes before the begin marker. "
                     "Left untouched." % rel)
    return begins[0], ends[0]


def region_body(text, rel="the file"):
    b, e = parse_region(text, rel)
    return "\n".join(text.split("\n")[b + 1:e])


def splice(text, body, rel="the file"):
    b, e = parse_region(text, rel)
    lines = text.split("\n")
    return "\n".join(lines[:b + 1] + body.split("\n") + lines[e:])


def undate(text):
    return DATE.sub(lambda m: m.group(1) + "<YYYY-MM-DD>" + m.group(3), text)


def date_marker(text, today):
    return DATE.sub(lambda m: m.group(1) + today + m.group(3), text, count=1)


def markers(text):
    """Every marker-shaped occurrence, two on one line included, as (line_no, version tuple
    or None for an unreadable one)."""
    out = []
    for i, line in enumerate(text.split("\n")):
        for hit in MARKER_LOOSE.finditer(line):
            m = MARKER.match(line, hit.start())
            out.append((i + 1, tuple(int(x or 0) for x in m.groups()) if m else None))
    return out


def vstr(v):
    return "v%d.%d" % v[:2] + (".%d" % v[2] if v[2] else "")


def release_version(release=RELEASE):
    for line in read(os.path.join(release, "PROTOCOL")).split("\n"):
        m = re.match(r"\s*protocol\s+v([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\s*$", line)
        if m:
            return tuple(int(x or 0) for x in m.groups())
    raise refuse("release-no-protocol", "init/release/PROTOCOL declares no `protocol vX.Y` line")


def release_label(release=RELEASE):
    """The one version label this release's callers carry beside their pins."""
    labels = set()
    for name in (TIER1[1], REVIEWC[1]):
        for line in read(os.path.join(release, name)).split("\n"):
            for rx, g in ((USES_LINE, 4), (REF_LINE, 3)):
                m = rx.fullmatch(line)
                if m:
                    lm = LABEL.fullmatch(m.group(g))
                    labels.add(lm.group(1) if lm else None)
    if len(labels) != 1 or None in labels:
        raise refuse("release-no-label", "init/release/%s and %s do not carry one and the same "
                     "version label beside every gates pin" % (TIER1[1], REVIEWC[1]))
    return labels.pop()


# ── pins ───────────────────────────────────────────────────────────────────────────────

def workflow_files(target):
    d = os.path.join(target, WF)
    if not os.path.isdir(d):
        return []
    return sorted(WF + "/" + n for n in os.listdir(d)
                  if n.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(d, n)))


def scan(text):
    """Read one workflow's LF text. Returns (pins, toolkit_bad, security_bad, security):
    pins          [(line_no, kind, sha)] for accepted lines, kind 'gates', 'review', 'gates_ref'
    toolkit_bad   [line_no] detected as naming the toolkit or a gates_ref, not in init's form
    security_bad  [line_no] detected as naming the security workflow, not in init's form
    security      True when an accepted security caller line is present
    A `gates_ref` key is only looked for in a file that mentions the toolkit."""
    lines = text.split("\n")
    mentions = any(TOOLKIT_MENTION.search(l) for l in lines)
    pins, bad, sec_bad, security = [], [], [], False
    for i, l in enumerate(lines):
        if TOOLKIT_MENTION.search(l):
            m = USES_LINE.fullmatch(l)
            if m:
                pins.append((i + 1, m.group(2), m.group(3)))
            else:
                bad.append(i + 1)
        elif mentions and ref_mention(l):
            m = REF_LINE.fullmatch(l)
            if m:
                pins.append((i + 1, "gates_ref", m.group(2)))
            else:
                bad.append(i + 1)
        elif SECURITY_MENTION.search(l):
            if SECURITY_LINE.fullmatch(l):
                security = True
            else:
                sec_bad.append(i + 1)
    return pins, bad, sec_bad, security


WITH_LINE = re.compile(r" *with:(?:[ \t]+#.*)?")


def unpaired(text, pins):
    """Line numbers of gates.yml calls and gates_ref lines that are not paired one to one
    inside the call's own job. Accept narrow: after the call's `uses:` line, and before any
    line indented less than it (the job's end), a `with:` key at the same indent as `uses:`
    holds exactly one accepted gates_ref line as its direct child. A gates_ref anywhere else
    (another job, another key, above its `uses:`) belongs to no call."""
    lines = text.split("\n")
    indent = lambda l: len(l) - len(l.lstrip(" "))
    refs = {n for n, k, _ in pins if k == "gates_ref"}
    claims, bad = {}, set()
    for n, k, _ in pins:
        if k != "gates":
            continue
        at, mine, in_with, child = indent(lines[n - 1]), [], False, None
        for j in range(n, len(lines)):
            l = lines[j]
            if not l.strip() or l.lstrip(" ").startswith("#"):
                continue
            ind = indent(l)
            if ind < at:
                break
            if ind == at:
                in_with, child = WITH_LINE.fullmatch(l) is not None, None
            elif in_with:
                child = ind if child is None else child
                if ind == child and j + 1 in refs:
                    mine.append(j + 1)
        if len(mine) != 1:
            bad.add(n)
        for r in mine:
            claims[r] = claims.get(r, 0) + 1
    return sorted(bad | {r for r in refs if claims.get(r, 0) != 1})


CANONICAL = ("`uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@<40-hex SHA>` (or review.yml) "
             "and `gates_ref: <40-hex SHA>`, each alone on its line, unquoted, as init writes them")
CANONICAL_SECURITY = ("`uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@<40-hex "
                      "SHA>`, alone on its line, unquoted")


def is_ancestor(history, old, new):
    """True when `old` is `new` or an ancestor of it in the git repo at `history`."""
    if old == new:
        return True
    if subprocess.run(["git", "-C", history, "cat-file", "-e", old + "^{commit}"],
                      capture_output=True).returncode != 0:
        return False
    return subprocess.run(["git", "-C", history, "merge-base", "--is-ancestor", old, new],
                          capture_output=True).returncode == 0


def move_pins(text, sha, label):
    """Set every accepted pin line to `sha` (None keeps each line's own SHA), replacing any
    trailing comment with the label."""
    out = []
    for l in text.split("\n"):
        m = USES_LINE.fullmatch(l)
        if m:
            l = m.group(1) + (sha or m.group(3)) + " # " + label
        else:
            m = REF_LINE.fullmatch(l)
            if m:
                l = m.group(1) + (sha or m.group(2)) + " # " + label
        out.append(l)
    return "\n".join(out)


# ── init ───────────────────────────────────────────────────────────────────────────────

def plan(target, sha, today, history, release=RELEASE):
    """Return (writes, notes). writes: [(relpath, final_text, verb)]. Raises one Problem
    carrying every refusal found, so a run that cannot finish writes nothing."""
    problems, writes, notes = [], [], []
    rel_version = release_version(release)
    label = release_label(release)

    def attempt(fn, *a):
        try:
            return fn(*a)
        except Problem as p:
            problems.append(str(p))
            return None

    for path, src in STANDING:
        full = attempt(guard, target, path)
        if full is None:
            continue
        fresh = read(os.path.join(release, src))
        if not os.path.exists(full):
            writes.append((path, date_marker(fresh, today), "wrote"))
            continue
        doc = attempt(load, full, path)
        if doc is None or attempt(parse_region, doc.text, path) is None:
            continue
        b_, e_ = parse_region(doc.text, path)
        ms = markers(doc.text)
        odd = ([n for n, v in ms if v is None] or [n for n, _ in ms if not b_ < n - 1 < e_]
               or [n for n, _ in ms][1:])
        if odd:
            problems.append(str(refuse("marker-shape", (
                "%s line %s: init expects at most one Protocol version marker, readable, inside "
                "the region. This file has one it cannot read, one outside the region, or more "
                "than one, and the check is red on the same thing. Remove or correct that line "
                "by hand, then run init again. Left untouched.")
                % (path, ", ".join(map(str, sorted(set(odd))))))))
            continue
        cur = region_body(doc.text, path)
        have = [v for _, v in markers(cur) if v]
        if have and max(have) > rel_version:
            problems.append(str(refuse("marker-ahead", (
                "%s: its marker (%s) is ahead of this release (%s). Either the marker was raised "
                "by hand, so lower it to %s, or the repo is on a newer release, so run init from "
                "that release's SHA, taken from the caller's pin on the default branch or from "
                "the founder.") % (path, vstr(max(have)), vstr(rel_version), vstr(rel_version)))))
            continue
        body = region_body(fresh)
        if undate(cur) == undate(body):
            notes.append("same    %s" % path)
            continue
        writes.append((path, doc.dump(splice(doc.text, date_marker(body, today), path)), "updated region"))

    # Callers. A repo may carry the one-file shape (both jobs in one workflow), so accepted
    # pins are found wherever they are rather than assumed at tier1.yml.
    if attempt(guard, target, WF, False) is None:
        raise gathered(problems)
    docs, kinds, has_security, reported = {}, set(), False, set()
    for p in workflow_files(target):
        if attempt(guard, target, p) is None:
            continue
        doc = attempt(load, os.path.join(target, p), p)
        if doc is not None:
            docs[p] = doc
    for p, doc in docs.items():
        pins, bad, sec_bad, security = scan(doc.text)
        if sec_bad:
            problems.append(str(refuse("security-noncanonical", (
                "%s line %s names the security-checks workflow in a form init does not read. The "
                "accepted form is %s. Fix or remove the line by hand, then run init again.")
                % (show(p), ", ".join(map(str, sec_bad)), CANONICAL_SECURITY))))
        if bad:
            problems.append(str(refuse("caller-noncanonical", (
                "%s line %s names the toolkit, or a gates_ref, in a form init does not read. The "
                "accepted forms are %s. Fix the line by hand, keeping the commit it names (ask the "
                "founder if it names none), then run init again.")
                % (show(p), ", ".join(map(str, bad)), CANONICAL))))
            reported.add(p)
            continue
        has_security = has_security or security
        if not pins:
            continue
        here = [k for _, k, _ in pins]
        loose = unpaired(doc.text, pins)
        if loose:
            problems.append(str(refuse("pin-unpaired", (
                "%s line %s: each gates.yml call needs exactly one gates_ref of its own, under "
                "the `with:` of the same job, after its `uses:` line; a gates_ref anywhere else "
                "reaches no call. Fix the caller by hand, then run init again.")
                % (show(p), ", ".join(map(str, loose))))))
            reported.add(p)
            continue
        kinds.update(here)
        stuck = sorted({s for _, _, s in pins if not is_ancestor(history, s, sha)})
        if stuck:
            problems.append(str(refuse("pin-not-ancestor", (
                "%s pins %s, which this checkout's history does not contain (a newer release, or "
                "one it cannot place). init moves a pin only forward. If the repo is not ahead, "
                "check that this toolkit clone has its full history.")
                % (show(p), ", ".join(s[:12] for s in stuck)))))
            continue
        new = move_pins(doc.text, sha, label)
        if new != doc.text:
            moved = {s_ for _, _, s_ in pins} != {sha}
            writes.append((p, doc.dump(new), "moved pin" if moved else "relabelled pin"))
            notes.append("note    %s: only its pin lines changed. Its body is this repo's, and any "
                         "header in it about where a SHA comes from may be stale: the Protocol "
                         "version paragraph in CLAUDE.md governs." % show(p))
        else:
            notes.append("same    %s" % show(p))
    for (path, src), kind in ((TIER1, "gates"), (REVIEWC, "review")):
        if kind in kinds:
            continue
        if path in reported:
            continue
        if path in docs:
            problems.append(str(refuse("caller-unrelated", (
                "%s exists but does not call the toolkit's %s.yml; left untouched.") % (path, kind))))
            continue
        if attempt(guard, target, path) is not None:
            writes.append((path, move_pins(read(os.path.join(release, src)), sha, label), "wrote"))
    if has_security:
        notes.append("same    a workflow already calls Chiibitsu-Labs/security-checks; init never "
                     "rewrites that pin")
    elif SECURITY[0] in docs:
        notes.append("note    %s is this repo's own and does not call Chiibitsu-Labs/security-checks. "
                     "init left it alone and wrote NO security caller: this repo has none from the "
                     "toolkit." % SECURITY[0])
    elif attempt(guard, target, SECURITY[0]) is not None:
        writes.append((SECURITY[0], read(os.path.join(release, SECURITY[1])), "wrote"))
        notes.append("note    %s carries the pin this release was cut with; its header's step 1 "
                     "(read the current SHA with git ls-remote) is still yours to do." % SECURITY[0])

    if problems:
        raise gathered(problems)
    return writes, notes


_replace = os.replace  # two seams the selftest uses to make a write fail on purpose


def _write_tmp(directory, text, mode):
    """Write `text` to a new file in `directory`, with permission bits `mode`, and return
    its path. mkstemp creates the file exclusively under a random name, so it can never open
    something already there: no link a repo plants at a guessable name is followed."""
    fd, path = tempfile.mkstemp(dir=directory, prefix=".gates-init.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(path, mode)
    except OSError:
        os.remove(path)
        raise
    return path


def _mode_for(full):
    """An existing file keeps its permission bits; a new one gets what the umask allows."""
    if os.path.isfile(full):
        return os.stat(full).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def apply(target, writes):
    """Write the plan in two steps: every file to a temporary name beside its destination,
    then each moved into place. A failure in the first step changes no file init owns; a
    failure in the second is reported with what had already been moved."""
    staged, path = [], None

    def clean():
        for _, _, tmp in staged:
            if os.path.lexists(tmp):
                os.remove(tmp)
    try:
        for path, text, _ in writes:
            full = guard(target, path)
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            guard(target, path)  # again, after the directories exist
            staged.append((path, full, _write_tmp(os.path.dirname(full) or ".", text, _mode_for(full))))
    except (OSError, Problem) as e:
        clean()
        raise refuse("write-prepare-failed", "%s could not be prepared (%s). No standing-orders "
                     "file or caller was changed." % (path, e))
    done = []
    for path, full, tmp in staged:
        try:
            _replace(tmp, full)
        except OSError as e:
            clean()
            raise refuse("write-failed", "%s could not be written (%s). Already written in this "
                         "run: %s. The tree is half-applied; restore it with git before anything "
                         "else." % (path, e, ", ".join(done) or "nothing"))
        done.append(path)


def git(where, *args):
    r = subprocess.run(["git", "-C", where] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def unclean(where):
    """Why the checkout at `where` is not clean, or None. `git status` alone is blind to a
    file flagged skip-worktree or assume-unchanged, so the index flags are read too."""
    rc, dirty = git(where, "status", "--porcelain", "--untracked-files=all")
    if rc != 0:
        return "it is not a git checkout"
    if dirty:
        return "it has local changes or untracked files"
    rc, flags = git(where, "ls-files", "-v")
    if rc != 0 or any(l and not l.startswith("H ") for l in flags.split("\n")):
        return "it has files hidden from git status (skip-worktree or assume-unchanged)"
    return None


def run_init(target, sha, toolkit=TOOLKIT, release=RELEASE, today=None):
    """init, whole: every refusal on the way raises Problem. Returns (writes, notes)."""
    if not SHA40.fullmatch(sha or ""):
        raise refuse("sha-shape", "--sha must be the 40-character SHA you were given for this "
                     "release (the Tier 1 caller's pin on the default branch, or one the founder "
                     "states).")
    rc, head = git(toolkit, "rev-parse", "HEAD")
    if rc != 0 or head != sha:
        raise refuse("sha-not-head", "this toolkit checkout is at %s, not the --sha you named "
                     "(%s). Clone at that SHA and confirm `git rev-parse HEAD` before running "
                     "init." % (head or "?", sha))
    why = unclean(toolkit)
    if why:
        raise refuse("toolkit-unclean", "this toolkit checkout cannot be used: %s. Run init only "
                     "from a clean checkout of the SHA you named." % why)
    if git(toolkit, "rev-parse", "--is-shallow-repository")[1] != "false":
        raise refuse("toolkit-shallow", "this toolkit checkout is a shallow clone, so init cannot "
                     "tell an older pin from an unknown one. Clone with full history.")
    label = release_label(release)
    if label not in git(toolkit, "tag", "--points-at", "HEAD")[1].split("\n"):
        raise refuse("sha-not-release", "this toolkit checkout's HEAD does not carry the tag %s, "
                     "the release its callers are labelled with. init runs only from a tagged "
                     "release, so the label it writes beside a pin is true." % label)
    if not os.path.isdir(target):
        raise refuse("target-not-dir", "TARGET %s is not an existing directory; init never "
                     "creates the repo it writes into." % target)
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    writes, notes = plan(target, sha, today, toolkit, release)
    apply(target, writes)
    return writes, notes


def cmd_init(target, sha, toolkit=TOOLKIT, today=None):
    try:
        writes, notes = run_init(target, sha, toolkit=toolkit, today=today)
    except Problem as p:
        for line in str(p).split("\n"):
            print("PROBLEM " + line)
        print("init: stopped. Unless a line above says a file was already written, nothing was.")
        return 1
    for path, _, verb in writes:
        print("%-7s %s" % (verb.split()[0], show(path)) + ("" if verb == "wrote" else "  (%s)" % verb))
    for n in notes:
        print(n)
    print("init: done. Read the diff, run the gates, and open a PR; init never commits or pushes.")
    return 0


# ── check ──────────────────────────────────────────────────────────────────────────────

def check(target, release=RELEASE):
    """Return a list of (code, message). Empty means green."""
    rel = release_version(release)
    fails, seen = [], {}

    def text_of(path):
        try:
            raw = read_raw(os.path.join(target, path))
        except (OSError, UnicodeDecodeError) as e:
            red(fails, "unreadable-file", "%s: expected UTF-8 text, found a file this check cannot "
                "read (%s). See %s." % (show(path), e.__class__.__name__, ptr))
            return None
        if ODD_BREAK.search(raw):
            red(fails, "odd-line-break", "%s: expected only LF or CRLF line breaks, found a lone "
                "carriage return or a Unicode line separator, which another reader may split "
                "differently. See %s." % (show(path), ptr))
            return None
        return raw.lstrip("\ufeff").replace("\r\n", "\n")

    # Point at the repo's own paragraph only when it is the copy's (it names this script); a
    # stale file's own repair text is the stale part.
    ptr = POINTER_RELEASE
    try:
        if "gates_init.py" in read(os.path.join(target, "CLAUDE.md")):
            ptr = POINTER
    except (OSError, UnicodeDecodeError):
        pass
    for path, _ in STANDING:
        if not os.path.isfile(os.path.join(target, path)):
            red(fails, "missing-file", "%s: expected a standing-orders file at %s, found none. "
                "See %s." % (path, vstr(rel), ptr))
            continue
        text = text_of(path)
        if text is None:
            continue
        ms = markers(text)
        if not ms:
            red(fails, "missing-marker", "%s: expected a Protocol version marker at %s, found "
                "none. See %s." % (path, vstr(rel), ptr))
            continue
        if len(ms) > 1:
            red(fails, "duplicate-marker", "%s: expected one Protocol version marker, found %d "
                "(lines %s); no copy wins. Remove the extra one by hand; init does not. Then see "
                "%s." % (path, len(ms), ", ".join(str(n) for n, _ in ms), ptr))
            continue
        n, v = ms[0]
        if v is None:
            red(fails, "unreadable-marker", "%s:%d: expected a marker reading `**Protocol version: "
                "vibeOS docs/02 %s**`, found one this check cannot read. Correct or remove that "
                "line by hand; init does not. Then see %s." % (path, n, vstr(rel), ptr))
            continue
        seen[path] = v
        # A file with no region at all is a later slice's (init refuses it and says so); a
        # file that has one must be a file init can still update, or a green here would
        # certify a file the next init refuses.
        # It is read here the way init reads it (guard, load, parse_region), so the two
        # cannot disagree about such a file.
        b_ = None
        if MARKER_KEY in text:
            try:
                b_, e_ = parse_region(load(guard(target, path), path).text, path)
            except Problem as e:
                red(fails, "region-unreadable", "%s: expected a file with one well-formed "
                    "template-owned region that init can update; init refuses this one: %s "
                    "Repair it by hand; init does not. Then see %s." % (path, e, ptr))
        if b_ is not None and not b_ < n - 1 < e_:
            red(fails, "marker-outside-region", "%s:%d: expected the Protocol version marker "
                "inside the template-owned region (lines %d to %d), found it outside, where init "
                "refuses the file. Move or remove that line by hand; init does not. Then see %s."
                % (path, n, b_ + 1, e_ + 1, ptr))
        found = ("%s:%d: expected %s, the version this toolkit checkout declares (in CI, the pinned "
                 "release); found %s, which is %%s it. See %s." % (path, n, vstr(rel), vstr(v), ptr))
        if v < rel:
            red(fails, "behind", found % "behind")
        elif v > rel:
            red(fails, "ahead", found % "ahead of")
    if len(set(seen.values())) > 1:
        red(fails, "split", "the standing-orders files disagree: expected one version, found %s. "
            "See %s." % (", ".join("%s %s" % (p, vstr(v)) for p, v in sorted(seen.items())), ptr))
    occ = []
    for p in workflow_files(target):
        text = text_of(p)
        if text is None:
            continue
        pins, bad, sec_bad, _ = scan(text)
        if bad:
            red(fails, "caller-noncanonical", "%s line %s: expected the toolkit and a gates_ref "
                "named only in the forms init writes (%s), found another form. Fix the line by "
                "hand, keeping the commit it names; init does not repair a caller."
                % (show(p), ", ".join(map(str, bad)), CANONICAL))
        if sec_bad:
            red(fails, "security-noncanonical", "%s line %s: expected the security workflow named "
                "only in the form init writes (%s), found another form. Fix or remove the line "
                "by hand; init does not repair a caller."
                % (show(p), ", ".join(map(str, sec_bad)), CANONICAL_SECURITY))
        try:  # init reads every workflow file this way and stops on one it refuses
            load(guard(target, p), p)
        except Problem as e:
            red(fails, "caller-unreadable", "%s: expected a workflow file init can read; init "
                "refuses this one, and with it the whole run: %s Repair it by hand; init does "
                "not." % (show(p), e))
        loose = unpaired(text, pins)
        if loose:
            red(fails, "pin-unpaired", "%s line %s: expected each gates.yml call to have exactly "
                "one gates_ref of its own, under the `with:` of the same job and after its "
                "`uses:` line; found a call without one, or a gates_ref that reaches no call. "
                "Fix the caller by hand; init does not repair one."
                % (show(p), ", ".join(map(str, loose))))
        occ += [(show(p), k, s) for _, k, s in pins]
    lacking = [k for k in ("gates", "gates_ref", "review") if k not in {k for _, k, _ in occ}]
    if lacking:
        red(fails, "pin-missing", "expected a gates.yml call with its gates_ref, and a review.yml "
            "call, in %s; found no %s pin. If the repo has no caller at all, see %s; a caller "
            "that is there and incomplete is fixed by hand." % (WF, " or ".join(lacking), ptr))
    if len({s for _, _, s in occ}) > 1:
        red(fails, "pin-split", "expected every gates pin to name one commit, found %s. "
            "See %s." % ("; ".join("%s %s %s" % (p, k, s[:12]) for p, k, s in occ), ptr))
    return fails


def cmd_check(target):
    fails = check(target)
    for code, msg in fails:
        print("FAIL [standing-orders] %s: %s" % (code, msg))
    if fails:
        return 1
    print("ok [standing-orders] the CLAUDE.md, AGENTS.md and REVIEW.md markers are %s, the version "
          "this toolkit checkout declares (in CI, the pinned release), each inside its file's "
          "template-owned region where the file has one, and the gates, gates_ref and review pins "
          "are in init's form, name one SHA, and each gates call has its own gates_ref in its own "
          "job. Markers, region markers and pins only, never the bodies; this run does not compare the pin with its own checkout." % vstr(release_version()))
    return 0


# ── the team-safe copy ─────────────────────────────────────────────────────────────────
#
# Derived from vibeOS's templates. Each targeted edit (_sub, _in_paragraph, _one_paragraph)
# is asserted to match exactly as often as expected, so a template that moved under one
# stops the derivation. The three sweeping rewrites are not counted and are backed another
# way: the name swap by leaks(), the path locators by the bare-path refusal in derive_text,
# the pin labels by the caller-form refusal there. No edit and no check spells out a private
# string: each finds its text by structure, and the founder's name is matched by its SHA-256
# digest, which keeps the literal out of this file (the shape list below says what that hides).

POINTER_WORD = "vibeOS pointer"  # the founder's word, 2026-11-24
POINTER_PARAGRAPH = (
    "**The %s line.** A session that can read `chiibitsu/vibeOS` also follows that repo's "
    "`chiibitsu/vibeOS:templates/claude-md.md`: its rules (its template-owned region and its "
    "repo-specific gotchas), never its placeholders, at its published head, never a "
    "checkout's working tree, a branch or a fork (`chiibitsu/vibeOS:canon/decisions.md`, "
    "2026-11-09). Where the two differ, the stricter rule wins; where neither is stricter, "
    "vibeOS's does. A session that cannot read `chiibitsu/vibeOS` skips this paragraph."
) % POINTER_WORD
TEMPLATE = "`chiibitsu/vibeOS:templates/claude-md.md`"
# What follows the pointer paragraph, in the same paragraph. The tiebreak sends a session to
# vibeOS's wording wherever neither is stricter, and vibeOS's template does not carry the
# init command, so those steps are named as this copy's.
POINTER_STEPS = (" The steps for running `init` and repairing these three files are this copy's "
                 "to state: where that template words the same repair without the command, "
                 "follow the command here.")
POINTER_SUFFIX = {
    "claude-md.md": POINTER_STEPS,
    "agents-md.md": POINTER_STEPS,
    "review-md.md": POINTER_STEPS + " The bound reviewer reads only this file, so for it every "
                    "rule is still the ones written out below; this paragraph adds that "
                    "template's rules only for a session reviewer that can read it.",
}
NOT_YET_LIVE = ("this release's `init` and its check do not handle such a repo yet (a later "
                "slice does); until then its repair is the founder's to direct, never a copy by "
                "hand from vibeOS, whose bare paths a reference gate reads as this repo's own")
CALLER_NOTE = ("# Written by `gates init`: the toolkit SHAs in this file are already set to the release\n"
               "# init ran from. Wherever the header below says to replace or bump them by hand, or to\n"
               "# take a SHA from the toolkit's Releases table, THIS NOTE SUPERSEDES IT: they move only\n"
               "# by running init from a newer release, at a SHA from one of the two sources the\n"
               "# Protocol version paragraph in CLAUDE.md names, and from nowhere else.\n")

NAME_DIGEST = "821e63c5081b93f995d20ab9f57f38eaf8eee8dc61bf5a9d6aef92c224a06485"
IN_REPO_DIRS = "canon|docs|ops|templates|team|product|patterns|scripts"
IN_REPO_FIRST = re.compile(r"(?:%s|\.github|\.claude|gates|fixtures|init|src|app|supabase|tests)" % IN_REPO_DIRS)


def _flex(old):
    """A literal whose whitespace runs match any whitespace run, so a rewrapped line still
    matches and a reworded one does not."""
    return re.compile(r"\s+".join(re.escape(w) for w in old.split()))


def _sub(text, old, new, count=1, name=None):
    rx = _flex(old) if isinstance(old, str) else old
    found = len(rx.findall(text))
    if found != count:
        raise refuse("derive-edit-count", "derive: expected %d match(es) for %s, found %d; the "
                     "template moved, so re-read it and update this edit."
                     % (count, name or repr(old[:60]), found))
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


def _one_paragraph(text, test, name):
    paras = text.split("\n\n")
    hits = [i for i, p in enumerate(paras) if test(p)]
    if len(hits) != 1:
        raise refuse("derive-paragraph", "derive: expected one paragraph %s, found %d; the "
                     "template moved." % (name, len(hits)))
    return paras, hits[0]


def _in_paragraph(text, lead, edits):
    """Apply `edits` [(old, new)] inside the one paragraph opening with `lead`."""
    paras, i = _one_paragraph(text, lambda p: p.startswith(lead), "opening %r" % lead)
    for old, new in edits:
        paras[i] = _sub(paras[i], old, new, name="%r in the paragraph %r" % (
            old if isinstance(old, str) else old.pattern, lead))
    return "\n\n".join(paras)


def _insert_pointer(text, name):
    b, e = parse_region(text, name)
    lines = text.split("\n")
    for i in range(b + 1, e):
        if MARKER.search(lines[i]):
            j = i
            while j < e and lines[j].strip():
                j += 1
            return "\n".join(lines[:j] + ["", POINTER_PARAGRAPH + POINTER_SUFFIX[name]] + lines[j:])
    raise refuse("derive-no-marker", "derive: %s has no marker line inside its region to put the "
                 "%s line after" % (name, POINTER_WORD))


def derive_claude(t):
    # The name has already become "the founder" here (derive_text runs _founder first).
    t = _sub(t, re.compile(r"^(- Deploy: previews per PR\.) [^\n]*$", re.M), lambda m: m.group(1),
             name="the Deploy line's hosting note")
    t = _drop_section(t, r"Deploy after merge\b", "on deploying after merge")
    t = _drop_section(t, r"Log the work\b[^\n]*\bledger$", "on the time ledger")
    t = _sub(t, re.compile(r"^- \*\*\d{4}-\d{2}-\d{2} — A session holding the [^\n]*vault MCP "
                           r"connector.*?(?=^- )", re.M | re.S), "", name="the vault-memory gotcha")
    paras, i = _one_paragraph(t, lambda p: TZ_AREA.search(p), "holding an Area/City timezone name")
    paras[i] = ("Containers run UTC. This copy does not carry the founder's timezone, and a\n"
                "current time written here would be a lie by the time anyone read it.")
    t = "\n\n".join(paras)
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
         "names; no repo qualifies by resembling one"),
        ("**Except a repo that reads this file live**",
         "**Except a repo that reads %s live**" % TEMPLATE),
        ("never re-instantiated from this file", "never re-instantiated from it"),
        ("read this file as the current standing orders", "read that file as the current standing orders"),
        ("and the by-hand steps above repair that one file.", "and " + NOT_YET_LIVE + "."),
    ])
    t = _in_paragraph(t, "**And a repo with no `CLAUDE.md` or `AGENTS.md` of its own**", [
        ("is meant to take this file, at", "is meant to take %s, at" % TEMPLATE),
        ("once they point here.", "once they point there."),
    ])
    t = _in_paragraph(t, "**vibeOS reads this file as its own standing orders too**", [
        ("**vibeOS reads this file as its own standing orders too**: its `CLAUDE.md` and `AGENTS.md` "
         "point here rather than copying it. So a rule changed here changes in vibeOS as well.",
         "**vibeOS reads that template, the source of this copy, as its own standing orders**: its "
         "`CLAUDE.md` and `AGENTS.md` point there rather than copying it."),
        ("which of this file's product-repo rules", "which of the template's product-repo rules"),
    ])
    t = _sub(t, "THIS repo's `product/specs/` —",
             "THIS repo's product/specs/ folder, which starts with the repo's first spec —")
    t = _sub(t, "run `gates init` in this repo's root,",
             "run `python3 <the clone>/init/gates_init.py init . --sha <that SHA>` in this repo's root,")
    t = _sub(t, "vibeOS `docs/03`'s \"Working with the founder\" holds",
             "vibeOS `docs/03`'s section on working with the founder holds")
    t = _sub(t, "never copy from vibeOS `main`, which no marker-scheme repo reads.",
             "never copy from vibeOS `main`: a marker-scheme repo reads it only through its %s "
             "line, below, and never copies from it." % POINTER_WORD)
    return t


def derive_text(name, text, label):
    text = _founder(text)
    if name == "claude-md.md":
        text = derive_claude(text)
    if name == "review-md.md":
        text = _sub(text, "(in a repo that reads the standing orders live, vibeOS `templates/claude-md.md`'s).",
                    "(in a repo that reads the standing orders live, ask the founder: " + NOT_YET_LIVE + ").")
    if name.endswith(".md"):
        text = _locators(text)
        bare = re.findall(r"`((?:canon|docs|ops|templates|team|patterns|scripts)/[^`\n]*)`", text)
        if bare:
            raise refuse("derive-bare-path", "derive: %s still cites vibeOS paths bare, which a "
                         "consuming repo's reference gate reads as its own: %s. The template worded "
                         "a citation in a way _locators does not rewrite."
                         % (name, ", ".join(sorted(set(bare)))))
        text = _insert_pointer(text, name)
    else:
        # Every caller template must already carry its call in the form init reads, the
        # security caller included: init writes these files as they are, and the check is red
        # on any other form.
        pins, bad, sec_bad, security = scan(text)
        want = {TIER1[1]: ["gates", "gates_ref"], REVIEWC[1]: ["review"], SECURITY[1]: []}[name]
        if (bad or sec_bad or [k for _, k, _ in pins] != want or unpaired(text, pins)
                or security != (name == SECURITY[1])):
            raise refuse("derive-caller-form", "derive: templates/%s does not carry exactly %s in "
                         "the form init writes" % (name, " and ".join(want) or "one security call"))
        if name != SECURITY[1]:
            # The pin label names the release being cut (CITATION.cff), never the one vibeOS's
            # template was last bumped to.
            text = CALLER_NOTE + move_pins(text, None, label)
    return text


# Shapes the team-safe copy must not carry. Each is a shape, a public list or an allowlist;
# none names a private string. What they catch, exactly:
#   - an `owner/repo` outside LOCATOR_ALLOW in any of these positions: an owner beginning
#     with this company's name, anywhere; before `:path` or `#number`; after a github.com or
#     raw.githubusercontent.com host, `/` or `:` form; as a `uses:` value (public actions in
#     USES_ALLOW pass); or opening a backticked span, whatever follows it, unless its first
#     segment is one of the in-repo directory names in IN_REPO_FIRST;
#   - every time zone name in init/tznames.txt except the bare words UTC and GMT;
#   - a UTC or GMT offset in any letter case, and a bare `+08:00` or `+0800` shape;
#   - the founder's name, by digest; a heading naming the ledger; a bold lead naming a vault
#     connector.
# NOT caught: an unbackticked `acme/widget` in prose; a zone abbreviation or name the vendored
# list does not hold; and any other person's or client's name, which only a reading of the
# derived diff can find. The digest keeps the name's spelling out of this source; a short
# name's digest is guessable, so it hides the literal, not the name.
LOCATOR_ALLOW = {"chiibitsu/vibeos", "chiibitsu-labs/gates", "chiibitsu-labs/security-checks"}
USES_ALLOW = LOCATOR_ALLOW | {"actions/checkout", "actions/setup-node"}
OWNER_QUALIFIED = re.compile(r"(?<![\w.-])(chiibitsu[\w-]*)/([A-Za-z0-9._-]*[A-Za-z0-9_-])", re.I)
COLON_LOCATOR = re.compile(r"(?<![\w./:-])([A-Za-z0-9][\w-]*)/([\w.-]+)(?=:[\w./-]|#\d)")
GITHUB_URL = re.compile(r"(?:github\.com|raw\.githubusercontent\.com)[/:]([\w-]+)/([\w.-]*[\w-])", re.I)
USES_VALUE = re.compile(r"\buses\s*:\s*[\"']?([\w-]+)/([\w.-]*[\w-])", re.I)
BACKTICK_LEAD = re.compile(r"`([\w.-]+)/([\w.-]*[\w-])")
ZONE_PASS = {"UTC", "GMT"}
ZONES = sorted(l.strip() for l in read(os.path.join(HERE, "tznames.txt")).split("\n")
               if l.strip() and not l.startswith("#"))


def _names_rx(names):
    return re.compile(r"(?<![\w/+-])(?:%s)(?![\w/])"
                      % "|".join(re.escape(z) for z in sorted(names, key=len, reverse=True)))


TZ_NAME = _names_rx([z for z in ZONES if z not in ZONE_PASS])
TZ_AREA = _names_rx([z for z in ZONES if "/" in z])
OFFSET = re.compile(r"\b(?i:UTC|GMT)\s*[+\u2212-]\s*\d{1,2}(?::?\d{2})?\b"
                    r"|(?:(?<=\d)|(?<![\w:.+\u2212-]))[+\u2212-](?:0\d|1[0-4]):?[0-5]\d(?![\d:])")
LEDGER_HEADING = re.compile(r"^#{1,6}\s.*\bledger\b", re.I | re.M)
VAULT_LEAD = re.compile(r"\*\*[^*\n]*\bvault\b[^*\n]*\bconnector\b[^*\n]*\*\*", re.I)


def leaks(text):
    """Every omitted shape the text still carries, as human-readable strings."""
    out = []
    for rx in (OWNER_QUALIFIED, COLON_LOCATOR, GITHUB_URL, USES_VALUE, BACKTICK_LEAD):
        for m in rx.finditer(text):
            if rx in (COLON_LOCATOR, BACKTICK_LEAD) and IN_REPO_FIRST.fullmatch(m.group(1)):
                continue  # an in-repo path, not a locator
            allow = USES_ALLOW if rx is USES_VALUE else LOCATOR_ALLOW
            if ("%s/%s" % (m.group(1), m.group(2))).lower() not in allow:
                out.append("an owner/repo locator outside the allowlist: %s" % m.group(0))
    for rx, what in ((TZ_NAME, "a timezone name"), (OFFSET, "a UTC offset"),
                     (LEDGER_HEADING, "the time-ledger heading"),
                     (VAULT_LEAD, "the vault-memory gotcha's bold lead")):
        out += ["%s: %s" % (what, m.group(0).strip()) for m in rx.finditer(text)]
    out += ["the founder's name (by digest)" for w in re.findall(r"[A-Za-z]+", text) if _is_name(w)]
    return out


RELEASE_FILES = ("claude-md.md", "agents-md.md", "review-md.md",
                 "tier1-ci.yml", "review-ci.yml", "security-ci.yml")
VIBEOS_ORIGIN = re.compile(r"(?i)^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                           r"chiibitsu/vibeos(?:\.git)?/?$")


def citation_version(toolkit=TOOLKIT):
    m = re.search(r'^version:\s*"?([0-9][0-9.]*)"?\s*$', read(os.path.join(toolkit, "CITATION.cff")), re.M)
    if not m:
        raise refuse("derive-no-citation", "derive: CITATION.cff declares no version")
    return "v" + m.group(1)


def protocol_of(vibeos):
    """The version docs/02 declares, which every template marker must equal."""
    text = read(os.path.join(vibeos, "docs", "02-vibecoding-protocol.md"))
    m = re.search(r"^\*\*Protocol version: v([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\.\*\*", text, re.M)
    if not m:
        raise refuse("derive-no-protocol", "derive: docs/02 declares no `**Protocol version: vX.Y.**` line")
    return tuple(int(x or 0) for x in m.groups())


def published(vibeos, origin_rx=VIBEOS_ORIGIN):
    """The commit derive may name: the checkout must be clean, hidden index flags included,
    its origin must be chiibitsu/vibeOS on GitHub, and its HEAD must equal origin/main as
    last fetched (a fetch is the session's step; this cannot see a newer push)."""
    why = unclean(vibeos)
    if why:
        raise refuse("derive-unclean", "derive: the vibeOS checkout cannot be used: %s. Derive "
                     "only from a clean checkout of its published head." % why)
    rc, url = git(vibeos, "remote", "get-url", "origin")
    if rc != 0 or not origin_rx.search(url):
        raise refuse("derive-origin", "derive: the vibeOS checkout's origin is %r, not "
                     "chiibitsu/vibeOS on GitHub" % (url or None))
    rc, head = git(vibeos, "rev-parse", "HEAD")
    rc2, main = git(vibeos, "rev-parse", "--verify", "-q", "refs/remotes/origin/main")
    if rc != 0 or rc2 != 0 or not main:
        raise refuse("derive-no-main", "derive: the vibeOS checkout has no origin/main to compare "
                     "with; fetch it")
    if head != main:
        raise refuse("derive-not-main", "derive: the vibeOS checkout is at %s, not origin/main "
                     "(%s); check out the published head (fetch first)" % (head[:12], main[:12]))
    return head


def derive_all(sources, want, label, names=RELEASE_FILES):
    """sources: {release file name: template text}. Returns the derived texts or refuses."""
    texts = {}
    for name in names:
        src = sources[name]
        if name.endswith(".md"):
            ms = markers(src)
            if [v for _, v in ms] != [want]:
                raise refuse("derive-version-mismatch", "derive: templates/%s carries %s; docs/02 "
                             "declares %s" % (name, [vstr(v) if v else "?" for _, v in ms], vstr(want)))
        texts[name] = derive_text(name, src, label)
        bad = leaks(texts[name])
        if bad:
            raise refuse("derive-leak", "derive: %s still carries %s" % (name, "; ".join(bad)))
    return texts


def cmd_derive(vibeos, out=RELEASE, toolkit=TOOLKIT):
    try:
        source = published(vibeos)
        want = protocol_of(vibeos)
        label = citation_version(toolkit)
        texts = derive_all({n: read(os.path.join(vibeos, "templates", n)) for n in RELEASE_FILES},
                           want, label)
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
    global NAME_DIGEST, _replace, _write_tmp
    FIRED.clear()
    failed = []

    def ok(cond, what):
        print(("ok  " if cond else "FAIL ") + what)
        if not cond:
            failed.append(what)

    def snapshot(root):
        """Every file under root as bytes, and every symlink as its target."""
        out = {}
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in fs + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
                p = os.path.join(d, f)
                if os.path.islink(p):
                    out[os.path.relpath(p, root)] = "-> " + os.readlink(p)
                else:
                    with open(p, "rb") as fh:
                        out[os.path.relpath(p, root)] = fh.read()
        return out

    def write(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def raw(root, rel):
        with open(os.path.join(root, rel), encoding="utf-8", newline="") as fh:
            return fh.read()

    def run(cmd, cwd):
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    work = tempfile.mkdtemp(prefix="gates-init-selftest.")
    env = ["-c", "user.name=selftest", "-c", "user.email=selftest@invalid", "-c", "commit.gpgsign=false"]

    def commit(d, msg):
        run(["git"] + env + ["commit", "-q", "--allow-empty", "-m", msg], d)
        return run(["git", "rev-parse", "HEAD"], d)

    # Two stand-in toolkit checkouts with one history: `kit_n` at release N, `kit_n1` at N+1.
    today = "2026-01-02"
    rel = release_version()
    relv = vstr(rel)
    label = release_label()
    kit_n1 = os.path.join(work, "toolkit-N1")
    os.makedirs(kit_n1)
    run(["git", "init", "-q"], kit_n1)
    N = commit(kit_n1, "release N")
    N1 = commit(kit_n1, "release N+1")
    run(["git", "tag", label], kit_n1)                      # N+1 is the tagged release there
    kit_n = os.path.join(work, "toolkit-N")                 # the same history, checked out at
    run(["git", "clone", "-q", kit_n1, kit_n], work)        # release N, with N+1 present in it
    run(["git", "checkout", "-q", N], kit_n)
    run(["git", "tag", "-f", label], kit_n)
    KITS = {"N": (kit_n, N), "N1": (kit_n1, N1)}
    behind = "v%d.%d" % (rel[0], rel[1] - 1) if rel[1] else "v%d.9" % (rel[0] - 1)
    ahead = "v%d.%d" % (rel[0], rel[1] + 1)
    count = [0]

    def fresh(name):
        count[0] += 1
        d = os.path.join(work, "%s-%d" % (name, count[0]))
        os.makedirs(d)
        return d

    def init(target, at="N", today=today):
        """The real entry point, run from the stand-in checkout at release N or N+1."""
        tk, sha = KITS[at]
        return run_init(target, sha, toolkit=tk, today=today)

    def fires(site, fn):
        """True when fn() raises a Problem made at `site`."""
        FIRED.discard("refuse:" + site)
        try:
            fn()
        except Problem:
            return "refuse:" + site in FIRED
        return False

    def refused(target, site, at="N"):
        """True when init refuses `target` at `site` and leaves the tree byte-for-byte as it was."""
        before = snapshot(target)
        return fires(site, lambda: init(target, at)) and snapshot(target) == before

    def pins_of(d):
        return [(k, s) for p in workflow_files(d) for _, k, s in scan(read(os.path.join(d, p)))[0]]

    all_files = [p for p, _ in STANDING] + [TIER1[0], REVIEWC[0], SECURITY[0]]
    srcs = dict(STANDING + (TIER1, REVIEWC, SECURITY))

    # 1. an empty directory gets the six files, equal to the release copies bar dates and pins.
    a = fresh("empty")
    init(a)
    got = {k: v.decode() for k, v in snapshot(a).items()}
    ok(sorted(got) == sorted(all_files), "init in an empty directory writes exactly the six files")
    ok(all(undate(got[p]) == read(os.path.join(RELEASE, srcs[p])) for p, _ in STANDING),
       "the three standing-orders files equal the release copies except the date")
    ok(all(DATE.search(got[p]).group(2) == today for p, _ in STANDING),
       "each standing-orders marker carries the instantiation date")
    ok(pins_of(a) == [("review", N), ("gates", N), ("gates_ref", N)],
       "the three gates-SHA occurrences equal each other and the HEAD of the checkout init ran from")
    rel_sha = SHA40.search(USES_LINE.search(read(os.path.join(RELEASE, TIER1[1]))).group(0)).group(0)
    ok(all(got[p] == read(os.path.join(RELEASE, s)).replace(rel_sha, N) and rel_sha in read(os.path.join(RELEASE, s))
           for p, s in (TIER1, REVIEWC)),
       "the two gates callers equal the release copies byte for byte once the release's SHA is replaced by the running one")
    ok(got[SECURITY[0]] == read(os.path.join(RELEASE, SECURITY[1])), "the security caller equals the release copy")
    ok(check(a) == [], "the check is green on a freshly initialised tree")

    # 2. a second run changes nothing, on a later day too.
    before = snapshot(a)
    writes, _ = init(a, today="2026-02-03")
    ok(writes == [] and snapshot(a) == before, "a second run, on a later day, writes nothing")

    # 3. a file one version behind keeps every byte outside its region, in LF and in CRLF,
    #    with and without a byte-order mark.
    cur = read(os.path.join(RELEASE, "claude-md.md"))
    for name, eol, bom in (("LF", "\n", ""), ("CRLF", "\r\n", ""), ("LF with a byte-order mark", "\n", "\ufeff"),
                           ("CRLF with a byte-order mark", "\r\n", "\ufeff")):
        b = fresh("behind")
        old = cur.replace("docs/02 " + relv, "docs/02 " + behind, 1)
        old = splice(old, region_body(old) + "\nan obsolete template rule")
        bb, be = parse_region(old)
        above, below = "# CLAUDE.md — acme\n\nRepo rule above. \u00e9\n", "\n## Ours\n- keep me\n"
        write(b, "CLAUDE.md", bom + (above + "\n".join(old.split("\n")[bb:be + 1]) + below).replace("\n", eol))
        init(b)
        new = raw(b, "CLAUDE.md")
        top, tail = bom + above.replace("\n", eol) + BEGIN + eol, eol + END + below.replace("\n", eol)
        ok(new.startswith(top) and new.endswith(tail),
           "a one-version-behind %s file keeps every byte outside its region" % name)
        mid = new[len(top):len(new) - len(tail)]
        ok(mid.replace(eol, "\n") == date_marker(region_body(cur), today) and mid.count("\n") == mid.count(eol),
           "and its region is the release's, dated, in the file's own line ending (%s)" % name)
    c = fresh("crlf-all")
    init(c)
    for pth in all_files:
        write(c, pth, raw(c, pth).replace("docs/02 " + relv, "docs/02 " + behind, 1).replace("\n", "\r\n"))
    init(c, "N1")
    ok(all(raw(c, pth).count("\n") == raw(c, pth).count("\r\n") for pth in all_files)
       and pins_of(c) == [("review", N1), ("gates", N1), ("gates_ref", N1)] and check(c) == []
       and init(c, "N1")[0] == [],
       "with all six files in CRLF: three regions rewritten, three pins moved to the new SHA, every "
       "file still all-CRLF, the check green, and a second run writes nothing")
    d = fresh("mixed")
    write(d, "AGENTS.md", read(os.path.join(RELEASE, "agents-md.md")).replace("\n", "\r\n", 3))
    ok(refused(d, "mixed-eol"), "a file that mixes line endings is refused, not guessed at")
    d = fresh("binary")
    with open(os.path.join(d, "REVIEW.md"), "wb") as fh:
        fh.write(b"\xff\xfe not utf-8")
    ok(refused(d, "unreadable"), "a file that is not UTF-8 text is refused with a message, not a traceback")
    ok("unreadable-file" in {c_ for c_, _ in check(d)}, "and the check is red on it under its own code")
    for name, brk in (("a lone carriage return", "\r"), ("a Unicode line separator", "\u2028"), ("a next-line character", "\x85"),
                      ("a Unicode paragraph separator", "\u2029"), ("a vertical tab", "\x0b"), ("a form feed", "\x0c")):
        d = fresh("oddbreak")
        init(d)
        t = raw(d, TIER1[0]).replace("gates.yml@%s # %s\n" % (N, label),
                                     "gates.yml@%s # %s%s    with:%s      gates_ref: main\n" % (N, label, brk, brk), 1)
        write(d, TIER1[0], t)
        ok(t != raw(a, TIER1[0]) and refused(d, "odd-line-break") and {c_ for c_, _ in check(d)} == {"odd-line-break", "pin-missing"},
           "%s in a caller, hiding a second gates_ref from a line reader, is refused by init and red in the check" % name)
        d = fresh("oddbreak-md")
        init(d)
        write(d, "CLAUDE.md", raw(d, "CLAUDE.md").replace("\n\n", brk + "**Protocol version: vibeOS docs/02 v7.0** twin\n\n", 1))
        ok(refused(d, "odd-line-break") and "odd-line-break" in {c_ for c_, _ in check(d)},
           "%s in CLAUDE.md, hiding a second marker, is refused by init and red in the check" % name)

    # 4. each malformed region shape is refused under its own name, and nothing is written.
    good = read(os.path.join(RELEASE, "agents-md.md"))
    gb, ge = parse_region(good)
    gl = good.split("\n")
    shapes = (
        ("no region", "region-none", "# AGENTS.md\nno markers\n"),
        ("two pairs", "region-many", good + "\n" + BEGIN + "\nx\n" + END + "\n"),
        ("an end before the begin", "region-reversed", "\n".join(gl[:gb] + [END] + gl[gb + 1:ge] + [BEGIN] + gl[ge + 1:])),
        ("a missing end", "region-unpaired", "\n".join(gl[:ge] + gl[ge + 1:])),
        ("a pair in a fenced block", "region-fenced", "# A\n```\n" + BEGIN + "\nx\n" + END + "\n```\n"),
        ("a pair in a four-backtick fence holding a three-backtick one", "region-fenced",
         "# A\n````md\n```\n" + BEGIN + "\nx\n" + END + "\n```\n````\n"),
        ("a pair in a tilde fence", "region-fenced", "# A\n~~~\n" + BEGIN + "\nx\n" + END + "\n~~~\n"),
        ("a pair in a fence that follows one opened on a list line", "region-fence-unclear",
         "# A\n- ```sh\n  make\n  ```\n\n```\n" + BEGIN + "\nx\n" + END + "\n```\n"),
        ("a pair after a stray fence line inside an HTML comment", "region-fence-unclear",
         "# A\n<!--\n```\n-->\n```\n" + BEGIN + "\nx\n" + END + "\n```\n"),
        ("a pair in a fence after an HTML comment holding a fence line, with a later prose line that carries a fence run", "region-fence-unclear",
         "# A\n<!--\n```\n-->\n```\n" + BEGIN + "\nx\n" + END + "\n```\nTo show code, open with ``` on its own line.\n"),
        ("a pair in a fence after a list-line fence and a prose line that carries a fence run", "region-fence-unclear",
         "# A\n- ```sh\n  make\n  ```\nUse ``` to open a fence.\n```\n" + BEGIN + "\nx\n" + END + "\n```\nAnd ``` closes it.\n"),
        ("a pair below a fence indented four spaces", "region-fence-unclear",
         "# A\n\n1. x\n\n    ```\n    y\n    ```\n\n" + BEGIN + "\nx\n" + END + "\n"),
        ("a marker after other text on its line", "region-not-col0", "# A\nsee " + BEGIN + " here\n"),
        ("an indented pair", "region-not-col0", "# A\n  " + BEGIN + "\nx\n  " + END + "\n"),
        ("a pair in a code block indented four spaces", "region-not-col0",
         "# A\n\n1. x\n\n    ```md\n    " + BEGIN + "\n    old\n    " + END + "\n    ```\n"),
        ("a marker with trailing spaces", "region-not-col0", "# A\n" + BEGIN + " \nx\n" + END + "\n"),
    )
    for name, site, text in shapes:
        d = fresh("malformed")
        write(d, "AGENTS.md", text)
        ok(refused(d, site), "%s is refused as %s, and nothing is written" % (name, site))

    # 5. init never writes through a link, onto a non-file, or into a missing target.
    outside = fresh("outside")
    d = fresh("symlink")
    os.symlink(os.path.join(outside, "planted.md"), os.path.join(d, "CLAUDE.md"))
    ok(refused(d, "symlink") and os.listdir(outside) == [], "a symlinked CLAUDE.md is refused, and nothing lands at its target")
    d = fresh("symlink-dir")
    os.makedirs(os.path.join(d, ".github"))
    os.symlink(outside, os.path.join(d, WF))
    ok(refused(d, "symlink") and os.listdir(outside) == [], "a symlinked workflows directory is refused, and nothing lands in it")
    d = fresh("hardlink")
    write(outside, "notes.md", "# notes\n")
    os.link(os.path.join(outside, "notes.md"), os.path.join(d, "CLAUDE.md"))
    ok(refused(d, "hard-link") and raw(outside, "notes.md") == "# notes\n",
       "a hard-linked CLAUDE.md is refused, and the file sharing its inode is untouched")
    os.remove(os.path.join(outside, "notes.md"))
    for kind in ("a symlink", "a hard link", "a file"):
        d = fresh("tmp-name")
        write(outside, "victim.txt", "precious\n")
        for f in all_files:
            os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
            for planted in (f + ".gates-init.tmp", os.path.join(os.path.dirname(f), ".gates-init.tmp")):
                if os.path.lexists(os.path.join(d, planted)):
                    continue
                if kind == "a symlink":
                    os.symlink(os.path.join(outside, "victim.txt"), os.path.join(d, planted))
                elif kind == "a hard link":
                    os.link(os.path.join(outside, "victim.txt"), os.path.join(d, planted))
                elif kind == "a file":
                    write(d, planted, "the repo's own\n")
        before = {k: v for k, v in snapshot(d).items()}
        init(d)
        after = snapshot(d)
        ok(raw(outside, "victim.txt") == "precious\n" and all(after[k] == v for k, v in before.items())
           and sorted(set(after) - set(before)) == sorted(all_files) and check(d) == [],
           "%s planted at a temporary-looking name beside each destination is neither followed, overwritten nor removed, and the six files are written" % kind)
        os.remove(os.path.join(outside, "victim.txt"))
    d = fresh("dir-dest")
    os.makedirs(os.path.join(d, TIER1[0]))
    ok(refused(d, "not-a-file"), "a directory where tier1.yml belongs is refused before any write")
    d = fresh("file-parent")
    write(d, ".github", "x")
    ok(refused(d, "not-a-directory"), "a file where .github belongs is refused before any write")
    d = fresh("typo")
    ok(fires("target-not-dir", lambda: init(os.path.join(d, "nope", "typo"))) and os.listdir(d) == [],
       "a TARGET that does not exist is refused, and no directory is created for it")

    # 6. which code runs: init is bound to the SHA it was told and to a clean checkout.
    d = fresh("sha")
    for bad_sha, site, why in ((N[:12], "sha-shape", "a short SHA"), ("main", "sha-shape", "a ref name"),
                               (N1, "sha-not-head", "a real SHA that is not this checkout's HEAD")):
        ok(fires(site, lambda: run_init(d, bad_sha, toolkit=kit_n, today=today)) and os.listdir(d) == [],
           "--sha given %s is refused as %s, with nothing written" % (why, site))
    dirty = os.path.join(work, "toolkit-dirty")
    run(["git", "clone", "-q", kit_n1, dirty], work)
    run(["git", "checkout", "-q", N], dirty)
    write(dirty, "planted.py", "x\n")
    ok(fires("toolkit-unclean", lambda: run_init(d, N, toolkit=dirty, today=today)) and os.listdir(d) == [],
       "a toolkit checkout with an untracked file is refused, with nothing written")
    untagged = os.path.join(work, "toolkit-untagged")
    run(["git", "clone", "-q", kit_n1, untagged], work)
    U = commit(untagged, "after the release")
    ok(fires("sha-not-release", lambda: run_init(d, U, toolkit=untagged, today=today)) and os.listdir(d) == [],
       "a checkout whose HEAD is not the tagged release its callers are labelled with is refused, with nothing written")
    shallow = os.path.join(work, "toolkit-shallow")
    run(["git", "clone", "-q", "--depth", "1", "file://" + kit_n1, shallow], work)
    ok(fires("toolkit-shallow", lambda: run_init(d, N1, toolkit=shallow, today=today)) and os.listdir(d) == [],
       "a shallow toolkit clone is refused, with nothing written")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc_bad, rc_good = cmd_init(d, N1, toolkit=kit_n, today=today), cmd_init(d, N, toolkit=kit_n, today=today)
    ok(rc_bad == 1 and "PROBLEM" in out.getvalue() and rc_good == 0 and out.getvalue().count("init: done") == 1
       and sorted(snapshot(d)) == sorted(all_files),
       "the `init` command exits 1 with a PROBLEM line on a refusal and 0 after writing the six files")
    run(["git", "add", "planted.py"], dirty)
    run(["git"] + env + ["commit", "-q", "-m", "tracked"], dirty)
    ok(unclean(dirty) is None, "a checkout with nothing changed is clean")
    write(dirty, "planted.py", "edited\n")
    ok(unclean(dirty) is not None, "an edited tracked file makes a checkout unclean")
    run(["git", "update-index", "--skip-worktree", "planted.py"], dirty)
    ok(run(["git", "status", "--porcelain"], dirty) == "" and unclean(dirty) is not None,
       "an edit hidden by skip-worktree, which git status does not show, makes a checkout unclean")
    run(["git", "update-index", "--no-skip-worktree", "planted.py"], dirty)
    run(["git", "checkout", "-q", "--", "planted.py"], dirty)
    run(["git", "update-index", "--assume-unchanged", "planted.py"], dirty)
    ok(unclean(dirty) is not None, "and so does an assume-unchanged flag")

    # 7. pins: forward only, only in init's own form, and a repo's own files are left alone.
    d = fresh("pin-forward")
    init(d, "N")
    init(d, "N1")
    ok(pins_of(d) == [("review", N1), ("gates", N1), ("gates_ref", N1)],
       "run from release N+1 on a repo pinned to N, init moves all three pins")
    ok(refused(d, "pin-not-ancestor", at="N"),
       "run from release N on a repo pinned to N+1, in a full clone that holds both commits, init moves no pin and writes nothing")
    ok(subprocess.run(["git", "-C", kit_n, "cat-file", "-e", N1 + "^{commit}"]).returncode == 0,
       "(that clone does hold the newer commit, so it is the ancestry test that refused, not a missing object)")
    d = fresh("pin-unknown")
    init(d)
    write(d, TIER1[0], raw(d, TIER1[0]).replace(N, "2" * 40))
    ok(refused(d, "pin-not-ancestor", at="N1"), "a pin the checkout's history does not contain is refused")
    d = fresh("marker-ahead")
    init(d)
    write(d, "CLAUDE.md", raw(d, "CLAUDE.md").replace("docs/02 " + relv, "docs/02 " + ahead, 1))
    ok(refused(d, "marker-ahead"), "a marker ahead of this release is refused, so init never downgrades a region")
    for name, f in (("a marker init cannot read", lambda t: MARKER.sub("**Protocol version: vibeOS docs/02 v9**", t, count=1)),
                    ("an unreadable marker claiming a newer version", lambda t: MARKER.sub("**Protocol version: vibeOS `docs/02` v9.0**", t, count=1)),
                    ("a second marker below the region", lambda t: t + "\n- note: **Protocol version: vibeOS docs/02 %s**\n" % behind),
                    ("a second marker inside the region", lambda t: t.replace(BEGIN + "\n", BEGIN + "\n**Protocol version: vibeOS docs/02 %s**\n" % relv, 1))):
        d = fresh("marker-shape")
        init(d)
        write(d, "CLAUDE.md", f(raw(d, "CLAUDE.md")))
        ok(refused(d, "marker-shape") and check(d) != [],
           "%s is refused by init, never overwritten, and the check is red on the same file" % name)
    d = fresh("no-marker")
    init(d)
    write(d, "AGENTS.md", "\n".join(l for l in raw(d, "AGENTS.md").split("\n") if not MARKER_LOOSE.search(l)))
    init(d)
    ok(check(d) == [], "a region that has lost its marker line is repaired by init, and the check is green again")
    d = fresh("unpaired")
    init(d)
    write(d, TIER1[0], "\n".join(l for l in raw(d, TIER1[0]).split("\n") if "gates_ref: " + N not in l))
    ok(refused(d, "pin-unpaired", at="N1") and {"pin-unpaired", "pin-missing"} <= {c_ for c_, _ in check(d)},
       "a gates call that has lost its gates_ref is refused by init, which the check is red on too, so 'run init' is never the answer to it")
    d = fresh("relabel")
    init(d)
    write(d, REVIEWC[0], raw(d, REVIEWC[0]).replace(" # " + label, " # mine"))
    writes, _ = init(d)
    ok([(w[0], w[2]) for w in writes] == [(REVIEWC[0], "relabelled pin")] and (" # " + label) in raw(d, REVIEWC[0]),
       "a pin whose SHA is current and whose comment is not gets its label back, and init calls that relabelled, not moved")
    d = fresh("one-file")
    one = ("jobs:\n  gates:\n    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s  # tag=v1.0.0\n"
           "    with:\n      gates_ref: %s # v1.0.0-rc1\n  review:\n"
           "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s\n" % (N, N, N))
    write(d, WF + "/gates.yml", one)
    init(d, "N1")
    want = ("jobs:\n  gates:\n    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s # %s\n"
            "    with:\n      gates_ref: %s # %s\n  review:\n"
            "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s # %s\n" % (N1, label, N1, label, N1, label))
    ok(raw(d, WF + "/gates.yml") == want and not os.path.exists(os.path.join(d, TIER1[0]))
       and not os.path.exists(os.path.join(d, REVIEWC[0])),
       "a one-file caller has exactly its three pin lines changed, each old trailing comment replaced "
       "by this release's label, and gains no second caller")

    G = "    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s\n" % N
    Rf = "      gates_ref: %s\n" % N
    Rv = "    uses: Chiibitsu-Labs/gates/.github/workflows/review.yml@%s\n" % N
    caller = lambda g, r, v: "jobs:\n  g:\n%s    with:\n%s  r:\n%s" % (g, r, v)
    odd = (
        ("a quoted uses value", caller(G.replace("uses: ", 'uses: "').replace("\n", '"\n'), Rf, Rv)),
        ("a quoted uses key", caller(G.replace("uses:", '"uses":'), Rf, Rv)),
        ("a spaced uses key", caller(G.replace("uses:", "uses :"), Rf, Rv)),
        ("a flow-mapping call", "jobs:\n  g: {uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s, with: {gates_ref: %s}}\n  r:\n%s" % (N, N, Rv)),
        ("a tag pin", caller(G.replace(N, "v1.2.2"), Rf, Rv)),
        ("a branch pin on review", caller(G, Rf, Rv.replace(N, "main"))),
        ("an owner in another letter case", caller(G.replace("Chiibitsu-Labs", "chiibitsu-labs"), Rf, Rv)),
        ("a repo in another letter case", caller(G.replace("/gates/", "/Gates/"), Rf, Rv)),
        ("the toolkit's old name", caller(G.replace("Chiibitsu-Labs", "chiibitsu"), Rf, Rv)),
        ("a lookalike owner", caller(G.replace("Chiibitsu-Labs", "notChiibitsu-Labs"), Rf, Rv)),
        ("a .yaml extension", caller(G.replace("gates.yml", "gates.yaml"), Rf, Rv)),
        ("a commented-out call", caller(G.replace("    uses:", "    # uses:"), Rf, Rv)),
        ("a quoted gates_ref value", caller(G, Rf.replace(N, "'%s'" % N), Rv)),
        ("a quoted gates_ref key", caller(G, Rf.replace("gates_ref:", '"gates_ref":'), Rv)),
        ("a gates_ref expression", caller(G, Rf.replace(N, "${{ vars.X }}"), Rv)),
        ("an uppercase SHA", caller(G.replace(N, "A" * 40), Rf, Rv)),
        ("text after the SHA that is not a comment", caller(G.replace("\n", " extra\n"), Rf, Rv)),
        ("text after a gates_ref SHA that is not a comment", caller(G, Rf.replace("\n", " extra\n"), Rv)),
        ("a tab-indented call", caller(G.replace("    uses", "\tuses"), Rf, Rv)),
        ("a gates_ref in a flow mapping", caller(G, "      {gates_ref: %s}\n" % N, Rv)),
        ("a gates_ref as a list item", caller(G, "      - gates_ref: %s\n" % N, Rv)),
        ("a gates_ref key in capitals", caller(G, Rf.replace("gates_ref", "GATES_REF"), Rv)),
        ("a gates_ref key behind a YAML anchor", caller(G, Rf.replace("gates_ref", "&r gates_ref"), Rv)),
        ("a gates_ref key behind a YAML tag", caller(G, Rf.replace("gates_ref", "!!str gates_ref"), Rv)),
        ("an explicit gates_ref key", caller(G, "      ? gates_ref\n      : %s\n" % N, Rv)),
        ("a gates_ref in a flow sequence", caller(G, "      [gates_ref: %s]\n" % N, Rv)),
    )
    for name, text in odd:
        d = fresh("odd")
        write(d, WF + "/ci.yml", text)
        ok(refused(d, "caller-noncanonical"), "%s is detected and refused: no pin moves and no twin caller is written" % name)
        for p, _ in STANDING:
            write(d, p, date_marker(read(os.path.join(RELEASE, srcs[p])), today))
        ok("caller-noncanonical" in {c_ for c_, _ in check(d)}, "and the check is red on %s" % name)
    d = fresh("yaml-ext")
    write(d, WF + "/ci.yaml", caller(G, Rf, Rv))
    init(d, "N1")
    ok(pins_of(d) == [("gates", N1), ("gates_ref", N1), ("review", N1)] and not os.path.exists(os.path.join(d, TIER1[0])),
       "a caller in a .yaml file is read like one in a .yml file: its pins move and no twin is written")
    d = fresh("bad-tier1")
    write(d, TIER1[0], caller(G.replace("uses: ", 'uses: "').replace("\n", '"\n'), Rf, Rv))
    msg = []
    try:
        init(d)
    except Problem as p:
        msg = str(p).split("\n")
    ok(len(msg) == 1 and "in a form init does not read" in msg[0],
       "a tier1.yml with a noncanonical call draws that one PROBLEM line, and no second line saying it does not call the toolkit")
    d = fresh("hostile-name")
    evil = "z\ninit: done. Nothing to review.\n.yml"
    write(d, WF + "/" + evil, caller(G, Rf, Rv))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = cmd_init(d, N1, toolkit=kit_n1, today=today)
    ok(rc == 0 and [l for l in out.getvalue().split("\n") if l.startswith("init:")] == [l for l in out.getvalue().split("\n") if l.startswith("init: done. Read the diff")][:1]
       and len([l for l in out.getvalue().split("\n") if l.startswith("init:")]) == 1 and "z\\ninit" in out.getvalue(),
       "a workflow file name holding line breaks is printed escaped by init, so it cannot forge a line of init's output")
    d = fresh("foreign-ref")
    other = "jobs:\n  n:\n    uses: someone/else/.github/workflows/y.yml@%s\n    with:\n      gates_ref: main\n" % ("a" * 40)
    write(d, WF + "/nightly.yml", other)
    init(d)
    ok(raw(d, WF + "/nightly.yml") == other and os.path.exists(os.path.join(d, TIER1[0])),
       "a gates_ref in a workflow that never mentions the toolkit is someone else's: left alone, and the callers are written")
    for path in (TIER1[0], REVIEWC[0]):
        d = fresh("unrelated")
        write(d, path, "name: mine\non: push\njobs: {}\n")
        ok(refused(d, "caller-unrelated"), "a %s of the repo's own, which does not call the toolkit, is refused and never overwritten" % path)

    # 8. the security caller: written only when nothing calls security-checks; never rewritten.
    sec = read(os.path.join(RELEASE, SECURITY[1]))
    d = fresh("security-kept")
    mine = sec.replace(SHA40.search(sec).group(0), "1" * 40)
    write(d, SECURITY[0], mine)
    init(d)
    ok(raw(d, SECURITY[0]) == mine, "an existing security caller keeps its pin")
    d = fresh("security-elsewhere")
    write(d, WF + "/sec.yml", "jobs:\n  s:\n    uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@%s # main\n" % ("1" * 40))
    init(d)
    ok(not os.path.exists(os.path.join(d, SECURITY[0])), "a security caller under another file name counts, and no second one is written")
    d = fresh("security-first-file")
    write(d, WF + "/a-sec.yml", "jobs:\n  s:\n    uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@%s # main\n" % ("1" * 40))
    write(d, WF + "/z-other.yml", "name: z\non: push\njobs: {}\n")
    init(d)
    ok(not os.path.exists(os.path.join(d, SECURITY[0])), "a security caller counts whichever workflow file it is in, the first or the last read")
    for tail, why in ((" extra", "text after the SHA that is not a comment"),):
        d = fresh("security-odd")
        write(d, WF + "/notes.yml", "jobs:\n  s:\n    uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@%s%s\n" % ("1" * 40, tail))
        ok(refused(d, "security-noncanonical"), "a security call with %s is refused" % why)
    d = fresh("security-own")
    own = "name: codeql\non: push\njobs: {}\n"
    write(d, SECURITY[0], own)
    _, notes = init(d)
    ok(raw(d, SECURITY[0]) == own and any("wrote NO security caller" in n for n in notes)
       and os.path.exists(os.path.join(d, "CLAUDE.md")),
       "a security.yml of the repo's own is left alone, init says in so many words that it wrote no "
       "security caller, and the rest of the run goes ahead")
    S1 = "1" * 40
    for name, line in (("a commented security call", "    # uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@%s\n" % S1),
                       ("a lookalike security owner", "    uses: notChiibitsu-Labs/security-checks/.github/workflows/security.yml@%s\n" % S1),
                       ("a security call with its owner in another letter case", "    uses: chiibitsu-labs/security-checks/.github/workflows/security.yml@%s\n" % S1),
                       ("a security call with its repo in another letter case", "    uses: Chiibitsu-Labs/Security-Checks/.github/workflows/security.yml@%s\n" % S1),
                       ("a security call pinned to a branch", "    uses: Chiibitsu-Labs/security-checks/.github/workflows/security.yml@main\n")):
        d = fresh("security-odd")
        write(d, WF + "/notes.yml", "jobs:\n  s:\n" + line)
        ok(refused(d, "security-noncanonical"), "%s is detected and refused, never counted as a security caller" % name)
    d = fresh("security-prose")
    write(d, WF + "/notes.yml", "# TODO: look at Chiibitsu-Labs/security-checks one day\nname: n\non: push\njobs: {}\n")
    init(d)
    ok(os.path.exists(os.path.join(d, SECURITY[0])),
       "a comment that names the security repo without its workflow path changes nothing: the security caller is written")

    # 9. the check is red for each failure, and reports exactly that failure's codes.
    def checked(mutate):
        d = fresh("check")
        init(d)
        mutate(d)
        return {c_ for c_, _ in check(d)}

    edit = lambda path, f: (lambda d: write(d, path, f(raw(d, path))))
    every = lambda f: (lambda d: [edit(p, f)(d) for p, _ in STANDING])
    without = lambda test: (lambda t: "\n".join(l for l in t.split("\n") if not test(l)))
    cases = (
        ("a missing file", lambda d: os.remove(os.path.join(d, "REVIEW.md")), {"missing-file"}),
        ("a missing marker", edit("AGENTS.md", without(MARKER_LOOSE.search)), {"missing-marker"}),
        ("a marker it cannot read", edit("AGENTS.md", lambda t: MARKER.sub("**Protocol version: `docs/02` v8.1.**", t)), {"unreadable-marker"}),
        ("a duplicated marker", edit("CLAUDE.md", lambda t: t + "\n" + MARKER.search(t).group(0) + "\n"), {"duplicate-marker"}),
        ("markers behind", every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1)), {"behind"}),
        ("markers ahead", every(lambda t: t.replace("docs/02 " + relv, "docs/02 " + ahead, 1)), {"ahead"}),
        ("the files split", edit("REVIEW.md", lambda t: t.replace("docs/02 " + relv, "docs/02 " + behind, 1)), {"behind", "split"}),
        ("the review pin differing", edit(REVIEWC[0], lambda t: t.replace(N, N1)), {"pin-split"}),
        ("the gates_ref differing", edit(TIER1[0], lambda t: t.replace("gates_ref: " + N, "gates_ref: " + N1)), {"pin-split"}),
        ("the gates.yml pin differing", edit(TIER1[0], lambda t: t.replace("gates.yml@" + N, "gates.yml@" + N1)), {"pin-split"}),
        ("two gates_refs for one gates call", edit(TIER1[0], lambda t: t + "  again:\n    with:\n" + Rf), {"pin-unpaired"}),
        ("two markers on one line", edit("CLAUDE.md", lambda t: MARKER.sub(lambda m: m.group(0) + " ~~" + m.group(0).replace(relv, ahead) + "~~", t, count=1)), {"duplicate-marker"}),
        ("an unreadable marker before a readable one on one line", edit("CLAUDE.md", lambda t: MARKER.sub(lambda m: "**Protocol version: old** " + m.group(0), t, count=1)), {"duplicate-marker"}),
        ("no callers", lambda d: [os.remove(os.path.join(d, p)) for p in (TIER1[0], REVIEWC[0])], {"pin-missing"}),
        ("no review caller", lambda d: os.remove(os.path.join(d, REVIEWC[0])), {"pin-missing"}),
        ("a gates call with no gates_ref", edit(TIER1[0], without(lambda l: "gates_ref: " + N in l)), {"pin-missing", "pin-unpaired"}),
        ("two gates calls sharing one gates_ref", edit(TIER1[0], lambda t: t + "  again:\n" + G), {"pin-unpaired"}),
        ("a commented-out security call", edit(SECURITY[0], lambda t: t.replace("    uses: Chiibitsu-Labs/security-checks", "    # uses: Chiibitsu-Labs/security-checks")), {"security-noncanonical"}),
        ("a review call left only in a comment", edit(REVIEWC[0], lambda t: t.replace("    uses: Chiibitsu-Labs/gates", "    # uses: Chiibitsu-Labs/gates")), {"caller-noncanonical", "pin-missing"}),
    )
    G = "    uses: Chiibitsu-Labs/gates/.github/workflows/gates.yml@%s\n" % N
    for name, mutate, want in cases:
        ok(checked(mutate) == want, "the check is red for %s, and reports exactly %s" % (name, ", ".join(sorted(want))))
    # What init refuses in a file that has a region, the check is red on: a green never
    # certifies a file the next init cannot update.
    def moved_out(t):
        line = next(l for l in t.split("\n") if MARKER_LOOSE.search(l))
        return t.replace(line + "\n", "", 1) + "\n" + line + "\n"
    ref_line = "      gates_ref: " + N
    drop_ref = lambda t: "\n".join(l for l in t.split("\n") if not l.startswith(ref_line))
    def ref_elsewhere(t):  # the gates_ref leaves the gates job for a new job of its own
        return drop_ref(t) + "  other:\n    with:\n" + ref_line + "\n"
    def ref_other_key(t):  # same job, under another key
        return t.replace("    with:\n" + ref_line, "    with:\n      x: 1\n    secrets:\n" + ref_line, 1)
    def ref_above(t):  # same job, its `with:` above the `uses:` line
        g = next(l for l in t.split("\n") if l.startswith(G.rstrip("\n")))
        rl = next(l for l in t.split("\n") if l.startswith(ref_line))
        return t.replace(g + "\n    with:\n" + rl + "\n", "    with:\n" + rl + "\n" + g + "\n", 1)
    def ref_nested(t):  # under the right `with:`, one level too deep
        return t.replace("    with:\n" + ref_line, "    with:\n      x:\n  " + ref_line, 1)
    for name, path, f, code, site in (
            ("the only marker moved below the region", "AGENTS.md", moved_out, "marker-outside-region", "marker-shape"),
            ("a second region pair", "AGENTS.md", lambda t: t + "\n" + BEGIN + "\n" + END + "\n", "region-unreadable", "region-many"),
            ("a gates_ref moved under another job", TIER1[0], ref_elsewhere, "pin-unpaired", "pin-unpaired"),
            ("a gates_ref under another key of the gates job", TIER1[0], ref_other_key, "pin-unpaired", "pin-unpaired"),
            ("a gates_ref above its uses line", TIER1[0], ref_above, "pin-unpaired", "pin-unpaired"),
            ("a gates_ref nested one level too deep", TIER1[0], ref_nested, "pin-unpaired", "pin-unpaired")):
        d = fresh("agree")
        init(d)
        before = raw(d, path)
        write(d, path, f(before))
        ok(raw(d, path) != before and {c_ for c_, _ in check(d)} == {code} and refused(d, site, at="N1"),
           "the check is red (%s, and only that) for %s, which init refuses too (%s)" % (code, name, site))
    def rawbytes(d, path, f):
        full = os.path.join(d, path)
        with open(full, encoding="utf-8", newline="") as fh:
            t = fh.read()
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(f(t))
    def linked(d, path):
        full = os.path.join(d, path)
        os.rename(full, full + ".real")
        os.symlink(os.path.basename(full) + ".real", full)
    crlf3 = lambda t: t.replace("\n", "\r\n", 3)
    for name, path, mutate, code, site in (
            ("mixed line endings in a file with a region", "AGENTS.md", lambda d: rawbytes(d, "AGENTS.md", crlf3), "region-unreadable", "mixed-eol"),
            ("a begin marker behind two byte-order marks", "AGENTS.md", lambda d: rawbytes(d, "AGENTS.md", lambda t: "\ufeff\ufeff" + t[t.index(BEGIN):]), "region-unreadable", "region-not-col0"),
            ("a file with a region that is a symlink", "AGENTS.md", lambda d: linked(d, "AGENTS.md"), "region-unreadable", "symlink"),
            ("mixed line endings in a caller", TIER1[0], lambda d: rawbytes(d, TIER1[0], crlf3), "caller-unreadable", "mixed-eol"),
            ("a caller that is a symlink", REVIEWC[0], lambda d: linked(d, REVIEWC[0]), "caller-unreadable", "symlink"),
            ("the security caller as a symlink", SECURITY[0], lambda d: linked(d, SECURITY[0]), "caller-unreadable", "symlink"),
            ("mixed line endings in a workflow that calls nothing of the toolkit's", WF + "/deploy.yml",
             lambda d: write(d, WF + "/deploy.yml", "name: d\r\non: push\njobs: {}\n"), "caller-unreadable", "mixed-eol"),
            ("two gates_refs under one with", TIER1[0], lambda d: write(d, TIER1[0], raw(d, TIER1[0]).replace(ref_line, ref_line + "\n" + ref_line, 1)), "pin-unpaired", "pin-unpaired")):
        d = fresh("agree-read")
        init(d)
        mutate(d)
        ok({c_ for c_, _ in check(d)} == {code} and refused(d, site, at="N1"),
           "the check is red (%s, and only that) for %s, which init refuses too (%s)" % (code, name, site))
    d = fresh("agree-none")
    init(d)
    write(d, "AGENTS.md", "\n".join(l for l in raw(d, "AGENTS.md").split("\n") if l not in (BEGIN, END)))
    ok(check(d) == [] and refused(d, "region-none"),
       "the one stated exception: a file with no region at all is green in the check and refused by init, a later slice's")
    d = fresh("pointer")
    init(d)
    write(d, "REVIEW.md", raw(d, "REVIEW.md").replace("docs/02 " + relv, "docs/02 " + behind, 1))
    ok(check(d) != [] and all(POINTER + "." in m for _, m in check(d)),
       "a failure points at CLAUDE.md's paragraph when that file is the copy's (it names this script)")
    write(d, "CLAUDE.md", "**Protocol version: vibeOS docs/02 %s** If stale, copy from vibeOS main.\n" % relv)
    ok(check(d) != [] and all(POINTER_RELEASE in m for _, m in check(d)),
       "and at the release's copy when CLAUDE.md is some older text, whose own repair steps are the stale part")

    d = fresh("modes")
    init(d)
    write(d, "CLAUDE.md", raw(d, "CLAUDE.md").replace("docs/02 " + relv, "docs/02 " + behind, 1))
    os.chmod(os.path.join(d, "CLAUDE.md"), 0o640)
    os.chmod(os.path.join(d, TIER1[0]), 0o755)
    writes, _ = init(d, "N1")
    umask = os.umask(0)
    os.umask(umask)
    fresh_mode = os.stat(os.path.join(a, "AGENTS.md")).st_mode & 0o777
    ok({"CLAUDE.md", TIER1[0]} <= {w[0] for w in writes} and os.stat(os.path.join(d, "CLAUDE.md")).st_mode & 0o777 == 0o640 and os.stat(os.path.join(d, TIER1[0])).st_mode & 0o777 == 0o755
       and fresh_mode == 0o666 & ~umask,
       "a rewritten file keeps its permission bits (0640 and 0755 here), and a new file gets what the umask allows (%o)" % fresh_mode)

    # 10. writing: a failure before anything is moved changes nothing; one after says what moved.
    d = fresh("write-prepare")
    real_tmp, real_replace, n_ = _write_tmp, _replace, [0]

    def third_fails(real):
        def f(*args):
            n_[0] += 1
            if n_[0] == 3:
                raise OSError("made to fail on purpose")
            return real(*args)
        return f
    _write_tmp = third_fails(real_tmp)
    ok(refused(d, "write-prepare-failed") and snapshot(d) == {},
       "a failure while preparing the third file leaves the tree as it was, with no temporary file left behind")
    _write_tmp, n_[0] = real_tmp, 0
    d = fresh("write-inner")
    real_chmod = os.chmod
    os.chmod = third_fails(real_chmod)
    try:
        inner = refused(d, "write-prepare-failed") and snapshot(d) == {}
    finally:
        os.chmod = real_chmod
    ok(inner, "a failure inside the writing of the third temporary file itself also leaves no temporary file behind")
    n_[0] = 0
    d = fresh("write-move")
    _replace = third_fails(real_replace)
    msg = []
    FIRED.discard("refuse:write-failed")
    try:
        init(d)
    except Problem as p:
        msg.append(str(p))
    _replace = real_replace
    ok("refuse:write-failed" in FIRED and msg and "Already written in this run: CLAUDE.md, AGENTS.md" in msg[0]
       and sorted(snapshot(d)) == ["AGENTS.md", "CLAUDE.md"],
       "a failure while moving the third file into place names the two already written, and leaves no temporary file")

    # 11. the committed release copy is the team-safe copy.
    cite = citation_version()
    for name in RELEASE_FILES:
        text = read(os.path.join(RELEASE, name))
        ok(leaks(text) == [], "init/release/%s carries none of the shapes the shape check matches" % name)
        if name.endswith(".md"):
            ok(text.count(POINTER_PARAGRAPH) == 1 and POINTER_PARAGRAPH in region_body(text),
               "init/release/%s carries the %s line once, inside its region" % (name, POINTER_WORD))
            ok([v for _, v in markers(text)] == [rel], "init/release/%s carries one marker, at %s" % (name, relv))
    ok("`product/specs/`" not in read(os.path.join(RELEASE, "claude-md.md")),
       "the CLAUDE copy does not cite the consuming repo's spec folder as an existing path, so a fresh repo's reference gate has nothing to resolve there")
    ok(label == cite, "the release's tier1 and review callers label every gates pin %s, the version CITATION.cff declares" % cite)
    relbad2 = fresh("release-label")
    for n_ in (TIER1[1], REVIEWC[1]):
        write(relbad2, n_, read(os.path.join(RELEASE, n_)))
    write(relbad2, REVIEWC[1], read(os.path.join(RELEASE, REVIEWC[1])).replace(" # " + label, " # v0.0.1"))
    ok(fires("release-no-label", lambda: release_label(relbad2)), "a review caller labelled differently from tier1 is refused")
    ok(LOCATOR_ALLOW == {"chiibitsu/vibeos", "chiibitsu-labs/gates", "chiibitsu-labs/security-checks"}
       and USES_ALLOW - LOCATOR_ALLOW == {"actions/checkout", "actions/setup-node"} and ZONE_PASS == {"UTC", "GMT"}
       and IN_REPO_FIRST.pattern == r"(?:canon|docs|ops|templates|team|product|patterns|scripts|\.github|\.claude|gates|fixtures|init|src|app|supabase|tests)",
       "the shape check's four exemption lists are exactly: vibeOS, gates and security-checks; plus two public actions for `uses:`; the bare words UTC and GMT; and seventeen in-repo directory names that may open a backticked path")
    ok(all(VIBEOS_ORIGIN.search(u) for u in ("https://github.com/chiibitsu/vibeOS", "https://github.com/chiibitsu/vibeos.git",
                                             "git@github.com:chiibitsu/vibeOS.git", "ssh://git@github.com/chiibitsu/vibeOS"))
       and not any(VIBEOS_ORIGIN.search(u) for u in ("https://github.com/chiibitsu/vibeOS-fork", "https://github.com/notchiibitsu/vibeOS",
                                                     "https://evil.example/https://github.com/chiibitsu/vibeOS", "/tmp/chiibitsu/vibeOS",
                                                     "https://github.com.evil.example/chiibitsu/vibeOS")),
       "derive's origin test accepts the four GitHub spellings of chiibitsu/vibeOS and none of five lookalikes")
    for name, want in ((TIER1[1], ["gates", "gates_ref"]), (REVIEWC[1], ["review"]), (SECURITY[1], [])):
        pins, bad, sec_bad, security = scan(read(os.path.join(RELEASE, name)))
        ok(bad == [] and sec_bad == [] and [k for _, k, _ in pins] == want and security == (name == SECURITY[1]),
           "init/release/%s names the toolkit only in init's own form: %s" % (name, ", ".join(want) or "the security call"))
    real = NAME_DIGEST
    NAME_DIGEST = hashlib.sha256(b"zorblat").hexdigest()  # a stand-in, so no probe names her
    probes = {
        "chiibitsu/example-private": True, "`chiibitsu/example:path/x.md`": True,
        "acme/widget:src/x.py": True, "acme/widget#12": True, "github.com/acme/widget": True,
        "git@github.com:acme/widget.git": True,
        "https://raw.githubusercontent.com/chiibitsu/example/abc/x.py": True,
        "`acme/client-portal`": True, "`acme/widget/docs/plan.md`": True, "`acme/widget@v1`": True,
        "    uses: acme/private-ci/.github/workflows/x.yml@" + "a" * 40: True,
        "Asia/Tokyo": True, "US/Pacific": True, "Japan": True, "GB": True, "NZ": True, "Zulu": True,
        "PST8PDT": True, "UTC+5": True, "utc+5": True, "GMT -05:00": True,
        "2026-11-20T09:00+05:00": True, "(+0530)": True,
        "Zorblat's call": True, "zorblat": True, "## Log it: the ledger": True,
        "**A vault MCP connector rule**": True,
        "Zorblatty": False, "Chiibitsu Labs": False, "Chiibitsu-Labs/gates": False,
        "chiibitsu/vibeOS": False, "`docs/04:12`": False, "`canon/decisions.md`": False,
        "    uses: actions/checkout@" + "a" * 40: False,
        "containers run UTC": False, "the ledger, unheaded": False, "2026-11-09": False,
        "v8.2, PR #208": False, "2.18 MB of notification bodies": False, "Japanese": False,
        "a GBP price": False, "an Asia/Tokyo-based team": True, "Japan-based": True, "Asia/Tokyo+1": True,
        "`gates/secret:README.md`": False, "`clientco/portal`": True,
    }
    for probe, bad in probes.items():
        ok(bool(leaks(probe)) == bad, "the shape check %s %r" % ("catches" if bad else "passes", probe))
    passed = sorted(z for z in ZONES if not leaks(z))
    ok(len(ZONES) >= 600 and passed == sorted(ZONE_PASS),
       "the shape check catches every one of the %d names in init/tznames.txt except %s (let through: %s)"
       % (len(ZONES), " and ".join(sorted(ZONE_PASS)), ", ".join(passed) or "none"))
    ok(_founder("Ask zorblat. Zorblat's call.") == "Ask the founder. The founder's call.",
       "the name swap works by digest and capitalises at a sentence start")
    NAME_DIGEST = real

    # 12. derive: every refusal it can make.
    agents = read(os.path.join(RELEASE, "agents-md.md"))
    plain = agents.replace(POINTER_PARAGRAPH + POINTER_SUFFIX["agents-md.md"] + "\n\n", "")
    ok(plain != agents and derive_text("agents-md.md", plain, label) == agents,
       "re-deriving the committed AGENTS copy, its pointer paragraph removed, gives the committed copy back")
    ok(fires("derive-bare-path", lambda: derive_text("agents-md.md", plain.replace(
        "`chiibitsu/vibeOS:docs/04`", "vibeOS's own `docs/04`", 1), label)),
       "derive refuses a vibeOS path its locator rewrite did not reach")
    ok(fires("derive-no-marker", lambda: derive_text("agents-md.md", "\n".join(
        l for l in plain.split("\n") if not MARKER.search(l)), label)),
       "derive refuses a template with no marker line inside its region")
    ok(fires("derive-edit-count", lambda: _sub("a b", "c", "d")), "a targeted edit that matches nothing stops the derivation")
    ok(fires("derive-edit-count", lambda: _sub("a a", "a", "d")), "a targeted edit that matches twice stops the derivation")
    ok(fires("derive-paragraph", lambda: _in_paragraph("x\n\ny", "**Missing lead**", [])), "an edit whose paragraph is gone stops the derivation")
    ok(fires("derive-caller-form", lambda: derive_text(TIER1[1], "jobs:\n  gates:\n" + G + "  other:\n    with:\n" + Rf, label))
       and not fires("derive-caller-form", lambda: derive_text(TIER1[1], "jobs:\n  gates:\n" + G + "    with:\n" + Rf, label)),
       "derive refuses a tier1 template whose gates_ref sits under another job, and takes the same lines paired")
    ok(fires("derive-caller-form", lambda: derive_text(TIER1[1], G.replace("uses: ", 'uses: "').replace("\n", '"\n') + Rf, label)),
       "derive refuses a caller template whose pins are not in init's form")
    sec_t = read(os.path.join(RELEASE, SECURITY[1]))
    sec_sha = SHA40.search(sec_t).group(0)
    ok(derive_text(SECURITY[1], sec_t, label) == sec_t, "derive passes the committed security caller through unchanged")
    for why, bad_t in (("pinned to a branch", sec_t.replace("@" + sec_sha, "@main")),
                       ("quoted", sec_t.replace("uses: Chiibitsu-Labs/security-checks", 'uses: "Chiibitsu-Labs/security-checks').replace(sec_sha + " # main", sec_sha + '" # main')),
                       ("with no security call at all", "\n".join(l for l in sec_t.split("\n") if "security-checks/.github" not in l)),
                       ("that also calls the toolkit", sec_t + G)):
        ok(bad_t != sec_t and fires("derive-caller-form", lambda: derive_text(SECURITY[1], bad_t, label)),
           "derive refuses a security caller template %s" % why)
    ok(fires("derive-version-mismatch", lambda: derive_all(
        {"agents-md.md": plain.replace("docs/02 " + relv, "docs/02 " + behind, 1)}, rel, label, names=("agents-md.md",))),
       "derive refuses a template whose marker is not the version docs/02 declares")
    ok(fires("derive-leak", lambda: derive_all(
        {"agents-md.md": plain.replace("## Reviewing a change", "## Reviewing a change in Asia/Tokyo", 1)}, rel, label, names=("agents-md.md",))),
       "derive refuses a result that still carries a timezone name")
    vib = fresh("vibeos")
    write(vib, "docs/02-vibecoding-protocol.md", "no version here\n")
    ok(fires("derive-no-protocol", lambda: protocol_of(vib)), "derive refuses a vibeOS whose docs/02 declares no version")
    write(vib, "docs/02-vibecoding-protocol.md", "**Protocol version: v8.2.** x\n")
    ok(protocol_of(vib) == (8, 2, 0), "and reads the version when it is there")
    write(vib, "CITATION.cff", "title: x\n")
    ok(fires("derive-no-citation", lambda: citation_version(vib)), "derive refuses a toolkit whose CITATION.cff declares no version")
    relbad = fresh("release")
    write(relbad, "PROTOCOL", "# nothing\n")
    ok(fires("release-no-protocol", lambda: release_version(relbad)), "a release with no protocol line is refused")
    write(relbad, TIER1[1], G + Rf.replace("\n", " # v9.9\n"))
    write(relbad, REVIEWC[1], Rv.replace("\n", " # v9.9\n"))
    ok(fires("release-no-label", lambda: release_label(relbad)), "a release whose callers do not carry one version label beside every pin is refused")

    origin = fresh("vibeos-origin")
    run(["git", "init", "-q", "--bare"], origin)
    clone = os.path.join(work, "vibeos-clone")
    run(["git", "clone", "-q", origin, clone], work)
    anyorigin = re.compile(".")
    ok(fires("derive-no-main", lambda: published(clone, anyorigin)), "derive refuses a checkout with no origin/main")
    write(clone, "templates/x.md", "a rule\n")
    run(["git", "add", "-A"], clone)
    run(["git"] + env + ["commit", "-q", "-m", "published"], clone)
    run(["git", "push", "-q", "origin", "HEAD:main"], clone)
    run(["git", "fetch", "-q", "origin"], clone)
    head = run(["git", "rev-parse", "HEAD"], clone)
    ok(fires("derive-origin", lambda: published(clone)), "derive refuses a checkout whose origin is not chiibitsu/vibeOS on GitHub")
    ok(published(clone, anyorigin) == head, "derive accepts a clean checkout at origin/main (origin test relaxed for this local clone)")
    write(clone, "templates/y.md", "an unmerged rule\n")
    ok(fires("derive-unclean", lambda: published(clone, anyorigin)), "derive refuses a checkout with an untracked file")
    os.remove(os.path.join(clone, "templates/y.md"))
    write(clone, "templates/x.md", "an edited rule\n")
    ok(fires("derive-unclean", lambda: published(clone, anyorigin)), "derive refuses a checkout with an edited tracked file")
    run(["git", "checkout", "-q", "--", "templates/x.md"], clone)
    commit(clone, "unpushed")
    ok(fires("derive-not-main", lambda: published(clone, anyorigin)), "derive refuses a checkout whose HEAD is not origin/main")

    # 13. the command line.
    empty_cwd, was = fresh("cwd"), os.getcwd()
    os.chdir(empty_cwd)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rcs = [main(["x", "init", "--sha", N]), main(["x", "init", "-t", "--sha", N]),
                   main(["x", "check", a, "--live-standing-orders"]), main(["x", "init", a, "--adopt", "--sha", N]),
                   main(["x", "nope", a]), main(["x", "init", "", "--sha", N])]
    finally:
        os.chdir(was)
    ok(rcs == [2] * 6 and out.getvalue().count("gates_init.py init   TARGET --sha SHA") == 6
       and os.listdir(empty_cwd) == [],
       "a flag where TARGET belongs, an unknown flag, an unknown command and an empty TARGET each exit 2, print the usage, and create nothing")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc_ok, rc_red = cmd_check(a), cmd_check(fresh("empty-check"))
    ok(rc_ok == 0 and rc_red == 1 and out.getvalue().count("FAIL [standing-orders]") == 4,
       "`check` exits 0 on the initialised tree and 1 on an empty one, printing its four failures")

    # 14. THE RULE: every refusal and every red is named, and every named one fired above.
    src = read(os.path.abspath(__file__))
    used_refusals = set(re.findall(r'refuse\("([\w-]+)"', src))
    used_reds = set(re.findall(r'red\(fails, "([\w-]+)"', src))
    ok(used_refusals == set(REFUSALS) and len(REFUSALS) == len(set(REFUSALS)),
       "REFUSALS names exactly the refusals the code makes, both ways (%d)" % len(REFUSALS))
    ok(used_reds == set(REDS) and len(REDS) == len(set(REDS)),
       "REDS names exactly the reds the check records, both ways (%d)" % len(REDS))
    ok(src.count("Problem" + "(") == 3 and src.count("fails.append" + "(") == 1,
       "a Problem is constructed only in refuse() and gathered(), and a failure recorded only in red()")
    never = [x for x in ["refuse:" + s for s in REFUSALS] + ["red:" + c_ for c_ in REDS] if x not in FIRED]
    ok(never == [], "every named refusal and red fired during this selftest (never fired: %s)" % (", ".join(never) or "none"))

    shutil.rmtree(work, ignore_errors=True)
    if failed:
        print("\nselftest: %d check(s) FAILED" % len(failed))
        return 1
    print("\nselftest: every init, check and derive case passed")
    return 0


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if len(argv) >= 3 and argv[1] in ("init", "check", "derive") and argv[2] and not argv[2].startswith("-"):
        target, rest = os.path.realpath(argv[2]), argv[3:]
        if argv[1] == "check" and not rest:
            return cmd_check(target)
        if argv[1] == "derive" and not rest:
            return cmd_derive(target)
        if argv[1] == "init" and len(rest) == 2 and rest[0] == "--sha":
            return cmd_init(target, rest[1])
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
