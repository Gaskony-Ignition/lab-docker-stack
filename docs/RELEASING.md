# Releasing, and taking a release

One version number for the whole stack. Not one per demonstration — the console
is a single Ignition project, a demo is not self-contained, and two demos on
different versions is a rig nobody can reason about. One number, one identity.

```bash
make version                    which release this checkout is
make release BUMP=patch         cut the next one
make update                     on another machine: take the newest
```

## What a release is

**The repo at a tag.** That is the whole of it. There is no tarball, no `.modl`,
no image: everything needed to build the stack is either committed or fetched by
a committed script — `scripts/get-modules.sh` for the third-party modules,
`scripts/make-env.sh` for the environment and its generated passwords.

So a release contains:

| | |
|---|---|
| the Compose stacks | every gateway, the front door, Postgres, the control plane |
| the Perspective projects | `ignition/projects/`, `ignition/edge-projects/` |
| the gateway themes | `ignition/themes/`, generated and committed |
| every script | including the ones that configure gateway state |
| `migrations/` | the machine-side steps this release needs (below) |
| `VERSION`, `CHANGELOG.md` | which release, and what is in it |
| the docs | they travel with the change that makes them stale |

And deliberately **not**:

| | why |
|---|---|
| binary artefacts on the GitHub release | the repo at the tag is the deliverable; an asset is a second copy of the truth, and the one that goes stale |
| the third-party `.modl` files | licensed vendor binaries, not ours to redistribute. `scripts/get-modules.sh` fetches them by hash |
| `.secrets.env`, `stacks/*/.env` | generated per machine; a password in a release is a password on every machine |
| the local CA and its leaf certificates | machine-local on purpose — sharing it means sharing the ability to mint certificates every machine that trusts it would accept |
| `.gwbk` backups | they carry the gateway's own credentials |
| anything under `.wd-local/`, `.snapshots/` | per-machine state, not repo content |

## Cutting one

```bash
make release BUMP=patch          1.2.0 -> 1.2.1
make release BUMP=minor          1.2.0 -> 1.3.0
make release BUMP=major          1.2.0 -> 2.0.0
make release VERSION=1.3.0       say it outright
make release BUMP=minor CHECK=1  every gate, nothing written
```

Host only, like `make update`: the push needs your ssh key and the GitHub
release needs `gh`. The gates it runs go back through `./wd` on their own.

**The gates are the point.** A tag that does not validate is worse than no tag,
because a machine will take it precisely *because* it is a tag. So it refuses
unless:

- the tree is clean, and on `main`
- `main` here is level with `origin/main`
- `./wd validate` reports **0 errors**
- `./wd verify-demos` **passes** for the demos this machine has started
- the tag does not already exist (except on `HEAD`, resuming an aborted run)

Then it writes `VERSION`, updates `CHANGELOG.md`, stamps the project, commits,
creates an **annotated** tag, pushes `main` and the tag, and opens the GitHub
release with that changelog section.

It is idempotent and safe to abort. A run that dies at the push can be re-run
and carries on from there rather than making a second commit. It will not move a
tag that already exists on another commit: re-pointing a tag other machines may
have taken is not something a script gets to decide.

### The changelog is grouped by demonstration

Not by Added/Fixed/Changed. The reader is somebody deciding whether to take this
onto a machine they are about to present from, and their real question is *"did
the MQTT demo change?"* — which a list sorted by commit type cannot answer
without reading all of it.

Commits are filed by the paths they touch: **MQTT Demo**, **EAM Demo**, **Store
& Forward Demo**, **Redundancy Demo**, **Theming & inheritance**, **Demo
console**, then **The rig** as a fallback and **Other** for anything matching
nothing. A commit touching two demonstrations appears under both, on purpose.
The mapping is one commented table in `scripts/release.sh`; add a demo, add a
row.

### The version stamp

House rule: a released Ignition project carries its version in the **Title** and
at the **end of the Description**, so any gateway or Designer shows at a glance
which release it is running. Project *names* never change.

**Only `GatewayAdmin` is stamped.** Its title becomes `Gateway Admin <ver>` and
its description ends `· v<ver>`.

- **Site1 / Site2** are pushed to the edges by EAM. A version in their title
  would claim, on an edge, whatever the *hub* last released — whether or not
  that push has happened. A number that is wrong exactly when somebody is
  looking at it is worse than no number.
- **Themes** is generated wholesale by `scripts/gen-themes.py`, which deletes
  its output directory before writing. A stamp there would be gone at the next
  generation.
- **Ops** is platform plumbing nobody opens.

