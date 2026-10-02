#!/usr/bin/env bash
#
# Deploy an Ignition project from this repo into a gateway container.
#
#   scripts/ign-deploy.sh <Project> [gateway]      gateway defaults to `ignition`
#   scripts/ign-deploy.sh --all     [gateway]      every project under PROJECT_ROOTS
#   scripts/ign-deploy.sh <Project> <gateway> --as <Name>
#                                  land it under another name -- how an isolated
#                                  edge receives ignition/edge-projects/SparkplugEdge
#                                  as `Edge`, the one project an Edge will run
#   ... --no-scan                  copy only; for a caller that restarts the
#                                  gateway anyway (a first deploy onto a stub
#                                  Edge, which has no AutoScan to answer a scan)
#
# The repo is the source of truth for project RESOURCES. The gateway is the
# source of truth for everything else (device connections, EAM registration,
# Gateway Network pairing, module config) -- none of that is file-based and none
# of it is touched here.
#
# Three things have to be right or the deploy silently does nothing:
#
#   1. The resource signature. Every resource.json carries a
#      lastModificationSignature computed by the gateway. If a file changes
#      underneath it, the signature no longer matches and the project scan
#      SKIPS that resource without complaint. We strip the signature and stamp
#      the modification as `external`, which is the documented way to tell the
#      gateway "trust what is on disk".
#   2. File ownership. Files arriving via `docker cp` land as root; the gateway
#      process cannot then rewrite them.
#   3. The scan itself. External edits never auto-apply -- see ign-scan.sh.
#
# Signatures are fixed in a staging copy, never in the repo, so deploying does
# not leave timestamp churn in your git diff.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROJ_DIR_IN_CONTAINER=/usr/local/bin/ignition/data/projects

# Where a project tree may live. There is exactly one root now.
#
# This used to be two: `ignition/projects` plus `.styles-build`, the styles
# template unpacked from a PRIVATE submodule's published zip. That template is
# gone -- the token layer is a gateway theme (see scripts/gen-themes.py) and the
# style classes are vendored into `ignition/projects/Themes`. So a clone needs
# nothing off the network, and there is no tree to produce before deploying.
PROJECT_ROOTS="$PROJECTS_DIR"

# Projects that go to ONE gateway, by name, and never to the hub. An isolated
# Sparkplug edge has no Gateway Network, so EAM cannot push to it: its project
# is deployed directly, under the only name an Edge gateway will run (`--as
# Edge`). It must not live under ignition/projects, or `--all ignition` would
# put it on the hub as one more project. So this root is searched only for a
# NAMED deploy, and after PROJECT_ROOTS -- a name clash still means the hub's.
EDGE_PROJECTS_DIR="$REPO_ROOT/ignition/edge-projects"

# First root that actually holds the project wins.
project_src() {
  local name="$1" root
  for root in $PROJECT_ROOTS $EDGE_PROJECTS_DIR; do
    if [ -f "$root/$name/project.json" ]; then printf '%s\n' "$root/$name"; return 0; fi
  done
  return 1
}

# `--as <Name>`, anywhere on the line, pulled out first so the positional
# arguments below keep exactly the shape they always had.
AS=""
NO_SCAN=0
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --as)      [ -n "${2:-}" ] || die "--as needs a project name"; AS="$2"; shift 2 ;;
    --no-scan) NO_SCAN=1; shift ;;
    *)         ARGS+=( "$1" ); shift ;;
  esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}

ALL=0
if [ "${1:-}" = "--all" ]; then ALL=1; shift; fi
if [ "$ALL" -eq 1 ] && [ -n "$AS" ]; then
  die "--as renames ONE project; it cannot be combined with --all"
fi

if [ "$ALL" -eq 1 ]; then
  GATEWAY="${1:-ignition}"
  # -printf is GNU-only and macOS ships BSD find; -exec dirname keeps this
  # working on every host the toolbox runs on.
  PROJECTS="$(for root in $PROJECT_ROOTS; do
      [ -d "$root" ] || continue
      find "$root" -maxdepth 2 -name project.json -exec dirname {} \; 2>/dev/null
    done | sed 's#.*/##' | sort -u)"
  [ -n "$PROJECTS" ] || die "no projects found under: $PROJECT_ROOTS"
  # shellcheck disable=SC2206
  PROJECTS=($PROJECTS)
