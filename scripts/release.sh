#!/usr/bin/env bash
#
# Cut a release: a tag another machine can move to.
#
#   make release BUMP=patch          1.2.0 -> 1.2.1
#   make release BUMP=minor          1.2.0 -> 1.3.0
#   make release BUMP=major          1.2.0 -> 2.0.0
#   make release VERSION=1.3.0       say it outright
#   make release BUMP=minor CHECK=1  every gate, no commit, no tag, no push
#   make release VERSION=1.0.0 NOTES=notes.md    supply the changelog section
#
# WHY A TAG AND NOT A COMMIT. Until this existed, `self-update.sh` followed main
# commit by commit: every machine ran whatever was pushed last, including the
# half of a change whose other half was still being written. A tag is a
# deliberate statement that a tree was checked -- validate clean, demos verified
# -- and it is the only thing a machine away from here can trust. Tracking main
# is still available (`make update TRACK=main`), for a development VM where the
# person breaking it is the person watching it.
#
# WHAT A RELEASE IS. The repo at that tag, and nothing else. There is no
# tarball, no .modl, no image: everything needed to build the stack is either
# committed or fetched by a script that is committed (scripts/get-modules.sh for
# the third-party modules, scripts/make-env.sh for the environment). So "take
# the release" is `git fetch --tags && git checkout v1.3.0` plus the machine-side
# steps in migrations/. docs/RELEASING.md says this at length.
#
# THE GATES ARE THE POINT. A tag that does not validate is worse than no tag: a
# machine will take it precisely because it is a tag. So this refuses to write
# one unless the tree is clean, on main, level with origin, validates with zero
# errors, and the started demos verify. Nothing here is skippable without saying
# so out loud.
#
# IDEMPOTENT AND SAFE TO ABORT. Every step checks its own end state first, so a
# run that dies at the push can be re-run and will carry on from there rather
# than making a second commit. It REFUSES outright if the tag already exists
# anywhere but on HEAD -- re-pointing a tag other machines may already have
# taken is not something a script gets to decide.
#
# HOST ONLY, like self-update.sh: pushing needs your ssh key and the GitHub
# release needs `gh`, and neither is in the toolbox. The gates go back through
# ./wd, because validate and verify-demos need the toolbox and Docker.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${WD_TOOLBOX:-}" = "1" ]; then
  die "release runs on the HOST, not inside the toolbox.
     The push needs your ssh key and the GitHub release needs 'gh'; neither is
     in here. The gates it runs go back through ./wd on their own.

         make release BUMP=patch"
fi

cd "$REPO_ROOT"
WD="$REPO_ROOT/wd"

BUMP=""
WANT=""
CHECK=0
NOTES=""
SKIP_A11Y=0
while [ $# -gt 0 ]; do
  case "$1" in
    --bump)    BUMP="${2:-}"; shift 2 ;;
    --version) WANT="${2:-}"; shift 2 ;;
    --notes)   NOTES="${2:-}"; shift 2 ;;
    --check)   CHECK=1; shift ;;
    --skip-a11y) SKIP_A11Y=1; shift ;;
    *) die "usage: release.sh [--bump patch|minor|major] [--version x.y.z] [--notes FILE] [--check] [--skip-a11y]" ;;
  esac
done

CHANGELOG="$REPO_ROOT/CHANGELOG.md"
# The one project stamped with the version. See docs/RELEASING.md for why it is
# this one and not the sites: Site1/Site2 are pushed to the edges by EAM, so a
# version in their title would claim on an edge whatever the HUB last released,
# whether or not that push has happened -- a number that is wrong exactly when
# somebody is looking at it. Themes is generated wholesale by gen-themes.py and
# a stamp there would be deleted by the next generation. Ops is plumbing nobody
# opens. GatewayAdmin is the console a human reads a version off, and it is
# where the chip lives.
STAMP_PROJECT="$PROJECTS_DIR/GatewayAdmin/project.json"

