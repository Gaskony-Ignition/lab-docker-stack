#!/usr/bin/env bash
#
# Blank Ubuntu VM  ->  a running Ignition demo stack, in one paste.
#
# THE CHICKEN AND EGG: this lives in the repo it clones, so on the VM you have
# not got it yet. Open it on github.com, copy, and paste into a file there:
#
#     nano blank-vm.sh          # paste, ^O ^X
#     chmod +x blank-vm.sh && ./blank-vm.sh
#
# No curl-pipe-bash line is offered on purpose. The repo is private, so a raw
# URL needs a token in it, and a token on a command line goes into shell
# history -- for a script that runs as root, or as you with sudo.
# Pasting it costs ten seconds and you get to read what you are running.
#
# EITHER PRIVILEGE WORKS: run it as root (which is all some VM images give you)
# or as a normal user with sudo. See the note at the top of the preflight for
# what actually differs -- and for the one thing you must not do, which is mix
# the two.
#
# It is in the repo rather than in a wiki so it is versioned with the thing it
# installs: when bootstrap grows a step or a port moves, this moves in the same
# commit.
#
# Installs Docker (official repo), git and the GitHub CLI, signs you in
# interactively, clones the repo and runs `./wd bootstrap`. Then it stops and
# tells you what to click.
#
# SAFE TO RE-RUN. Every step checks before it acts, so if bootstrap dies
# halfway you fix the cause and run this again rather than unpicking it.
#
# Tested against Ubuntu 22.04 and 24.04. Wants ~20 minutes, most of it
# bootstrap pulling images.

set -euo pipefail

REPO_SLUG="${REPO_SLUG:-Gaskony-Ignition/demo-docker-stack}"
TARGET="${TARGET:-$HOME/Ignition-Demos-Stack}"

# ---------------------------------------------------------------------------
# talking
# ---------------------------------------------------------------------------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; Z=$'\033[0m'
else B=""; G=""; Y=""; R=""; Z=""; fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$Z"; }
ok()   { printf '  %sok%s   %s\n' "$G" "$Z" "$*"; }
warn() { printf '  %swarn%s %s\n' "$Y" "$Z" "$*"; }
die()  { printf '\n%sstopped:%s %s\n\n' "$R" "$Z" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. preflight -- everything that would waste twenty minutes if wrong
# ---------------------------------------------------------------------------
step "checking this machine"

# ROOT IS FINE, and on a lot of VM images it is all you get -- DigitalOcean,
# Hetzner and Vultr hand you root; AWS, Azure and GCP hand you a sudo user.
# Both work, and the difference is smaller than it looks:
#
#   as root      no sudo, no docker group (root already reaches the socket),
#                and hosts-setup.sh writes /etc/hosts directly because it
#                tests `[ -w ]` before it reaches for sudo. Everything ends up
#                owned by root, which is self-consistent: `wd` runs the toolbox
#                as `--user $(id -u):$(id -g)`, so as root that is 0:0 and the
#                repo it writes into is root's too.
#   as a user    sudo for apt and /etc/hosts, and the docker group -- which is
#                where the one real trap lives, further down.
#
# What you must NOT do is mix them: bootstrap as root and then drive the stack
# as a normal user, or the .env files, the CA and the deployed projects are all
# root-owned and `wd` cannot rewrite them. Pick one and stay with it.
if [ "$(id -u)" -eq 0 ]; then
  AS_ROOT=1
  SUDO=""
  ok "running as root -- no sudo and no docker group needed"
  warn "everything will be owned by root, including $TARGET and its .secrets.env.
       Fine for a throwaway demo VM. If this box is going to have real users on
       it, Ctrl-C and run as one of them instead:
           adduser demo && usermod -aG sudo demo && su - demo"
else
  AS_ROOT=0
  SUDO="sudo"
  command -v sudo >/dev/null || die "not root, and no sudo on this box.
     Install sudo, or re-run this as root."
  sudo -v || die "sudo refused."
  ok "running as $USER, with sudo"
fi

. /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
  *ubuntu*|*debian*) ok "$PRETTY_NAME" ;;
  *) die "this script is apt-only and you are on '${PRETTY_NAME:-unknown}'.
     The stack itself runs anywhere Docker does -- only this installer is Ubuntu-shaped." ;;
