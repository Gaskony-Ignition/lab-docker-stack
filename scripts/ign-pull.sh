#!/usr/bin/env bash
#
# Pull a project OUT of a gateway and into this repo.
#
#   scripts/ign-pull.sh <Project> [gateway]      gateway defaults to `ignition`
#   scripts/ign-pull.sh --list    [gateway]      what projects does that gateway have?
#
# Use this after doing work in the Designer, so the repo catches up with what a
# human changed. The gateway is always the source of truth for a project you did
# not author here -- a local copy is stale by default.
#
# Signatures are normalised on the way in so that a pull followed immediately by
# a deploy produces no diff. Without that, every round trip would churn a
# hundred timestamps and make review useless.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROJ_DIR_IN_CONTAINER=/usr/local/bin/ignition/data/projects

if [ "${1:-}" = "--list" ]; then
  GATEWAY="${2:-ignition}"
  need_docker; require_gateway "$GATEWAY"
  say "projects on $GATEWAY"
  docker exec "$GATEWAY" sh -c "ls -1 $PROJ_DIR_IN_CONTAINER" | while IFS= read -r p; do
    title=$(docker exec "$GATEWAY" sh -c "cat $PROJ_DIR_IN_CONTAINER/$p/project.json 2>/dev/null" \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"parent={d.get('parent') or '-'} inheritable={d.get('inheritable')} enabled={d.get('enabled')}\")" 2>/dev/null || echo '')
    printf '  %-24s %s\n' "$p" "$title"
  done
  exit 0
fi

[ $# -ge 1 ] || die "usage: ign-pull.sh <Project> [gateway]   |   ign-pull.sh --list [gateway]"
PROJECT="$1"; GATEWAY="${2:-ignition}"

need_docker; require_gateway "$GATEWAY"

docker exec "$GATEWAY" test -d "$PROJ_DIR_IN_CONTAINER/$PROJECT" \
  || die "gateway $GATEWAY has no project '$PROJECT' (see: ign-pull.sh --list $GATEWAY)"

dest="$PROJECTS_DIR/$PROJECT"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT

say "pulling $PROJECT from $GATEWAY"
docker cp "$GATEWAY:$PROJ_DIR_IN_CONTAINER/$PROJECT" "$STAGE/$PROJECT"

# Normalise so repeated pulls are diff-stable: drop the gateway-computed
# signature and the timestamp, both of which change on every gateway write and
# carry no information we want under version control.
python3 - "$STAGE/$PROJECT" <<'PY'
import json, os, sys
root = sys.argv[1]
n = 0
for dirpath, _, files in os.walk(root):
    if 'resource.json' not in files:
        continue
    p = os.path.join(dirpath, 'resource.json')
    with open(p) as fh:
        d = json.load(fh)
    attrs = d.setdefault('attributes', {})
    attrs.pop('lastModificationSignature', None)
    d.pop('lastModificationSignature', None)
    attrs['lastModification'] = {'actor': 'external', 'timestamp': '1970-01-01T00:00:00Z'}
    with open(p, 'w') as fh:
        json.dump(d, fh, indent=2); fh.write('\n')
    n += 1
print(f'  normalised {n} resource(s)')
PY

if [ -d "$dest" ]; then
  backup="$REPO_ROOT/.pull-backup/$PROJECT-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$(dirname "$backup")"
  cp -a "$dest" "$backup"
  dim "  previous copy backed up to ${backup#$REPO_ROOT/}"
  rm -rf "$dest"
fi

mv "$STAGE/$PROJECT" "$dest"
ok "$PROJECT -> ignition/projects/$PROJECT ($(find "$dest" -type f | wc -l) files)"

echo
dim "Review before committing:  git diff --stat ignition/projects/$PROJECT"