# --- 1. what version is this? -------------------------------------------------

current="$(stack_version_number)"
if [ -z "$current" ]; then current="0.0.0"; fi

next_version() {  # next_version <current> <patch|minor|major>
  local IFS=. maj min pat
  read -r maj min pat <<EOF
$1
EOF
  case "$2" in
    major) printf '%s.0.0' $((maj + 1)) ;;
    minor) printf '%s.%s.0' "$maj" $((min + 1)) ;;
    patch) printf '%s.%s.%s' "$maj" "$min" $((pat + 1)) ;;
    *) die "BUMP must be patch, minor or major (got '$2')" ;;
  esac
}

if [ -n "$WANT" ] && [ -n "$BUMP" ]; then
  die "pick one: VERSION=x.y.z or BUMP=patch|minor|major, not both"
fi
if [ -n "$WANT" ]; then
  case "$WANT" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) die "VERSION must look like 1.3.0 (got '$WANT')" ;;
  esac
  VER="$WANT"
elif [ -n "$BUMP" ]; then
  VER="$(next_version "$current" "$BUMP")"
else
  die "say which release this is:
         make release BUMP=patch|minor|major
         make release VERSION=1.3.0
     This checkout's VERSION is currently '$current'."
fi
TAG="v$VER"

say "releasing $TAG (from $current)"

# --- 2. the gates -------------------------------------------------------------
#
# Cheapest first, and each one fatal. The order is deliberate: there is no point
# running verify-demos, which takes a minute against four gateways, on a tree
# that is not even on main.

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || die "on branch '$branch' -- releases are cut from main."

dirty="$(git status --porcelain | head -10 || true)"
if [ -n "$dirty" ]; then
  die "the working tree is not clean:

$(printf '%s\n' "$dirty" | sed 's/^/       /')

     A release is a statement about a tree that was checked, and an uncommitted
     edit is not in it. Commit or discard, then re-run."
fi

command -v gh >/dev/null 2>&1 \
  || die "'gh' is not installed -- it is what creates the GitHub release.
     https://cli.github.com/  then: gh auth login"
gh auth status >/dev/null 2>&1 \
  || die "'gh' is not logged in: gh auth login"

say "fetching origin"
git fetch --quiet --tags origin "$branch" \
  || die "fetch failed -- is the credential still good?  ssh -T git@github.com"

# THE TAG CHECK COMES BEFORE THE EXPENSIVE GATES, and it accepts exactly one
# pre-existing tag: this one, already on HEAD, from a run that died after
# tagging. Anything else is a refusal. Moving a tag that another machine may
# already have taken would silently change what "v1.2.0" means on two machines
# that both believe they are current.
RESUME=0
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
  if [ "$(git rev-list -n1 "$TAG")" = "$(git rev-parse HEAD)" ]; then
    RESUME=1
    warn "$TAG already exists and points at HEAD -- resuming an interrupted release"
  else
    die "$TAG already exists, on a different commit ($(git rev-list -n1 "$TAG" | cut -c1-7)).
     A tag is what other machines take; this one will not move it. Pick the
     next version, or delete the tag by hand if it was never pushed."
  fi
fi
ON_ORIGIN=0
if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  ON_ORIGIN=1
fi

# THE DIFFERENCE BETWEEN RESUMING AND RE-CUTTING, which is the whole reason
# there are two paths here.
#
#   tag local, not on origin        an interrupted run. Resume: push it, make
#                                   the GitHub release, say so.
#   tag on origin, no gh release    the last step failed. Resume that step only.
#   tag on origin, gh release too   THIS RELEASE IS DONE. Refuse. Somebody who
#                                   runs it again meant to cut the NEXT one, and
#                                   quietly reporting success for work that was
#                                   finished a week ago teaches them that
#                                   `make release` is a command with no effect.
if [ "$ON_ORIGIN" -eq 1 ] && [ "$RESUME" -eq 1 ] && [ "$CHECK" -eq 0 ]; then
  if gh release view "$TAG" >/dev/null 2>&1; then
    die "$TAG is already released: the tag is on origin and its GitHub release
     exists. Releases are not re-cut -- moving one would change what v$VER
     means on two machines that both believe they are current.

     Cut the next one instead:  make release BUMP=patch
     Or look at this one:       gh release view $TAG"
  fi
  warn "$TAG is on origin but has no GitHub release -- finishing that step only"
