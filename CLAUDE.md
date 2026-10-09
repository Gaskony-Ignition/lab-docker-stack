# CLAUDE.md — Ignition-Demos-Stack

A hub gateway (a redundant pair, which is also the MQTT broker -- Cirrus
Link Distributor), Edge spokes, Postgres and the Perspective projects that run
on them — built to show EAM, project
inheritance, MQTT over TLS, store and forward, and redundancy.

**Where changes are made.** Changes are made and tested here and pushed to
GitHub; the machines that run the stack take releases with `make update`.
Nothing Claude-specific needs to run on those machines.

## Hard rules

1. **Restarting a gateway here is allowed** — it is a test rig whose purpose
   includes showing failure modes, and the redundancy demo restarts a gateway
   from a button. Prefer `make scan` regardless: it applies changes in
   seconds without dropping a session. Say before you restart and why —
   someone may be mid-demonstration. Never extend this to a customer's
   gateway or to the work machine mid-demo.
2. **Never print a password or token**, and never put one in an
   AskUserQuestion option. A library will do it for you if you are not
   careful — `ign-gw.js` filters every `console.*` through `scrub()`; do the
   same in anything new that touches a credential.
3. **Real secrets never enter git.** Committed `.env.example` files carry
   variable names only; `scripts/make-env.sh` fills them in locally.
   `make check-secrets` is the gate and runs in CI.
4. **The gateway is the source of truth for anything not file-based** — EAM
   registration, Gateway Network pairing, database connections, MQTT module
   config. The repo owns project *resources* only.
5. **Look it up, don't guess.** Ignition's file formats and APIs are
   version-specific; check `docs/` and the reference workspaces below before
   guessing.
6. **Self-verify before reporting done.** After any view change: deploy,
   scan, then screenshot it (`make shot`) and look at the image.
7. Australian English, ISO or DD/MM/YYYY dates, 24-hour time.

## Layout

```
wd, wd.cmd        front door on every platform -- `./wd <make target>`
tools/toolbox/    the image every command runs inside
stacks/           one Compose project per folder; every stack carries a
                  stack.meta manifest that every list derives from
ignition/projects/  the Perspective projects, as deployable resource trees
ignition/themes/  ten gateway theme config resources -- generated, never
                  hand-edited, installed by scripts/ign-themes.sh
scripts/          everything is driven from here; the Makefile is what wd calls
modules/          third-party .modl files (gitignored -- fetched by get-modules.sh)
docs/             reference for every demo and mechanism -- see README's
                  documentation table for the index
```

## Never hand-edit generated output

`ignition/themes/` and `ignition/projects/Themes/` are generated wholesale by
`scripts/gen-themes.py`, which deletes each output directory before writing —
an edit is gone without warning. The picker, the chart colour maps and
`demo_styles.THEMES` are likewise generated from `ignition/themes/themes.json`,
and `make validate` cross-checks all of it.

## The work loop

```
make status                      what is running
make deploy PROJECT=Site1        validate, stage, fix signatures, copy, scan
make scan                        apply files already on the gateway
make pull-project PROJECT=X      bring Designer changes back into the repo
make validate                    what CI will check
make shot PROJECT=Site1          screenshot a page and confirm it rendered
make verify-demos                is the stack ready to demonstrate?
make gwbk                        take a .gwbk from every gateway
make update-check / make update  is another machine behind? take it
```

`make deploy` calls `make scan` for you. See [docs/DEPLOY.md](docs/DEPLOY.md)
for what silently breaks a scan, [docs/GATEWAY-UI.md](docs/GATEWAY-UI.md) for
driving a gateway UI headlessly, and [docs/TRIALS.md](docs/TRIALS.md) for the
trial mechanics: gateways lapse every two hours and a person resets them (Trials page, console EAM tab, or `make trial-reset`).

`make validate` and `make verify-demos` answer different questions. Validate
reads the repo and is what CI runs. `verify-demos` reads the *running*
gateways and asks whether each demonstration would work right now; it is
read-only, names the repair for every failure, and is what to run before a
customer meeting.

`docker volume prune` is denied in `.claude/settings.json`: prune's "unused"
includes every gateway once `make down` removes its container, which is this
stack's normal finished state, and pruning would take commissioning, Gateway
Network pairing and EAM registration with it. `docker volume rm` is on the
**ask** list instead, since it names its target.

## Demonstrations

Full mechanism for each lives in its own doc, linked from the README. Two
things that apply to all of them: docs travel with the change that makes them
stale, in the same commit; and a Perspective script transform that raises
renders a red ERROR box and logs nothing anywhere a script can see, which is
why every demo script that can plausibly fail catches for itself.

## Writing scripts here

`scripts/lib.sh` sets `set -euo pipefail`. The recurring cost: **a command
whose "not found" is a normal answer, used inside a pipeline, kills the whole
script** at the point where it had just done its job — `make status` printing
its header and exiting 1 on a stack that is simply down is the classic shape.
So: end a pipeline whose failure is a legitimate outcome with `|| true` (on the
whole pipeline, not one stage of it, since `pipefail` makes any stage's
failure the pipeline's); never `grep -q` where the pipeline's exit status is
read (an early exit SIGPIPEs upstream and returns 141) — use `grep -c` and test
the count; and judge a step by its end state, not its exit code.

## Conventions

- **Every Perspective project hides the app bar** — `session-props` sets
  `props.appBar.togglePosition = "hidden"`. A child project's `session-props`
  replaces the parent's wholesale rather than merging, so every project needs
  this individually; `make validate` enforces it and `make new-site`
  scaffolds it.
- One-shot scripts stay out of git — run them from the scratchpad. Anything
  worth keeping lives in `scripts/` with a header explaining why.

## Reference workspaces (read-only)

- `plugins/ignition/knowledge/` in the toolkit checkout (IGNITION_TOOLKIT) — the
  accumulated Ignition reference: component quirks, binding gotchas, timer
  scripts, MQTT Engine, Postgres. Propose a new gotcha here via the
  `ignition:learn` skill rather than duplicating it in this repo.

Read from them freely; do not write to either.
