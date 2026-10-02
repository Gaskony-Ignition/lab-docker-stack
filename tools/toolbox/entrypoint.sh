#!/usr/bin/env bash
#
# Toolbox entrypoint. Two jobs, both about the seam between host and container.
#
#   1. Make the mounted Docker socket usable by whatever uid we were given.
#   2. Turn `wd <words>` into either a make target or a shell command.
#
set -euo pipefail

SOCK=/var/run/docker.sock

# --- the socket group ---------------------------------------------------------
#
# On Linux the socket is owned by root:docker with mode 0660, and the launcher
# runs us as the HOST user so that files written into the bind-mounted repo are
# owned by that user rather than by root. Those two facts collide: the host
# user is not in this container's `docker` group, and the docker CLI answers
# with "permission denied while trying to connect to the Docker daemon socket",
# which reads like Docker not running.
#
# The launcher passes --group-add with the socket's gid, which fixes it without
# anything happening here. This check exists to turn the remaining case into a
# sentence someone can act on, rather than a permissions error from a CLI three
# layers down.
#
# On Docker Desktop (macOS, Windows) the socket is a proxy and this is all moot
# -- it is world-writable and the uid mapping is handled by the VM.
if [ -S "$SOCK" ] && [ ! -w "$SOCK" ]; then
  echo "wd: the Docker socket is mounted but not writable by uid $(id -u)." >&2
  echo "    On Linux the launcher passes --group-add with the socket's group;" >&2
  echo "    if you invoked docker run by hand, add:" >&2
  echo "      --group-add \$(stat -c %g /var/run/docker.sock)" >&2
  exit 1
fi

if [ ! -S "$SOCK" ]; then
  echo "wd: no Docker socket at $SOCK." >&2
  echo "    The toolbox drives the host's Docker daemon; it cannot do anything" >&2
  echo "    without it. Start Docker Desktop (or dockerd) and try again." >&2
  exit 1
fi

# HOME is wherever the launcher's uid points, which on a bind-mounted repo is
# usually a directory that does not exist in here. npm, git and playwright all
# want somewhere writable; /tmp is guaranteed to be.
export HOME="${HOME:-/tmp}"
[ -w "$HOME" ] || export HOME=/tmp

# --- what did they ask for ----------------------------------------------------
#
# `wd bootstrap`, `wd deploy PROJECT=Site1`   -> make targets, the documented
#                                                front door
# `wd bash`, `wd sh`                          -> a shell in the toolbox
# `wd -- <anything>`                          -> run it verbatim, for the cases
#                                                the Makefile does not cover
case "${1:-help}" in
  --)         shift; exec "$@" ;;
  bash|shell) shift; exec bash "$@" ;;
  sh)         shift; exec sh "$@" ;;
  *)          exec make "$@" ;;
esac
