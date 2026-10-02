#!/usr/bin/env bash
#
# Drive the Compose projects as one unit, in the right order.
#
#   scripts/stack.sh up      [stack...]   default: all, in dependency order
#   scripts/stack.sh down    [stack...]   containers only -- data volumes survive
#   scripts/stack.sh restart [stack...]
#   scripts/stack.sh pull    [stack...]
#   scripts/stack.sh logs    <stack>      follows
#   scripts/stack.sh ps
#   scripts/stack.sh status               health + the URL table
#   scripts/stack.sh destroy <stack...>   DESTROYS that stack's volumes
#
# Each stack stays a separate Compose project on purpose -- they start, stop and
# get rebuilt independently. `backbone` is the only thing they share.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cmd="${1:-status}"; shift || true

compose() {
  local stack="$1"; shift
  # wd-control is the only stack that bind-mounts the repo, and the path has to
  # be the one the DAEMON knows -- see host_repo_path in lib.sh. Exported for
  # every compose call rather than just that stack's, because `down`, `restart`
  # and `ps` all interpolate the same compose file and a missing value makes
  # compose refuse the file rather than ignore the line.
  export WD_REPO_HOST="${WD_REPO_HOST:-$(host_repo_path)}"

  # THE CHOSEN IGNITION VERSION, for every compose call rather than just `up`.
  # `down`, `ps` and `config` interpolate the same file, and compose matches
  # existing containers by project+service, not by image -- but a `down` that
  # resolved a different tag than the `up` did would still print a confusing
  # plan. One value, every call.
  #
  # Exported deliberately, and the precedence is the point: the shell
  # environment beats a stack's .env, so this overrides the compose default
  # without editing a file under the read-only mount. That is exactly the
  # mechanism that made `TZ` overwrite nine .env files by accident -- the
  # difference is the name. IGNITION_VERSION is prefixed and referenced by
  # exactly four compose files; TZ was an ordinary word already meaning
  # something to glibc and the JVM. Keep new names prefixed.
  local v; v="$(ign_version_override)"
  if [ -n "$v" ]; then export IGNITION_VERSION="$v"; fi

  # wd-control bind-mounts .wd-local, and Docker creates a MISSING bind source
  # as root -- which the user who owns the checkout then cannot write, so the
  # console's version picker would fail with a permission error nobody could
  # explain. Created here, before any compose call, rather than hoped for.
  wd_local_dir >/dev/null

  local stack_dir; stack_dir="$(stack_path "$stack")"
  ( cd "$stack_dir" && docker compose "$@" )
}