fi
if [ "$ON_ORIGIN" -eq 1 ] && [ "$RESUME" -eq 0 ]; then
  die "$TAG is already published on origin, on a commit this checkout is not on.
     Releases are not re-cut. Pick the next version."
fi

behind="$(git rev-list --count "HEAD..origin/$branch" || echo 0)"
if [ "$behind" -gt 0 ]; then
  die "origin/$branch has $behind commit(s) this checkout does not.
     Releasing now would tag a tree that is not what main says. Pull first."
fi

say "validate"
if ! "$WD" validate >/dev/null 2>&1; then
  "$WD" validate 2>&1 | tail -20
  die "validate is not clean -- nothing was written."
fi
ok "validate: 0 errors"

say "verify-demos"
if ! "$WD" verify-demos 2>&1 | tail -40; then
  die "verify-demos did not pass -- nothing was written.
     It checks the demos this machine has STARTED. Start what you are
     releasing (./wd demos, ./wd demo-start DEMO=<id>) and re-run, or fix
     what it named."
fi
ok "verify-demos passed"

if [ "$SKIP_A11Y" -eq 1 ]; then
  warn "console-a11y and perspective-a11y skipped (--skip-a11y)"
else
  say "console-a11y"
  scripts/console-a11y.sh
  ok "console-a11y passed"

  say "perspective-a11y"
  scripts/perspective-a11y.sh
  ok "perspective-a11y passed"
fi

# --- 3. the changelog section -------------------------------------------------
#
# Generated from the commits since the previous tag, grouped, in plain words.
# No marketing: the reader is somebody deciding whether to take this onto a
# machine they are about to present from.

prev_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' "HEAD^{commit}" 2>/dev/null || true)"
if [ "$RESUME" -eq 1 ]; then
  # HEAD is already the tag, so "the previous tag" has to skip it or the range
  # is empty and the section comes out blank on a resumed run.
  prev_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' --exclude "$TAG" "HEAD^{commit}" 2>/dev/null || true)"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SECTION="$WORK/section.md"
TODAY="$(date '+%d/%m/%Y')"

if [ -n "$NOTES" ]; then
  [ -f "$NOTES" ] || die "no such notes file: $NOTES"
  {
    printf '## %s -- %s\n\n' "$TAG" "$TODAY"
    cat "$NOTES"
  } > "$SECTION"
  ok "changelog section taken from $NOTES"