Re-stamping replaces rather than accumulates, so the third release does not read
`Gateway Admin v1.0.0 v1.1.0 v1.2.0`.

## Taking a release on another machine

```bash
make update                  go to the newest release and apply it
make update CHECK=1          say what would happen, change nothing
make update VERSION=v1.2.0   a named release, forwards or back
make update TRACK=main       follow main commit by commit (a dev machine)
make update-check            is a newer release waiting? looks, changes nothing
```

`make update` does, in this order:

1. **fetch, with tags** — the tags *are* the releases
2. **move** to the newest tag, on a detached HEAD. "On release v1.2.0" is not a
   branch, and resetting `main` onto the tag would destroy that branch's meaning
   for any machine that later wants to follow it
3. **validate** — and stop if it fails, leaving the gateway on the last good tree
4. **run the pending migrations** (below)
5. **apply** what changed: themes installed, projects deployed, and every
   gateway setting bootstrap creates re-applied to the running gateways
   (`scripts/converge.sh`, which writes only what differs). It never restarts
   a gateway
6. **verify-demos** — the only step that asks whether the machine can still do
   its job

**A demo start applies the settings too**, to the gateways it brings up, once
per release: a machine built before a setting existed gets it without anyone
typing a fix. The first start after an update takes a few minutes longer;
later starts skip it (`.wd-local/converged/`). `make converge` runs it by hand.

Nothing auto-updates. The timer in [SELF-UPDATE.md](SELF-UPDATE.md) only
*looks*; taking the update is a command somebody runs.

## Migrations: the machine-side half

`git checkout v1.2.0` moves every file, and **files are not the whole of a
release**. The `wd-mqtt` network does not exist because a compose file mentions
it. A container created last week is not on a network added this week. A
generated token is not in git by design. A module lives in a gateway's Docker
volume, a trigger lives in Postgres, and a retired stack leaves behind a
container nothing can see.

So `migrations/` holds one small script per release that needs machine-side
work, and `scripts/migrate.sh` applies what is pending, in order.

```bash
make migrate              apply everything pending
make migrate CHECK=1      what would happen, change nothing
make migrate-list         what has run here, and what is waiting
scripts/migrate.sh --baseline   record all as applied without running them
scripts/migrate.sh --force      re-run everything (they are all idempotent)
```

**The rule for writing one.** Named `<version>-<NN>-<slug>.sh`, sorted by
version then by the rest, so `NN` orders migrations within a release where one
must come before another. Three things it must do:

1. **Be safe to run twice.** It checks its own end state first. A timer will run
   it; a migration only correct the first time breaks the machine on the second.
2. **Be safe on a machine that never had the old state.** A fresh bootstrap
   builds every one of these end states from nothing, so a migration must say
   "already right" quietly rather than repairing something that was never wrong.
3. **Never restart a gateway behind your back.** A step that genuinely needs one
   **defers**: it prints the command, stays pending, and is offered again. A
   deferral is not a failure and does not fail the update.

Progress is recorded **per file** in `.migrations-applied`, beside
`.update-applied`, and gitignored for the same hard reason: self-update refuses
to run on a dirty tree, so a tracked file a timer rewrites would stop every
update. Per file rather than a single "applied up to 1.2.0" marker, because a
version marker fails the first time a migration is *backfilled* — which is
exactly how this scheme started.

`./wd bootstrap` calls `--baseline`: it has just built everything from scratch,
so every migration is satisfied and proving it would cost a minute.

## Going back

**`make update VERSION=v1.2.0` works in both directions.** Going back is
reported as `GOING BACK`, because it is a different sentence from going forward:
**the files go back and machine state does not come with them.**

There is no `--down` and there will not be one. A reverse migration is a second
body of code exercised approximately never, and therefore wrong when it finally
runs. Instead each migration declares what it cannot undo, in its own header, as
a `# ONE-WAY:` line — next to the code that caused it rather than in a table
that goes stale — and `scripts/migrate.sh` reads those and names them before it
does anything.

For the v1.0.0 set, two are one-way:

| migration | what does not come back |
|---|---|
| `1.0.0-04-distributor-tls` | the Distributor module stays installed, the hub pair stays a TLS-only broker on 8883, and Engine and every MQTT edge stay pointed at it. Re-point them with `make mqtt-setup` against whatever the older release wants |
| `1.0.0-06-emqx-retired` | the `emqx` container and its three volumes are gone, and this filesystem TRIMs deleted blocks. An older release gets its `stacks/emqx` folder back from the checkout, but the broker is empty: `make certs` and `make up STACK=emqx` build it again from nothing |