esac

# EVERY image this stack pulls is multi-arch, arm64 included. Checked against
# the registry rather than assumed, 26/08/2026:
#
#   inductiveautomation/ignition:8.3.9   amd64, arm64, arm/v7  (24/09/2026)
#   jc21/nginx-proxy-manager:2.15.1      amd64, arm64
#   postgres:18.4, haproxy:3.0.7-alpine  amd64, arm64 + more
#
# and the toolbox is built here from docker:28-cli + node:22-bookworm-slim,
# both multi-arch. The third-party .modl files are JVM bytecode, so they do not
# care either.
#
# An earlier version of this script warned that Ignition was amd64-only and
# would not run. That was simply wrong, and wrong in the expensive direction --
# it would have turned somebody away from a VM that works.
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64) ok "architecture amd64" ;;
  arm64) ok "architecture arm64 (every image in the stack is multi-arch)"
         warn "arm64 is supported but far less exercised than amd64 here.
       If something behaves oddly, that is worth mentioning when you report it." ;;
  *) die "unsupported architecture '$ARCH' -- the images publish amd64 and arm64." ;;
esac

# The whole stack is nine Compose projects. Core plus one demo is comfortable in
# 8 GB; all nine at once measured ~15 GB on the machine this came from, which is
# why the repo insists you start a demo and stop it again rather than leaving
# everything up.
MEM_GB=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024 / 1024 ))
if   [ "$MEM_GB" -lt 6 ];  then warn "${MEM_GB} GB RAM -- expect swapping. 8 GB is the comfortable floor."
elif [ "$MEM_GB" -lt 12 ]; then ok "${MEM_GB} GB RAM (fine for core + one demo at a time)"
else ok "${MEM_GB} GB RAM"; fi

DISK_GB=$(( $(df -Pk /var/lib 2>/dev/null | awk 'NR==2{print $4}') / 1024 / 1024 ))
[ "$DISK_GB" -lt 30 ] && warn "${DISK_GB} GB free on /var -- images and volumes want ~30 GB." \
                      || ok "${DISK_GB} GB free for images and volumes"

# 80 and 443 are the front door and are deliberately NOT moved -- they are what
# https://ignition.test means. Everything else this stack publishes is in 29xxx
# precisely so it cannot collide, but these two can, and a stock VM image that
# happens to ship nginx or apache is exactly how.
if command -v ss >/dev/null; then
  for p in 80 443; do
    if ss -ltnH "sport = :$p" 2>/dev/null | grep -q .; then
      warn "something is already listening on :$p -- the proxy stack will fail to start.
       Find it with: sudo ss -ltnp 'sport = :$p'"
    fi
  done
  ok "checked ports 80 and 443"
fi

# ---------------------------------------------------------------------------
# 1. packages
# ---------------------------------------------------------------------------
step "installing git, curl and the basics"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq ca-certificates curl gnupg git >/dev/null
ok "$(git --version)"

