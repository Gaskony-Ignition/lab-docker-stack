# Keeping another machine current

A demo VM drifting three weeks behind `main` is not a dramatic failure. It is a
customer meeting where the thing you are about to show was fixed a fortnight ago
on the machine you are not standing at. Nobody notices, which is the whole
problem — so the job is **being told**, and the update itself is the easy half.

**It follows RELEASES, not commits.** `make update` goes to the newest git tag
-- a tree somebody checked, with validate clean and the demos verified
([RELEASING.md](RELEASING.md)). Following `main` commit by commit meant every
machine ran the half of a change whose other half was still being written; that
is still available, deliberately, for a development machine where the person
breaking it is the person watching it.

```bash
make update                  the newest release
make update TRACK=main       commit by commit, for a development VM
make update VERSION=v1.2.0   a named release, forwards or back
```

A machine on a release sits on a **detached HEAD** at its tag. That is the
honest description of where it is, and it is also the freeze: nothing moves it
until somebody runs `make update`.

Two arrangements. Pick one; they use the same two scripts.

| | you are told, you decide | nobody is told, it just happens |
|---|---|---|
| the timer runs | `update-check.sh --quiet` | `self-update.sh` |
| what it does | looks, records, **changes nothing** | pulls and applies |
| you see | a notice at login, in `make status`, in `verify-demos` | a journal entry |
| you run | `make update` when it suits | nothing |

**The first is the default and the one to set up.** Every run that finds
something deploys projects to a live gateway, and a machine you present from is
one where you want to choose the moment. The second exists for a machine nobody
ever logs into at all.

```bash
make update-check      # is anything waiting? looks; changes nothing
make update            # take it: pull, validate, apply
make update CHECK=1    # say what applying would do, change nothing
```

## Being told: `update-check.sh`

```bash
make update-check                  # look now, and say
scripts/update-check.sh --quiet    # look and record, print nothing (the timer)
scripts/update-check.sh --show     # print the RECORDED answer -- no network
```

**The work is split by what it costs, not by what it is about.** Looking means
talking to GitHub, which needs the ssh key, which means the host and a second or
two of network. Showing means reading one file: no git, no credential, about a
millisecond, and it works inside the toolbox where there is no key at all.

```text
LOOK   once a day, on a timer          ->  writes .update-state
SHOW   every login, every make status  ->  reads it
```

That split is the reason the banner is safe. Put a `git fetch` in a shell
profile and every new terminal blocks on the network — on a bad link the machine
simply feels broken, and the fix anyone reaches for is deleting the banner.

Nothing in `update-check.sh` ever applies anything. It reports; you run
`make update`.

### Where the notice appears

- **At login**, if you install it (below).
- **`make status`** — the first command this repo tells anyone to run, and the
  last thing `bootstrap` prints.
- **`make verify-demos`** — the moment before a demonstration is exactly when
  being a fortnight behind matters, and exactly when you do not want a script
  going off to fetch something. It prints the recorded answer only.

All three are silent when there is nothing waiting. A line saying "nothing to
do" on every login is a line nobody reads by the third day.

```text
  ↓ 2 releases waiting: v1.0.0 → v1.2.0  (checked 4h ago)
      v1.1.0  MQTT Demo, Demo console -- 7 change(s)
      v1.2.0  Redundancy Demo, The rig -- 4 change(s)
      take it:  cd ~/Ignition-Demos-Stack && make update
```

**It counts RELEASES, and lists every one it is behind.** "3 updates waiting"
over a commit count told you how much had been pushed, which is not a decision
anybody can act on -- half of it may be one change in progress. Each line's
summary is read from that tag's own `CHANGELOG.md`, which is already grouped by
demonstration, so the areas named are exactly the words somebody wants.

A machine deliberately on `main` past the newest tag says so instead of nagging:
`on the newest release (v1.2.0) and 7 commits past it on main`.