From v1.1.0, `1.1.0-02-sf-quarantine-release` is one-way too: on a machine whose
hub had quarantined store-and-forward batches (from before the Postgres history
guard), it releases them for forwarding. That needs the hub stopped, so it
defers like any other restart: `make update` prints `SF_RELEASE=1
scripts/migrate.sh` and stays pending until someone runs it. It keeps a
`.pre-release-<date>` copy of the file beside it, and does nothing on a machine
with no quarantined rows. Tested 23/09/2026 on a copy of this rig's file with 4
synthetic quarantined batches (released, 0 left, copy intact, owner kept); this
rig's own file had none.

From v1.1.1, `1.1.1-01-ignition-image` is one-way: the gateways move to Ignition
8.3.9, and a volume that has started on 8.3.9 cannot be read by 8.3.8. The migration
itself only names the running gateways still on the old image and defers with
`./wd restart STACK=<gw>` for each; a stopped gateway takes the new image on its next
start. Going back to v1.1.0 after that needs `scripts/ign-version.sh 8.3.9` so the
older tree keeps starting 8.3.9 ([IGNITION-VERSION.md](IGNITION-VERSION.md)).

The other four v1.0.0 migrations are additive — a network that exists but is unused, a generated
secret, a rebuilt image, some Postgres triggers — and cost an older release
nothing.

## Freezing

**Staying on your current tag *is* the frozen state.** A machine on
`v1.2.0` sits on a detached HEAD there and moves only when somebody runs
`make update`. Nothing in this repo moves it on its own. That is the freeze, and
it needs no branch, no flag and no ceremony.

A **long-lived freeze that still needs fixes** — a customer on `v1.2.x` who
cannot take `v1.3.0` — is a `release/1.2` branch cut from the tag, with fixes
cherry-picked onto it and patch releases cut from it. That is **deliberately
rare**: it is a second line of history to keep green, two changelogs, and every
migration has to be correct on both. Do it when a real customer commitment needs
it, and not to avoid a decision about whether a change is ready.

## Settings a machine is allowed to keep

An engineer is meant to open these gateways and change things — that is half of
what the MQTT demo is for. The setup scripts then write those settings back,
because writing only the wanted value is what makes them idempotent. **So by
default a hand change lasts until the next setup run, and that is correct:** the
rig has to be able to return to a known state.

`make drift` says what currently differs, and `make snapshot` keeps a copy
before an update overwrites it.

For a few values that a machine might reasonably keep for good, the script does
not hold the value at all — it asks `.demo-settings.env`, which is gitignored, so
an update cannot reach it. Copy the committed template and edit it:

```bash
cp demo-settings.env.example .demo-settings.env
```

**Changing these survives an update. Anything else you change in a gateway does
not.**

| knob | what it sets | written by |
|---|---|---|
| `KEEPALIVE` | seconds between MQTT keepalive pings, on every Transmission and Engine server | `scripts/ign-mqtt.sh` |
| `ROLLING_MAX_AGE` | seconds each edge's Rolling History Buffer holds history while the broker is unreachable | `scripts/sf-arm.sh` |
| `SPARKPLUG_CONVERT_UDTS` | publish UDT members as flat metrics instead of a UDT instance | `scripts/sparkplug-setup.sh` |
| `SPARKPLUG_PUBLISH_UDT_DEFS` | publish UDT definitions in the birth certificate | `scripts/sparkplug-setup.sh` |
| `SPARKPLUG_DISPLAY_PATH_TYPE` | which path MQTT Engine puts in an alarm's display path | `scripts/sparkplug-setup.sh` |
| `WD_MQTT_CUT_SECONDS` | how long a broker cut lasts when the caller does not say | `control/mqttcut.py` |

Precedence is the ambient environment (`KEEPALIVE=20 ./wd mqtt-setup` for a
one-off), then `.demo-settings.env`, then the default in the script. An
explicitly **empty** value is a real answer for the three Sparkplug knobs: it
means "leave whatever the gateway has alone", which is how to hand a gateway to
an engineer to experiment with and have the scripts stop arguing.

**Not a knob:** tag pacing. How fast the demo's tags change is `"delay": 1000`
in a committed timer resource (`ignition/edge-projects/SparkplugEdge/.../timer/
Simulate/resource.json` and the two `EdgeDataStream` timers), deployed verbatim
— so a Designer change to it *is* overwritten. Making it a knob would mean a
setup script rewriting a deployed resource's attributes, which nothing here does
and which `docs/DEPLOY.md` names as a hazard. Transmission's own
`tagPacingPeriod` is the module's shipped default and no script touches it, so a
hand change there already survives.