# ---------------------------------------------------------------------------
# 2. Docker, from Docker's own repository
# ---------------------------------------------------------------------------
# NOT Ubuntu's `docker.io`: it lags, and it does not carry the Compose *plugin*
# (`docker compose`, two words). This repo drives `docker compose` throughout,
# and the standalone `docker-compose` v1 binary is not a substitute -- it parses
# some of these files differently and is end-of-life.
step "installing Docker"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  ok "already present: $(docker --version)"
else
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
    | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  # UBUNTU_CODENAME first: on a derivative (Mint, Pop!_OS) VERSION_CODENAME is
  # the derivative's own name, which Docker's repo has never heard of.
  CODENAME="${UBUNTU_CODENAME:-$VERSION_CODENAME}"

  # AND DOCKER MAY NOT PUBLISH FOR IT YET. A brand-new or interim Ubuntu
  # release exists for months before Docker cuts a matching suite, and the
  # failure is a poor one to debug: `apt-get update` succeeds (the repo
  # answers, it just has no such suite), and the install then fails with
  # "Unable to locate package docker-ce", which reads like a typo or a
  # missing repo rather than a release Docker has not reached.
  #
  # So ask the repository, and fall back to the newest suite it does have.
  # Docker's packages are not codename-specific in practice; an LTS suite on
  # an interim release is the same fallback Docker's own convenience script
  # makes.
  if ! curl -fsI "https://download.docker.com/linux/${ID}/dists/${CODENAME}/Release" >/dev/null 2>&1; then
    for try in noble jammy focal; do
      if curl -fsI "https://download.docker.com/linux/${ID}/dists/${try}/Release" >/dev/null 2>&1; then
        warn "Docker publishes nothing for '${CODENAME}' yet -- using '${try}' packages instead."
        CODENAME="$try"
        break
      fi
    done
  fi

  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} ${CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
       docker-buildx-plugin docker-compose-plugin >/dev/null
  ok "$(docker --version)"
fi

$SUDO systemctl enable --now docker >/dev/null 2>&1 || true

# THE TRAP THIS SCRIPT EXISTS TO STEP OVER -- and root simply walks around it.
#
# usermod changes the group database, not the process you are running in: group
# membership is baked into a process at login and cannot be added to a shell
# that is already running. So a script that installs Docker and then calls it
# gets `permission denied on /var/run/docker.sock`, which reads exactly like a
# broken install, and the usual advice ("log out and back in") is useless inside
# a script that has not finished.
#
# Root owns the socket already, so none of this applies there.
if [ "$AS_ROOT" -eq 0 ]; then
  if id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    ok "$USER is already in the docker group"
  else
    $SUDO usermod -aG docker "$USER"
    ok "added $USER to the docker group"
  fi
fi

# FOUR WAYS TO GET THE GROUP APPLIED, TRIED IN ORDER, because there is no one
# mechanism present everywhere.
#
#   1. nothing needed   root, or a shell that already logged in with the group
#                       (which is what a re-run in a fresh session looks like)
#   2. sg               the classic. NOT ALWAYS INSTALLED: on current Ubuntu it
#                       comes from `util-linux-extra`, which a minimal cloud
#                       image does not carry -- and it moved there from `login`,
#                       so "it has always been in the base system" is no longer
#                       true. Reported from a 26.04 VM: `sg: command not found`.
#   3. sudo -g docker   runs one command with that group without needing sg at
#                       all. We are already using sudo on this path, so it costs
#                       nothing.
#   4. give up cleanly  and say the one thing that always works.
#
# The probe below decides between them by RUNNING DOCKER, not by testing for the
# binaries, so whatever it picks is known to work rather than assumed to.
DOCKER_RUN=""
if docker version >/dev/null 2>&1; then
  DOCKER_RUN() { bash -c "$*"; }; DOCKER_RUN=direct
elif command -v sg >/dev/null 2>&1 && sg docker -c 'docker version >/dev/null' 2>/dev/null; then
  DOCKER_RUN() { sg docker -c "$*"; }; DOCKER_RUN=sg
elif [ "$AS_ROOT" -eq 0 ] && sudo -u "$USER" -g docker docker version >/dev/null 2>&1; then
  # -E preserves HOME and the rest; some sudoers configurations refuse it, so
  # fall back to a plain -u/-g with HOME set explicitly rather than failing.
  if sudo -E -u "$USER" -g docker true >/dev/null 2>&1; then
    DOCKER_RUN() { sudo -E -u "$USER" -g docker bash -c "$*"; }
  else
    DOCKER_RUN() { sudo -u "$USER" -g docker env "HOME=$HOME" bash -c "$*"; }
  fi
  DOCKER_RUN=sudo-g
fi

# NAME WHICH THING FAILED. The first version of this said "docker is installed
# but not usable even with the group applied -- check the daemon", which was
# wrong in the one case it actually fired: the group was fine and the daemon was
# fine, and `sg` did not exist. A message that blames the daemon for a missing
# binary sends someone to `systemctl status docker`, which reports everything
# healthy, and there the trail ends.
if [ -z "$DOCKER_RUN" ]; then
  if ! $SUDO docker version >/dev/null 2>&1; then
    die "the Docker daemon is not responding, even under sudo.
     Check it: sudo systemctl status docker"
  fi
  die "the daemon is fine and $USER is in the docker group, but this shell
     cannot use it, and no mechanism to borrow the group is available
     (no 'sg' -- on current Ubuntu that is in util-linux-extra -- and
     'sudo -g' did not work either).

     The fix that always works is to start a shell that has the group:
         exit        # then log back in
         cd $TARGET && ./wd bootstrap

     Or install sg and re-run this script:
         sudo apt-get install -y util-linux-extra"
fi
ok "docker responds (group applied via: $DOCKER_RUN)"

# ---------------------------------------------------------------------------
# 3. GitHub CLI, and signing in
# ---------------------------------------------------------------------------
# The repo is PRIVATE, so the clone needs credentials. gh is the least painful
# way to do that on a fresh VM: `gh auth login` does a device-code flow, so it
# works over ssh with no browser on the box, and `gh auth setup-git` then leaves
# plain `git pull` working afterwards -- which a one-off token pasted into a URL
# does not.
step "installing the GitHub CLI"
if command -v gh >/dev/null; then
  ok "already present: $(gh --version | head -1)"
else
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | $SUDO gpg --dearmor -o /etc/apt/keyrings/githubcli.gpg --yes
  $SUDO chmod a+r /etc/apt/keyrings/githubcli.gpg
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/githubcli.gpg] \
https://cli.github.com/packages stable main" \
    | $SUDO tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq gh >/dev/null
  ok "$(gh --version | head -1)"