case "$cmd" in

  up)
    need_docker; ensure_backbone
    [ -f "$STACKS_DIR/postgres/.env" ] || die "no .env files yet -- run: make env"

    # Refill the repo-owned volumes before anything mounts them. bootstrap does
    # this too, one step earlier; doing it here as well is what makes a bare
    # `make up` correct on a machine bootstrap has never run on, and it keeps a
    # certificate or an init script edited in the repo from being ignored
    # because the volume still holds the copy taken the first time.
    seed_all
    while IFS= read -r s; do
      say "starting $s"
      compose "$s" up -d
    done < <(resolve_stacks "$@")
    echo
    dim "Gateways take a minute or two to reach RUNNING. Watch with: make status"
    echo
    # Say this here rather than trusting anyone to remember it. A COLD START
    # routinely leaves the Gateway Network edge's historian sink faulted: the
    # edge comes up before the hub is ready to accept storage, the sink caches
    # that failure, and it only retries when a SETTING CHANGES -- so it stays
    # faulted indefinitely while everything else looks healthy. Seen on both
    # machines. `verify-demos` catches it and names the repair; the one thing
    # that must not happen is starting the stack and assuming it is ready.
    dim "Then CHECK IT: make verify-demos  -- a cold start often needs"
    dim "  'make sf-gan-history' before the Gateway Network edge files history."
    ;;

  down)
    need_docker
    # Reverse order on the way down so the spokes let go of the hub first.
    while IFS= read -r s; do
      say "stopping $s"
      compose "$s" down
    done < <(resolve_stacks "$@" | tac)
    ;;

  restart)
    "$0" down "$@"; "$0" up "$@"
    ;;

  pull)
    need_docker
    while IFS= read -r s; do
      say "pulling images for $s"
      compose "$s" pull
    done < <(resolve_stacks "$@")
    ;;

  logs)
    need_docker
    [ $# -ge 1 ] || die "usage: stack.sh logs <stack>"
    compose "$1" logs -f
    ;;

  ps)
    need_docker
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;

  destroy)
    need_docker
    [ $# -ge 1 ] || die "usage: stack.sh destroy <stack...|all>  (refusing to destroy everything implicitly)"

    if [ "$1" = all ]; then
      # The from-scratch path. A DIFFERENT confirmation token from the
      # per-stack one, so muscle memory from destroying one stack cannot take
      # out eleven; read from stdin, so a scripted rebuild can pipe it:
      #     printf 'DESTROY ALL\n' | scripts/stack.sh destroy all
      [ $# -eq 1 ] || die "destroy all takes no other stacks"
      echo "This DESTROYS every stack and every data volume:"
      printf '  %s\n' $(stacks_in_order | tac)
      echo "plus the seeded external volumes, which 'down -v' cannot touch:"
      printf '  %s\n' "$(seeded_volumes | cut -d'|' -f1 | tr '\n' ' ')"
      echo
      echo "Deliberately SURVIVING: .secrets.env, stacks/*/.env, both cert"
      echo "directories, the toolbox image and the backbone network -- a"
      echo "rebuild should not rotate every password or force re-trusting the"
      echo "CA in every browser. Delete those by hand for a true bare start."
      printf "Type exactly 'DESTROY ALL' to continue: "
      read -r confirm
      confirm="${confirm%"$(printf '\r')"}"   # a cmd.exe pipe sends CRLF
      [ "$confirm" = "DESTROY ALL" ] || die "aborted"
      # Reverse start order, so the spokes let go of the hub first.
      while IFS= read -r s; do
        say "destroying $s"
        compose "$s" down -v
      done < <(stacks_in_order | tac)
      # `down -v` skips external volumes BY DESIGN -- that is what external
      # means -- which is exactly why the seeded ones need a second pass.
      while IFS='|' read -r vol _; do
        [ -n "$vol" ] || continue
        docker volume rm "$vol" >/dev/null 2>&1 \
          && ok "removed seeded volume $vol" \
          || warn "could not remove volume $vol (already gone?)"
      done <<EOF
$(seeded_volumes)
EOF
      # NOT the backbone: when this runs inside the toolbox, the toolbox is
      # itself attached to it, so removal would be refused -- and wd recreates
      # it for free anyway.
      ok "everything destroyed -- rebuild with: ./wd bootstrap"
      exit 0
    fi

    echo "This DESTROYS the data volumes for: $*"
    echo "An Ignition gateway rebuilt this way loses its projects, its Gateway Network"
    echo "pairing and its EAM registration, and re-commissions from .env."
    printf 'Type the word DESTROY to continue: '
    read -r confirm
    confirm="${confirm%"$(printf '\r')"}"   # a cmd.exe pipe sends CRLF
    [ "$confirm" = "DESTROY" ] || die "aborted"
    for s in "$@"; do
      say "destroying $s"
      compose "$s" down -v
    done
    ;;

  status)
    need_docker

    # Read the host port out of the stack's own .env rather than hardcoding it.
    # Every published port is ${VAR:-default}, and a machine may override some of
    # them, so a hardcoded table would confidently print the wrong URL.
    #
    # THE `|| true` IS LOAD-BEARING. Not finding the variable is the NORMAL case
    # -- since the 29xxx move no machine needs an override, so make-env.sh writes
    # none and every .env legitimately lacks its PORT_VAR. Under lib.sh's
    # `set -euo pipefail` that grep returns 1, the assignment inherits it, and
    # the whole of `stack.sh status` dies BEFORE its first row: header, rule,
    # exit 1. `make status` is the first command this repo tells anyone to run,
    # and bootstrap ends with it, so a completely successful sixteen-step
    # bootstrap reported `Error 1`. Same shape as the eam-push.sh bug: a grep
    # that is ALLOWED to find nothing, inside a command substitution, under
    # pipefail.
    port_of() {  # port_of <stack> <VAR> <default>
      local envf="$STACKS_DIR/$1/.env" v=''
      if [ -f "$envf" ]; then
        v="$(grep -E "^$2=" "$envf" | tail -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
      fi
      printf '%s' "${v:-$3}"
    }

    # One row per stack, rendered from its manifest. The old hand-written
    # table was a second copy of the stack list that nothing checked; it and
    # STACK_ORDER drifted twice before this.
    render_rows() {
      local s port url note
      for s in $(stacks_in_order); do
        port="$(port_of "$s" "$(meta_get "$s" PORT_VAR)" "$(meta_get "$s" PORT_DEFAULT)")"
        if [ "$(meta_get "$s" STATUS_SCHEME http)" = none ]; then
          url="localhost:$port"
        else
          url="http://localhost:$port"
        fi
        note="$(meta_get "$s" STATUS_NOTE)"
        [ -n "$note" ] && url="$url   ($note)"
        printf '%s|%s\n' "$s" "$url"
      done
    }

    # WHICH RELEASE THIS IS, first line. `make status` is the first command this
    # repo tells anyone to run and the last thing bootstrap prints, so it is the
    # one place a version number is certain to be seen -- and "which release is
    # this machine on" is the question behind almost every report of something
    # being broken on a machine that is not this one.
    printf '%sstack %s%s\n\n' "$C_BOLD" "$(stack_version)" "$C_RESET"

    printf '%-16s %-10s %-12s %s\n' SERVICE STATE HEALTH URL
    printf '%-16s %-10s %-12s %s\n' ------- ----- ------ ---
    while IFS='|' read -r name url; do
      # A stack that is not running is the NORMAL case here -- since the demo
      # console the machine sits at the core and everything else is down, and
      # `status` exists precisely to say so. `docker inspect` on a missing
      # container does two unhelpful things: it writes a blank line to stdout
      # before failing, which would smear the table across three rows, and it
      # exits non-zero, which under lib.sh's `set -euo pipefail` took the whole
      # command down on the FIRST absent container -- header, rule, exit 1, no
      # rows. `tr` handles the first; `|| true` handles the second. Without it
      # `make status` worked only on a machine with every stack up, which
      # is the one state this repo tells you not to be in.
      state="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null | tr -d '\n' || true)"
      health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$name" 2>/dev/null | tr -d '\n' || true)"
      [ -n "$state" ]  || state='-'
      [ -n "$health" ] || health='-'
      case "$state" in
        running) c="$C_GRN" ;;
        -)       c="$C_DIM" ;;
        *)       c="$C_YEL" ;;
      esac
      printf '%-16s %s%-10s%s %-12s %s\n' "$name" "$c" "$state" "$C_RESET" "$health" "$url"
    done <<EOF
$(render_rows)
EOF
    # `make status` is the first command this repo tells anyone to run and the
    # last thing bootstrap prints, which makes it the one surface a notice is
    # certain to be seen on. This READS a file -- no git, no network, silent
    # when there is nothing waiting, and safe in here where there is no ssh
    # key. See scripts/update-check.sh on the look/show split.
    "$REPO_ROOT/scripts/update-check.sh" --show || true
    ;;

  *)
    die "unknown command: $cmd (try: up down restart pull logs ps status destroy)"
    ;;
esac
