#!/usr/bin/env bash
#
# v1.0.0 -- the toolbox image tag, and the wd-control container running it.
#
# WHAT CHANGED. The toolbox image gained `paho-mqtt 2.1.0`, which THE WIRE
# (control/wire.py) needs to subscribe to the broker directly instead of relying
# on EMQX's rule engine. The tag went 1 -> 2, in three places that must agree:
# `TAG` in `wd`, `TAG` in `wd.cmd`, and `image:` in stacks/wd-control/compose.yaml.
#
# WHY A PULL IS MOSTLY, BUT NOT QUITE, ENOUGH. The launcher is self-healing: the
# pull brings `TAG=2`, `docker image inspect wd-toolbox:2` fails, and the next
# `./wd <anything>` builds it. Nothing needed.
#
# THE GAP IS THE RUNNING CONTAINER. wd-control was started from wd-toolbox:1,
# which has no paho. Building the image does not recreate the container, and
# self-update reports a changed compose file rather than applying it -- so THE
# WIRE stays dead with ModuleNotFoundError until somebody recreates wd-control.
# That is this migration.
#
# RECREATING wd-control IS ALLOWED. self-update's own rule is that a stack
# carrying no GATEWAY can be restarted at any time and no demonstration
# notices; wd-control is KIND=service. It is out for a few seconds, the page
# reconnects, and nothing else in the rig is touched.
#
# IT ALSO CHECKS THE THREE TAGS AGREE, because nothing else does -- `make
# validate` has no rule for it, and all three were bumped by hand.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

IMAGE=wd-toolbox

# `tr -d '\r'` on wd.cmd is not decoration: it is a Windows batch file and its
# lines end CRLF, so a `$`-anchored pattern never matches -- which reported the
# tag as <unreadable> and made this migration refuse to run at all.
wd_tag()      { sed -n 's/^TAG=\([0-9][0-9]*\)$/\1/p' "$REPO_ROOT/wd" | head -1; }
wdcmd_tag()   {
  tr -d '\r' < "$REPO_ROOT/wd.cmd" | sed -n 's/^set "TAG=\([0-9][0-9]*\)"$/\1/p' | head -1
}
compose_tag() {
  sed -n "s|^ *image: $IMAGE:\([0-9][0-9]*\) *$|\1|p" \
    "$STACKS_DIR/wd-control/compose.yaml" | head -1
}

TAG="$(wd_tag)"
[ -n "$TAG" ] || die "cannot read TAG out of $REPO_ROOT/wd -- has its shape changed?"

# --- 1. the three tags agree --------------------------------------------------
t2="$(wdcmd_tag)"; t3="$(compose_tag)"
if [ "$TAG" = "$t2" ] && [ "$TAG" = "$t3" ]; then
  mig_ok "toolbox tag $TAG, agreed by wd, wd.cmd and wd-control's compose file"
else
  # Not repairable from here: which of the three is right is a judgement about
  # what the Dockerfile now contains, and guessing would either rebuild for
  # nothing or leave wd-control on an image without the library it needs.
  die "the toolbox tag disagrees between the three places that must match:
       wd                          TAG=$TAG
       wd.cmd                      TAG=${t2:-<unreadable>}
       stacks/wd-control/compose   $IMAGE:${t3:-<unreadable>}
     Nothing validates this, so fix it in the repo -- not on this machine."
fi

# --- 2. the image exists ------------------------------------------------------
if docker image inspect "$IMAGE:$TAG" >/dev/null 2>&1; then
  mig_ok "$IMAGE:$TAG is built"
else
  mig_do "building $IMAGE:$TAG (first time on this machine, a few minutes)" \
    docker build -t "$IMAGE:$TAG" "$REPO_ROOT/tools/toolbox"
fi

# --- 3. wd-control is running THAT image --------------------------------------
if ! container_exists wd-control; then
  dim "     wd-control does not exist here -- compose will start it on the right image"
  mig_finish
fi

running_image="$(docker inspect -f '{{.Config.Image}}' wd-control 2>/dev/null || true)"
if [ "$running_image" = "$IMAGE:$TAG" ]; then
  mig_ok "wd-control is running $IMAGE:$TAG"
else
  mig_do "recreating wd-control: it is on '${running_image:-unknown}', not $IMAGE:$TAG (no gateway is touched)" \
    "$WD" up STACK=wd-control
fi

mig_finish