elif [ -z "$prev_tag" ]; then
  # THE FIRST RELEASE WILL NOT INVENT A HISTORY. There is no previous tag, so
  # "the commits since it" is every commit this repo has ever had -- hundreds of
  # them, most of which are steps towards something that was then rewritten.
  # Dumping that list would be true and useless. Write the summary yourself.
  die "this is the first release -- there is no previous tag to diff from, and
     the whole history ($(git rev-list --count HEAD) commits) is not a changelog.
     Write the section by hand and pass it in:

         make release VERSION=$VER NOTES=path/to/notes.md

     The file holds the section BODY only (### headings and bullets); the
     '## $TAG -- $TODAY' line is added for you."
else
  range="$prev_tag..HEAD"
  n="$(git rev-list --count "$range" || echo 0)"
  say "changelog: $n commit(s) since $prev_tag"

  # GROUPED BY DEMONSTRATION, not by commit type. The reader is somebody
  # deciding whether to take this onto a machine they present from, and the
  # question they actually have is "did the MQTT demo change?" -- which a list
  # sorted into Added/Fixed/Changed cannot answer without reading all of it.
  #
  # A commit that touches two demos appears under BOTH, deliberately: the point
  # is that a reader interested in one demonstration can read one heading and
  # stop, and a shared change is genuinely news for both. Anything matching no
  # area lands under "Other", never dropped -- a changelog that silently loses
  # a commit is worse than one that files it untidily.
  #
  # THE MAPPING LIVES HERE AND NOWHERE ELSE. Add a demo, add a row. The order of
  # the rows is the order of the headings.
  git log --no-merges --reverse --name-only --pretty=format:'@@%h%x09%s' "$range" \
    > "$WORK/log.txt" || true

  python3 - "$WORK/log.txt" "$TAG" "$TODAY" > "$SECTION" <<'PY'
import sys

log_path, tag, today = sys.argv[1], sys.argv[2], sys.argv[3]

# area name -> path prefixes that belong to it. Checked in order; a commit joins
# every area it matches.
AREAS = [
    ("MQTT Demo", (
        "stacks/ignition-edge3", "stacks/ignition-edge4",
        "scripts/sparkplug-", "scripts/ign-mqtt.sh", "scripts/distributor-setup.sh",
        "scripts/mqtt-", "ignition/edge-projects/SparkplugEdge",
        "docs/SPARKPLUG.md", "docs/MQTT-DISTRIBUTOR.md", "docs/MQTTS.md",
        "ignition/projects/GatewayAdmin/ignition/script-python/sparkplug_demo",
        "com.inductiveautomation.perspective/views/Sparkplug",
    )),
    ("EAM Demo", (
        "stacks/ignition-edge1", "stacks/ignition-edge2",
        "scripts/eam-push.sh", "scripts/ign-gan.sh", "scripts/ign-modules.sh",
        "docs/EAM.md", "ignition/projects/Site1", "ignition/projects/Site2",
        "com.inductiveautomation.perspective/views/EAM",
    )),
    ("Store & Forward Demo", (
        "scripts/sf-", "scripts/pg-", "scripts/sf-historian.sh",
        "stacks/postgres", "docs/STORE-FORWARD.md",
        "com.inductiveautomation.perspective/views/StoreForward",
        "ignition/projects/GatewayAdmin/ignition/script-python/sf_demo",
    )),
    ("Redundancy Demo", (
        "stacks/ignition-backup", "stacks/ignition-ha",
        "scripts/ign-redundancy.sh", "docs/REDUNDANCY.md",
        "com.inductiveautomation.perspective/views/Redundancy",
    )),
    ("Theming & inheritance", (
        "ignition/themes", "ignition/projects/Themes", "scripts/gen-themes.py",
        "scripts/ign-themes.sh", "docs/THEMES.md",
    )),
    ("Demo console", (
        "ignition/projects/GatewayAdmin", "control/", "demos.json",
        "stacks/wd-control", "docs/DEMO-CONSOLE.md",
    )),
]

# LAST, AND ONLY IF NOTHING ELSE MATCHED. Its prefixes are `scripts/` and
# `docs/`, which match nearly every commit in the repo -- listed alongside the
# others it would put almost everything under two headings and drown the
# per-demo view this grouping exists to give. So it is a fallback tier: the
# heading means "this changed the rig, not one demonstration".
FALLBACK = [
    ("The rig", (
        "scripts/", "stacks/", "tools/", "Makefile", "wd", "wd.cmd",
        "migrations/", "docs/", "README.md", "CLAUDE.md", "VERSION",
        "CHANGELOG.md", ".github/", ".gitignore",
    )),
]

commits = []   # (sha, subject, [paths])
for line in open(log_path):
    line = line.rstrip("\n")
    if line.startswith("@@"):
        sha, _, subject = line[2:].partition("\t")
        commits.append((sha, subject, []))
    elif line and commits:
        commits[-1][2].append(line)

# The type prefix is stripped and the scope kept: `feat(mqtt): an engineer can`
# reads as `mqtt: an engineer can`, which says where the change landed without
# the jargon. Most of this repo's history is plain sentences and is left alone.
TYPES = ("feat", "fix", "docs", "refactor", "perf", "chore", "test",
         "build", "ci", "style", "revert")


def plain(subject):
    head, sep, rest = subject.partition(": ")
    if not sep:
        return subject
    t = head.rstrip("!")
    scope = ""
    if "(" in t and t.endswith(")"):
        t, _, scope = t.partition("(")
        scope = scope[:-1]
    if t not in TYPES:
        return subject
    return "%s: %s" % (scope, rest) if scope else rest


ALL = AREAS + FALLBACK
buckets = {name: [] for name, _ in ALL}
other = []


def touches(paths, prefixes):
    return any(p.startswith(pre) or pre in p for p in paths for pre in prefixes)


for sha, subject, paths in commits:
    hit = False
    for name, prefixes in AREAS:
        if touches(paths, prefixes):
            buckets[name].append((sha, subject))
            hit = True
    if not hit:
        for name, prefixes in FALLBACK:
            if touches(paths, prefixes):
                buckets[name].append((sha, subject))
                hit = True
    if not hit:
        other.append((sha, subject))

out = ["## %s -- %s\n" % (tag, today)]
for name, _ in ALL:
    rows = buckets[name]
    if not rows:
        continue
    out.append("### %s\n" % name)
    for sha, subject in rows:
        out.append("- %s (`%s`)" % (plain(subject), sha))
    out.append("")
if other:
    out.append("### Other\n")
    for sha, subject in other:
        out.append("- %s (`%s`)" % (plain(subject), sha))
    out.append("")
print("\n".join(out))
PY
fi

# The section, spliced in under the preamble. A section for THIS version already
# present -- an aborted run -- is replaced rather than duplicated.
CHANGELOG_HEAD='# Changelog

Every release is a git tag, and this file says what is in each one. Written by
`scripts/release.sh` from the commits since the previous tag; see
[docs/RELEASING.md](docs/RELEASING.md) for how a release is cut and what one
contains.

Newest first. Dates are DD/MM/YYYY.
'

rewrite_changelog() {
  local tail_body=''
  if [ -f "$CHANGELOG" ]; then
    # Everything from the first `## v` heading down, minus any section for this
    # version (start-of-section to just before the next one).
    tail_body="$(awk -v tag="## $TAG " '
      /^## v/ { started = 1 }
      started && index($0, tag) == 1 { skip = 1; next }
      started && /^## v/ && index($0, tag) != 1 { skip = 0 }
      started && !skip { print }
    ' "$CHANGELOG" || true)"
  fi
  {
    printf '%s\n' "$CHANGELOG_HEAD"
    cat "$SECTION"
    if [ -n "$tail_body" ]; then printf '%s\n' "$tail_body"; fi
  } > "$CHANGELOG.tmp"
  mv "$CHANGELOG.tmp" "$CHANGELOG"
}

# --- 4. the stamp -------------------------------------------------------------
#
# House rule: a released Ignition project carries its version in the Title and
# at the end of the Description, so any gateway or Designer shows at a glance
# which release it is running. The project NAME never changes.
#
# Both edits strip an existing stamp first, so re-stamping is replacement rather
# than accumulation -- otherwise the third release would read
# "Gateway Admin v1.0.0 v1.1.0 v1.2.0".
stamp_project() {  # stamp_project <version>
  [ -f "$STAMP_PROJECT" ] || die "no $STAMP_PROJECT to stamp"
  python3 - "$STAMP_PROJECT" "$1" <<'PY'
import json, re, sys

path, ver = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)

title = re.sub(r'\s+v\d+\.\d+\.\d+$', '', cfg["title"]).rstrip()
desc  = re.sub(r'\s*·\s*v\d+\.\d+\.\d+$', '', cfg["description"]).rstrip()

cfg["title"] = f"{title} v{ver}"
cfg["description"] = f"{desc} · v{ver}"

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(cfg["title"])
PY
}