**The age is part of the notice on purpose.** A check that quietly stopped
working looks exactly like a machine that is up to date, so past a week the
notice says so instead of staying silent — the same reasoning as the readiness
snapshot's age on the Demos page. A failed fetch keeps the previous answer and
says it is stale; "3 waiting, checked 2 days ago" is more use to somebody about
to present than nothing at all.

### The login banner

```bash
scripts/login-notice.sh --install     # adds it to this user's ~/.bashrc
scripts/login-notice.sh --remove
```

**It guards on an interactive shell**, and that guard is not decoration:
`~/.bashrc` is sourced by things that are not people — `scp` and `rsync` among
them, which abort outright if the far end writes anything on connect. A helpful
banner that breaks file copies to the machine is the classic way this goes
wrong.

### The timer that fills it in

```ini
# /etc/systemd/system/wd-update-check.service
[Unit]
Description=Look for Ignition-Demos-Stack updates (reports only)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=<your-user>
WorkingDirectory=/home/<your-user>/Ignition-Demos-Stack
ExecStart=/home/<your-user>/Ignition-Demos-Stack/scripts/update-check.sh --quiet
```

```ini
# /etc/systemd/system/wd-update-check.timer
[Unit]
Description=Look for Ignition-Demos-Stack updates

[Timer]
OnCalendar=*-*-* 07:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now wd-update-check.timer
systemctl list-timers wd-update-check.timer
```

**Morning rather than the small hours**, unlike the applying timer below: this
one only has to have run before you sit down, and `Persistent=true` catches up a
run missed while the VM was suspended — which for a demo box is the normal state
rather than the exception.

## Taking it: `self-update.sh`

```bash
make update            # pull main and apply what changed
make update CHECK=1    # say what would happen, change nothing
scripts/self-update.sh --force   # re-apply everything, whatever the record says
```

### A pull by hand is not an update

**`git pull` on its own changes nothing the gateway is running.** Projects are
files the gateway only picks up on a scan; run a pull and the Demos page keeps
the buttons it had.

The apply is derived from `before..after`: the commits *this run* pulled. That
assumes this script is the only thing that ever moves `HEAD`, and it is not.
Two ways that silently applies nothing, both ending with a repo that is
current and a gateway that is not:

- **`git pull` by hand, then `make update`.** There is nothing left to pull, so
  `before == after`, so it said **"already up to date"** and deployed nothing —
  and it would say that for ever, because the commits it needed to act on were
  already behind `HEAD`. Pulling by hand is the obvious thing to do, and it is
  what actually happened.
- **The stack was down during a run.** The pull landed, the apply was correctly
  skipped, and the next run computed an empty diff from the new `HEAD`. Never
  applied, never mentioned again.

So the base is now **what was last successfully applied**, recorded in the
gitignored `.update-applied` and written only *after* the apply — never after
the pull, and never on the hub-is-down path. With no record (a fresh clone, or
the first run after this change) it applies everything once and says so, because
`deploy-all` never restarts a gateway: being wrong that way costs a few seconds,
and being wrong the other way is invisible.

If a gateway is rebuilt or its volume destroyed, the repo is right and the
gateway is empty with the record still claiming otherwise — that is what
`--force` is for.

**It runs on the HOST, not in the toolbox** — the same exception
`hosts-setup.sh` is, and for a related reason: the pull needs a **git
credential**, and that credential is an ssh key in the host user's `~/.ssh`. The
toolbox has no `~/.ssh` and must not be given one — a private key bind-mounted
into the container every script runs inside is a key you can no longer reason
about.

So `make update` (or `scripts/self-update.sh`) rather than `./wd update`. Run it
in the toolbox and git answers

```text
fatal: could not read Username for 'https://github.com'
```

which reads as a broken remote rather than as a container with no keys, so the
script guards for it and says which command you wanted. The steps that need
Docker and the gateway — validate, deploy, theme install — still go back through
`./wd`: the work needing your *identity* stays outside, the work needing the
*toolbox* goes in.

