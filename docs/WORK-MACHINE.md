# Running this on another machine

Changes are made on one machine and pushed; the machine that runs the demos takes
releases from GitHub.

```
  dev machine  ──push──▶  GitHub  ──pull──▶  demo machine
                          CI validates      docker + Ignition run
```

The demo machine needs nothing but a git checkout and `make`.

## First-time setup

```bash
git clone https://github.com/Gaskony-Ignition/demo-docker-stack.git ~/Ignition-Demos-Stack
cd ~/Ignition-Demos-Stack
```

A plain clone is the whole story: no `--recurse-submodules`, no credential
prompt. The styling is vendored; see [PORTABLE.md](PORTABLE.md).

### If the stacks are already running from `~/docker`

They almost certainly are, with commissioned gateway volumes you do not want to lose.
**Do not run `make bootstrap`** — it is for a blank machine.

The volumes are keyed by Compose project name. The compose files here default to the
same project names the original `~/docker` folder used (`ignition`, `postgres`, …), so a
checkout adopts the existing containers and volumes as-is. Confirm before doing anything:

```bash
cd stacks/ignition && docker compose config | grep '^name:'    # expect: ignition
docker volume ls | grep ignition                                # expect: ignition_ignition_data
```

If both match, carry your existing `.env` files across and you are done:

```bash
# One name per stack folder -- derived, so a stack added later is included.
for s in $(ls -d stacks/*/ | xargs -n1 basename); do
  cp ~/docker/$s/.env ~/Ignition-Demos-Stack/stacks/$s/.env
done
cp -r ~/docker/npm/certs   ~/Ignition-Demos-Stack/stacks/npm/certs
make env        # backfills any variable added since those files were written
make validate
```

Then `make status` should show your running stack. Retire `~/docker` only once you are
satisfied, and keep it until then.

> `make env` never overwrites an existing `.env`; it only appends variables that the
> committed `.env.example` has gained. That is how a setting added on the personal side
> reaches the work machine without anyone editing files by hand.

### On a genuinely blank machine

```bash
make bootstrap
```

Generates `.env` files with fresh passwords, issues the CA and certificates, creates the
`backbone` network, starts everything in order, installs the projects and restarts the
hub once. Passwords land in `.secrets.env` (chmod 600, gitignored).

## Day to day

```bash
git pull
make validate        # catch anything CI would have caught
make deploy-all      # push every project to the hub, then scan
make status
```

`make deploy` never restarts a gateway, so it is safe with sessions open.

**A pull that changes `ignition/themes/` needs one more step, and only that
case.** A gateway theme is a config resource, not a project resource, so
`deploy-all` does not carry it and a theme that is already installed does not
recompile on its own:

```bash
make themes          # install the vendored themes onto every gateway
```

It reports which gateways are serving the installed bytes and which are still
serving what they compiled at startup. A gateway in the second list needs a
restart -- there is no config-scan API to drive from a script, so that is the
only lever. `bootstrap` sidesteps it by installing themes before the one
restart it already performs.

### If `git pull` cannot authenticate

The Windows credential manager only answers an INTERACTIVE prompt; a session
without a terminal (ssh from another machine, a scheduled task) gets
`could not read Username` and there is no flag that fixes it. The offline path
is a git bundle, which needs no credentials on this side:

```bash
# on a machine that CAN reach GitHub:
git bundle create wd.bundle <last-commit-here>..main
# copy it across, then on this machine:
git fetch /path/to/wd.bundle main && git merge --ff-only FETCH_HEAD
```

**A bundle now carries everything**, which it did not when the styling was a
submodule: a bundle holds only this repo's own objects, so the styling had to
be fetched separately whatever the bundle contained. Vendoring removed that
second path -- the themes and their style classes are ordinary tracked files
and travel in the bundle with the rest.

## What is NOT in this repo

By design — none of it is file-based, and some of it is machine-specific:

- Gateway commissioning, admin passwords, licences
- Gateway Network pairing and EAM controller/agent registration ([EAM.md](EAM.md))
- Database connections, MQTT module configuration ([MQTTS.md](MQTTS.md))
- **Each gateway's public HTTP address** — without one the Perspective Session
  Launcher errors with `MissingGatewayAddressException`. Set it per machine with
  that machine's addresses (Config → Web Server, or `POST /data/config/web-server`
  — see [ARCHITECTURE.md](ARCHITECTURE.md#gateway-public-addresses))
- The local CA and TLS private keys — each machine generates its own
- Docker named volumes, which hold every gateway's real state

A `.gwbk` from each gateway is the only backup of that state. Take one before anything
destructive: **Config → System → Backup/Restore**. Do not commit it — it contains the
gateway's credentials, and `make check-secrets` will refuse it.

## Ports

**There are no longer any port differences between the two machines.** Every
published port lives in a private 29xxx block — hub `29088/29043`, edge1
`29188/29143`, edge2 `29288/29243` — and the full table is in CLAUDE.md.

That block exists precisely so this section can be short. Obvious port numbers
(8088, 5432, 8080) collide with whatever else a machine is already running --
a per-machine override in a stack's gitignored `.env` handles that case.
Nothing on either machine wants a 29xxx port, so both run the committed
defaults unchanged.

`80` and `443` are the exception and do not move: they are what
`https://ignition.test` means.

Every published port is still a `${VAR:-default}`, so a future clash is one line
in a gitignored `.env` rather than a code change — but needing one again is a
sign something new has moved in, not the normal case.

## Getting the repo

The repo is public, so an https clone needs no credentials, and `make update`
pulls the same way. A machine that already pulls over SSH with a deploy key keeps
working unchanged.

The arrangement is designed so that only source text crosses — no
credentials, no gateway backups, no licensed binaries.