fi

step "signing in to GitHub"
if gh auth status >/dev/null 2>&1; then
  ok "already signed in as $(gh api user --jq .login 2>/dev/null || echo '?')"
else
  echo "  This part is interactive -- pick HTTPS when it asks, and say yes to"
  echo "  authenticating git with your GitHub credentials."
  echo
  gh auth login || die "sign-in did not complete."
fi
gh auth setup-git 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. the repo
# ---------------------------------------------------------------------------
step "getting the repo"
# FOUR STATES, not two. The first version tested `-d $TARGET/.git` and treated
# everything else as "clone it", which is wrong the moment the directory exists
# without being a checkout: git refuses a non-empty target and the script's own
# "clone failed" added nothing to git's message and named no way forward.
# Reported from a VM where ~/Ignition-Demos-Stack already existed.
if [ -e "$TARGET" ] && [ ! -d "$TARGET" ]; then
  die "$TARGET exists and is not a directory.
     Move it aside, or run with a different target:
         TARGET=~/wd ./$(basename "$0")"

elif [ -d "$TARGET/.git" ]; then
  # A checkout -- but OF WHAT? Pulling without looking means a directory that
  # happens to be some other repo gets a --ff-only pull that either fails
  # confusingly or, worse, succeeds and leaves you bootstrapping the wrong tree.
  origin="$(git -C "$TARGET" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    *"$REPO_SLUG"*)
      ok "already cloned at $TARGET -- pulling"
      git -C "$TARGET" pull --ff-only \
        || warn "pull failed (local commits, or a dirty tree) -- carrying on
       with what is on disk. Check it with: git -C $TARGET status" ;;
    "")
      die "$TARGET is a git repository with no 'origin' remote.
     If it is this repo, add one; otherwise pick another target:
         TARGET=~/wd ./$(basename "$0")" ;;
    *)
      die "$TARGET is a checkout of something else:
         $origin
     Expected $REPO_SLUG. Pick another target rather than pulling over it:
         TARGET=~/wd ./$(basename "$0")" ;;
  esac