### The machine-side half: migrations

**`git checkout v1.2.0` moves every file, and files are not the whole of a
release.** The `wd-mqtt` network does not exist because a compose file mentions
it. A container created last week is not on a network added this week. A
generated token is not in git by design. A module lives in a gateway's Docker
volume; a trigger lives in Postgres; a retired stack leaves a container nothing
can see.

Reporting these as `stacks/ changed -- NOT applied` and leaving them is right
for a gateway restart and wrong for everything else: it leaves a machine current
in git and broken in fact, with nothing saying so.

So between the move and the deploy, `make update` runs `scripts/migrate.sh`,
which applies every pending migration in order and records each one. A migration
that needs a human -- a gateway restart, a sudo -- **defers**: it prints the
command, stays pending, and is offered again. That is not a failure and does not
fail the update.

The order is deliberate: **after validate**, because rule 2 below applies to
machine state at least as much as to projects and a tree that does not validate
must not be allowed to reconfigure a broker; **before the deploy**, because a
release's migrations bring the machine up to what the new tree assumes.

`make update` then ends on `verify-demos` -- the only step that asks whether the
machine can still do its job.

Full rules for writing one, and what cannot be undone by going back, are in
[RELEASING.md](RELEASING.md).

### An update overwrites settings you changed by hand

Some migrations re-run the setup scripts, and those write gateway settings back
-- which is what makes them idempotent. **Updates win, and that is the right
rule**; losing a day's poking about without being told is not. So `make update`
prints the recorded drift answer before it applies anything:

```bash
make drift        which settings differ now (read-only, and slow -- it is
                  dozens of gateway logins; QUICK=1 does the hub alone)
make snapshot     keep a copy of what this machine has first
```

A handful of settings are meant to be kept for good, and those live in
`.demo-settings.env` where an update cannot reach them -- the table is in
[RELEASING.md](RELEASING.md).

### On the page: the stack chip

The Demos tab's rig strip carries a chip beside the Ignition one, from
`/state`'s `version` block through `demo_control._stack()`:

| state | chip | colour |
|---|---|---|
| on a release, unchanged, nothing newer | `stack v1.0.1` | neutral |
| something changed here | `stack v1.0.1 · modified` | warn |
| a newer release waiting | `stack v1.0.1 → v1.2.0` | warn |
| both | `stack v1.0.1 · modified → v1.2.0` | warn |
| no release / control plane down | hidden | |

*Modified* comes first: it is the one with a consequence. The tooltip says
what changed, and says so when nobody has ever run `make drift` rather than
reading that as clean. The commits-past-the-tag count is on `/state` for
`./wd status` and deliberately not on the chip. When a release is waiting, a
block under the rig strip lists each one with its summary and the line
`make update`, to be run on the host.

### Why a pull is not enough

`git pull` leaves the gateway running exactly what it was running. Projects are
files the gateway only picks up on a **scan**, and gateway **themes** are config
resources a running gateway does not recompile at all. So `self-update.sh`
pulls and then applies — and applies only what is safe to apply while somebody
might be watching a demo.

| what changed | what happens |
|---|---|
| `ignition/projects/` | `make deploy-all` — never restarts a gateway, safe with sessions open |
| `ignition/themes/` | installed, then **reported** if a gateway is still serving the old CSS |
| `stacks/` | **not applied** — recreating a stack stops containers; the stacks are named, gateways separated from the rest |
| `scripts/` | nothing to do; in effect from the next run |

### Three rules it will not break

1. **It never restarts a gateway.** A changed theme genuinely needs one to
   recompile — so it installs the theme, notices it is not live, and says so.
   A demo interrupted by an automatic restart is a worse outcome than a theme
   one release behind.