else
  [ $# -ge 1 ] || die "usage: ign-deploy.sh <Project> [gateway]   |   ign-deploy.sh --all [gateway]"
  PROJECTS=("$1")
  GATEWAY="${2:-ignition}"
fi

need_docker
require_gateway "$GATEWAY"

# The gateway runs as a non-root user; match whatever owns the projects dir
# rather than assuming a uid, because that has changed between image versions.
OWNER="$(docker exec "$GATEWAY" stat -c '%u:%g' "$PROJ_DIR_IN_CONTAINER")"
dim "gateway $GATEWAY, projects owned by $OWNER"

# `docker cp` always writes as root, and the gateway container runs as a
# non-root user that then cannot delete or rewrite what landed. Every step that
# touches ownership therefore runs with -u 0, and the tree is handed back to the
# gateway user immediately afterwards.
as_root() { docker exec -u 0 "$GATEWAY" "$@"; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

for project in "${PROJECTS[@]}"; do
  src="$(project_src "$project")" \
    || die "no such project: $project (looked in: $PROJECT_ROOTS $EDGE_PROJECTS_DIR)"
  [ -f "$src/project.json" ] || die "$project has no project.json -- is it really an Ignition project?"
  # The name it lands under on the gateway. Only --as makes the two differ.
  target="${AS:-$project}"

  say "deploying $project -> $GATEWAY${AS:+ as $AS}"

  # --- validate before anything leaves the machine ---------------------------
  # One interpreter for the whole tree. A project this size has ~2,250 JSON
  # files; one python3 per file turns a two-second check into a five-minute one.
  python3 - "$src" <<'PY' || die "$project has invalid JSON -- nothing was deployed"
import json, os, sys
bad = []
for dirpath, _, files in os.walk(sys.argv[1]):
    for f in files:
        if not f.endswith('.json'):
            continue
        p = os.path.join(dirpath, f)
        try:
            with open(p) as fh:
                json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            bad.append(f'{p}: {e}')
for b in bad[:10]:
    print(f'  invalid JSON -- {b}', file=sys.stderr)
sys.exit(1 if bad else 0)
PY

  # --- stage + fix signatures -----------------------------------------------
  rm -rf "${STAGE:?}/$target"
  cp -a "$src" "$STAGE/$target"

  python3 - "$STAGE/$target" <<'PY'
import json, os, sys, datetime

root = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
fixed = 0

for dirpath, _, files in os.walk(root):
    if 'resource.json' not in files:
        continue
    p = os.path.join(dirpath, 'resource.json')
    with open(p) as fh:
        d = json.load(fh)

    # The signature is what the scan checks. Drop it (both nestings seen in the
    # wild) and mark the change as external so the gateway takes the file as-is.
    attrs = d.setdefault('attributes', {})
    attrs.pop('lastModificationSignature', None)
    d.pop('lastModificationSignature', None)
    attrs['lastModification'] = {'actor': 'external', 'timestamp': now}

    with open(p, 'w') as fh:
        json.dump(d, fh, indent=2)
        fh.write('\n')
    fixed += 1

print(f'  signatures reset on {fixed} resource(s)')
PY

  # --- push ------------------------------------------------------------------
  # Remove the old tree first: docker cp merges, so a resource deleted in the
  # repo would otherwise linger on the gateway forever.
  as_root rm -rf "$PROJ_DIR_IN_CONTAINER/$target"
  docker cp "$STAGE/$target" "$GATEWAY:$PROJ_DIR_IN_CONTAINER/$target"
  as_root chown -R "$OWNER" "$PROJ_DIR_IN_CONTAINER/$target"

  # --- verify the bytes actually arrived ------------------------------------
  here_count=$(find "$STAGE/$target" -type f | wc -l)
  there_count=$(docker exec "$GATEWAY" find "$PROJ_DIR_IN_CONTAINER/$target" -type f | wc -l)
  [ "$here_count" -eq "$there_count" ] \
    || die "file count mismatch after copy: local $here_count, gateway $there_count"
  ok "$target -- $there_count files on $GATEWAY"
done

echo
if [ "$NO_SCAN" -eq 1 ]; then
  dim "deployed, NOT scanned (--no-scan): the caller applies it -- nothing is live yet"
  exit 0
fi
say "deployed; now make it live"
"$REPO_ROOT/scripts/ign-scan.sh" "$GATEWAY"
