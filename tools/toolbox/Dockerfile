# The toolbox: one runtime for every command in this repo, on every machine.
#
# WHY THIS IMAGE EXISTS
#
# The stack itself was always portable -- named volumes, container-name
# addressing, every host port overridable from a .env. The TOOLING was not, and
# it failed in ways that are invisible until someone else tries:
#
#   * `declare -A` and `mapfile` are bash 4. macOS ships bash 3.2, so four
#     scripts died on a Mac with a syntax error at a line that reads fine.
#   * `find -printf` and `xargs -r` are GNU. BSD find on macOS has neither.
#   * Playwright was loaded from a hard-coded path under one person's home
#     directory, and it is a ~400 MB per-machine install besides.
#   * The gateway credentials came from a machine-wide file outside the repo.
#   * Windows has no make, and Git Bash rewrites container paths on the way
#     into `docker exec` unless you know to stop it.
#
# Fixing each of those in the scripts is possible and is a tax forever: every
# future script has to be written for the union of three platforms, and the
# failure mode is always "works here, breaks on the machine you cannot see".
# So instead the scripts run in ONE place -- here -- and the host only needs
# Docker and git. The `wd` launcher at the repo root is the front door.
#
# The container is a sibling of the stack, not a parent: it talks to the host's
# Docker daemon through the mounted socket, so `docker compose up` from inside
# starts containers OUTSIDE, next to it. It joins `backbone` for the same
# reason the gateways address each other by container name -- from in here,
# the hub is `http://ignition:8088` on every machine, and the host port
# mappings (which differ per machine) stop mattering at all.

# The Docker CLI and the Compose plugin, lifted from the official image rather
# than added from an apt repository: it is one COPY, it pins cleanly, and it
# avoids carrying Docker's apt key and repo into an image that has no business
# installing anything from Docker at runtime.
FROM docker:28-cli AS dockercli

FROM node:22-bookworm-slim

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose \
                      /usr/local/libexec/docker/cli-plugins/docker-compose

# bash: the scripts are bash, and node:*-slim gives you dash as /bin/sh.
# python3: validate.py, normalise.py and the JSON handling in several scripts.
# openssl: stacks/*/create-certs.sh mints the local CA and the broker cert.
# make: the Makefile is the documented front door and should keep working.
# curl, ca-certificates: module downloads and gateway health checks.
# git: `make validate` and the pre-commit hook, and so the team can commit from
#      inside the toolbox if they would rather not install git on the host.
# tini: PID 1 that reaps children and forwards signals, so Ctrl-C on a long
#      `make logs` stops it instead of orphaning the docker CLI underneath.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash python3 openssl make curl ca-certificates git tini procps \
    && rm -rf /var/lib/apt/lists/*

# Playwright, once, in the image. ign-gw.js drives the gateway's configuration
# UI headlessly and -- more importantly -- gets its API session cookie by
# logging in through a browser, because 8.3 answers HTTP Basic from curl with
# 401. So this is not optional decoration: without it every REST call in
# scripts/ fails to authenticate.
#
# --with-deps pulls the shared libraries Chromium needs on Debian. It is the
# bulk of the image (~600 MB) and the reason the first build takes a few
# minutes. Chromium only: Firefox and WebKit are another 500 MB for nothing.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN npm install -g playwright@1.56.1 \
    && npx --yes playwright@1.56.1 install --with-deps chromium \
    && npm cache clean --force

# So `require('playwright')` resolves from any working directory, including the
# bind-mounted repo, which has no node_modules of its own and should not need
# one.
ENV NODE_PATH=/usr/local/lib/node_modules

# paho-mqtt: wd-control's THE WIRE listener (control/wire.py) subscribes to the
# broker and copies each message to the hub's witness route. Pinned to the
# newest stable release (checked on PyPI 18/09/2026). pip only for this one
# pure-Python package; --break-system-packages because Debian marks its python3
# externally managed and this image IS the environment, there is no venv to use.
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
    && pip3 install --no-cache-dir --break-system-packages paho-mqtt==2.1.0 \
    && apt-get purge -y python3-pip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Marks the environment for the scripts. `.gateways.env` carries two URLs per
# gateway -- one by container name for in here, one by host port for a browser
# on the host -- and this is how ign-gw.js knows which of the two it is.
ENV WD_TOOLBOX=1

COPY entrypoint.sh /usr/local/bin/wd-entrypoint
RUN chmod +x /usr/local/bin/wd-entrypoint

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/wd-entrypoint"]
CMD ["help"]