2. **It never applies a tree that does not validate.** A machine with nobody at
   the console is exactly where a broken deploy goes unnoticed, so `validate`
   gates the apply. If it fails, the repo moves and the gateway does not.
3. **It never merges.** `--ff-only`, and it stops if the tree is dirty or has
   local commits. Somebody was working there; quietly rebasing over that is
   not a script's decision.

It also takes a `flock` so a timer firing over a running update cannot put two
`docker cp` streams into the same gateway.

## The credential: a deploy key, not your account

Both halves need a credential sitting on that machine to read a **private**
repo. The right one is a **deploy key** — an SSH key that grants read-only
access to *this repo alone*:

```bash
# on the VM, as the user that will run the update
ssh-keygen -t ed25519 -C "wd-vm $(hostname)" -f ~/.ssh/wd_deploy -N ""
cat ~/.ssh/wd_deploy.pub
```

Add that public key at **repo → Settings → Deploy keys → Add deploy key**.
Leave *Allow write access* **unticked**. Then point the machine's clone at SSH
and tell ssh which key to use:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/wd_deploy
  IdentitiesOnly yes
EOF
git -C ~/Ignition-Demos-Stack remote set-url origin git@github.com:Gaskony-Ignition/demo-docker-stack.git
ssh -T git@github.com          # expect: "...successfully authenticated..."
```

**Why not the `gh` login `blank-vm.sh` already did?** It works, and it is the
wrong tool for a machine left alone: that token is *your account* — every repo
you can reach, write included — it can expire, and revoking it to protect the
VM signs you out everywhere else. A deploy key is read-only, scoped to one
repo, has no expiry, and revoking it affects that one machine.

**Why not a GitHub Action?** CI cannot reach a VM behind your network — it would
have to connect *in*, which means opening a port and giving GitHub a credential
to your machine. The pull model needs no inbound anything. (A self-hosted runner
inverts that, but it is a bigger thing to look after than a timer.)

## The other arrangement: applying it unattended

For a machine nobody logs into, run `self-update.sh` itself from a timer and
skip the notice entirely.

```ini
# /etc/systemd/system/wd-update.service
[Unit]
Description=Update the Ignition-Demos-Stack demo stack
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=<your-user>
WorkingDirectory=/home/<your-user>/Ignition-Demos-Stack
ExecStart=/home/<your-user>/Ignition-Demos-Stack/scripts/self-update.sh
```

```ini
# /etc/systemd/system/wd-update.timer
[Unit]
Description=Check for Ignition-Demos-Stack updates

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now wd-update.timer
journalctl -u wd-update.service -n 50      # what it did last time
```

**03:30 rather than hourly, on purpose.** The failure this guards against is a
machine drifting a month behind, not one drifting a day behind — and every run
that finds something deploys projects to a live gateway. Nightly is frequent
enough and puts the work where nobody is presenting.

**Do not enable both timers.** They would not corrupt anything — `self-update.sh`
holds a `flock` and `update-check.sh` only reads — but a banner offering you an
update that a timer already took is a notice that trains you to ignore notices.

**They run as your user, not root.** The user must be in the `docker` group and
own the checkout — see the note in `PORTABLE.md` about not mixing root and user
ownership of this repo.

## What it deliberately leaves for a human

- **A gateway restart**, when a changed theme needs recompiling. The closing
  line says so; pick your moment and `docker restart ignition`.
- **A change under `stacks/`.** Recreating a stack stops containers, which is a
  restart by another name. It **names the stacks** and splits them, because
  "stacks/ changed — take everything down" is true and nearly useless: a stack
  carrying no gateway (`ignition-ha`, `npm`, `postgres`…) can be restarted alone at
  any time and no demonstration notices, while a gateway stack is a container
  someone may be presenting from. Only the second kind needs a quiet moment.
  The line prints `./wd restart STACK=<name>` for each.
- **Anything that failed validate.** The line naming the failure is in the
  journal, and the gateway is still serving the last good tree.