# --- 5. write, commit, tag, push ---------------------------------------------

if [ "$CHECK" -eq 1 ]; then
  echo
  say "--check: every gate passed. Nothing was written."
  dim "  version would be:  $TAG"
  dim "  changelog section:"
  sed 's/^/      /' "$SECTION"
  exit 0
fi

if [ "$RESUME" -eq 0 ]; then
  printf '%s\n' "$VER" > "$VERSION_FILE"
  rewrite_changelog
  ok "CHANGELOG.md updated"
  ok "stamped: $(stamp_project "$VER")"

  # The stamp is a project resource, so it has to still validate -- a
  # project.json this script wrote badly would otherwise be tagged.
  if ! "$WD" validate >/dev/null 2>&1; then
    "$WD" validate 2>&1 | tail -20
    die "the stamped tree does not validate. VERSION, CHANGELOG.md and
     project.json have been written but NOT committed -- look, fix, re-run."
  fi

  # Staged BY NAME. Never `git add -A` here: this repo is worked on in parallel
  # and a release commit that swept up somebody's half-finished view would be
  # tagged and pushed before anyone noticed.
  git add -- "$VERSION_FILE" "$CHANGELOG" "$STAMP_PROJECT"
  if [ -n "$(git diff --cached --name-only || true)" ]; then
    git commit -q -m "release: $TAG