elif [ -d "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
  die "$TARGET already exists, is not a git checkout, and is not empty:

$(ls -A "$TARGET" | head -10 | sed 's/^/         /')

     Nothing here will delete it -- it is your directory and this script has no
     idea what is in it. Either move it aside and re-run:
         mv $TARGET ${TARGET}.old
     or clone somewhere else:
         TARGET=~/wd ./$(basename "$0")"

else
  # Absent, or present and empty -- git is happy with an existing empty dir.
  #
  # A PLAIN clone is the whole story: no submodules, no --recurse, nothing to
  # init. The styling is vendored into the repo precisely so this step cannot
  # half-succeed the way it used to.
  gh repo clone "$REPO_SLUG" "$TARGET" \
    || die "clone failed -- see git's message above.
     Common causes: the account signed in above has no access to
     $REPO_SLUG, or the network is blocked."
  ok "cloned to $TARGET"
fi

[ -f "$TARGET/ignition/themes/themes.json" ] \
  || die "the clone looks incomplete -- ignition/themes/ is missing.
     Bootstrap would build four healthy gateways serving no projects."
ok "styling present ($(ls "$TARGET/ignition/themes" | grep -vc themes.json) themes)"

# ---------------------------------------------------------------------------
# 5. bootstrap
# ---------------------------------------------------------------------------
step "bootstrap -- this is the long one"
cat <<'NOTE'
  Roughly 15-20 minutes. It builds the toolbox image, generates environment
  files and a local CA, fetches the third-party modules, starts nine Compose
  projects, installs the modules and the themes, deploys the projects, restarts
  the hub once, then wires up the database, the Gateway Network, EAM, MQTT over
  TLS, store-and-forward and the redundant pair.

NOTE
if [ "$AS_ROOT" -eq 0 ]; then
  echo "  It will ask for your sudo password once more, to put the .test names in"
  echo "  /etc/hosts. That step runs on the host rather than in the toolbox"
  echo "  container, because the hosts file belongs to the machine."
  echo
fi
read -r -p "  Enter to go, Ctrl-C to stop here: " _ || true

cd "$TARGET"
DOCKER_RUN "cd '$TARGET' && ./wd bootstrap"

# ---------------------------------------------------------------------------
# 6. what now
# ---------------------------------------------------------------------------
step "done -- what to look at"
cat <<NOTE

  Check it is genuinely demo-ready (reads the running gateways, not the repo):

      cd $TARGET
      ./wd verify-demos

  Then:

      https://ignition.test                the hub, through the proxy
      http://localhost:29088               the hub, direct
      http://localhost:29485               the demo console
      http://localhost:29081               the proxy admin

  Site pages need the /demo route -- http://localhost:29088/data/perspective/client/Site1/demo

  Finish with  ./wd down , not docker stop: it REMOVES the containers, which is
  what stops a Docker daemon restart resurrecting the whole stack later. The
  data volumes survive.

  KEEPING THIS MACHINE CURRENT. A demo box drifts behind main and nobody
  notices, so the point is being TOLD -- 'make update-check' looks, and
  'scripts/login-notice.sh --install' puts the answer on every login. Neither
  changes anything; 'make update' is what takes it. Set up the daily timer and
  the read-only deploy key with docs/SELF-UPDATE.md.

NOTE
if [ "$AS_ROOT" -eq 0 ]; then
  cat <<'NOTE'
  ONE THING BEFORE YOU DRIVE IT YOURSELF: you are in the docker group now, but
  THIS shell is not -- membership is fixed at login. Open a new session, or
  'newgrp docker', or ./wd will fail on the socket exactly as described above.

NOTE
fi