$(sed '1,2d' "$SECTION")

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
    ok "committed $(git rev-parse --short HEAD)"
  else
    warn "nothing changed -- VERSION, changelog and stamp already say $TAG"
  fi

  # ANNOTATED, not lightweight: an annotated tag carries a message, a tagger and
  # a date, which is what makes `git describe` and `gh release` meaningful and
  # what lets anybody see who cut it and when without reading the reflog.
  git tag -a "$TAG" -F - <<EOF
$TAG

$(sed '1,2d' "$SECTION")
EOF
  ok "tagged $TAG (annotated)"
fi

say "pushing main and the tag"
git push --quiet origin "$branch" || die "push of $branch failed -- the tag is
     local only. Sort the push out and re-run; this will resume."
git push --quiet origin "refs/tags/$TAG" || die "push of $TAG failed. Re-run to resume."
ok "pushed"

# --- 6. the GitHub release ----------------------------------------------------
#
# NO BINARY ARTEFACTS, deliberately. The repo AT THE TAG is the deliverable:
# there is nothing to build and nothing licensed to redistribute (the
# third-party .modl files are fetched by scripts/get-modules.sh and are not
# ours to attach). An asset here would be a second copy of the truth, and the
# one that goes stale.
if gh release view "$TAG" >/dev/null 2>&1; then
  warn "a GitHub release for $TAG already exists -- left alone"
else
  # A REAL FILE, not a process substitution. `--notes-file <(...)` hands gh a
  # fifo, which it cannot seek or re-read, and the failure mode is a release
  # created with empty notes -- which looks like it worked.
  sed '1,2d' "$SECTION" > "$WORK/notes.md"
  gh release create "$TAG" --title "$TAG" --notes-file "$WORK/notes.md" >/dev/null \
    || die "the tag is pushed but 'gh release create' failed. Re-run to resume,
     or create it by hand from the CHANGELOG.md section."
  ok "GitHub release created"
fi

echo
ok "$TAG released -- $(gh release view "$TAG" --json url --jq .url 2>/dev/null || echo 'see GitHub')"
dim "  another machine takes it with:  make update"
dim "  what that does:  docs/RELEASING.md"
