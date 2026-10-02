# Deploying a project and verifying it landed

`make deploy PROJECT=X` validates, stages, fixes resource signatures, copies
the files to the gateway and calls `make scan`. `make scan` on its own is only
for after editing files inside the container directly, which you should not
normally do.

## How the scan works

The `Ops` project carries a gateway timer (`ignition/timer/AutoScan`) that
polls for a trigger file every 5s and calls `system.project.requestScan()`.
`make scan` drops that file and waits for the log to acknowledge it — about 6s
end to end. `Ops` cannot scan itself into existence, which is why
`bootstrap.sh` restarts once.

8.3.8 also exposes `POST /data/api/v1/scan/projects` (and `.../scan/config` for
config resources such as themes) — listed in the gateway's own `/openapi.json`
— which answers with the scan's own result. `scripts/sparkplug-setup.sh` uses
it to apply an isolated edge's first project with no restart and no `Ops`.

## Three things silently break a deploy

- **A stale `lastModificationSignature`.** The gateway computes it over the
  resource's bytes; if it no longer matches, the scan *skips that resource
  with no error*. `ign-deploy.sh` strips it in a staging copy, and
  `make normalise` keeps it out of git — `make validate` fails if one is
  committed.
- **Root-owned files.** `docker cp` writes as root and the gateway runs as a
  non-root user, which then cannot rewrite them. `ign-deploy.sh` chowns after
  every copy.
- **No scan.** External file edits never auto-apply.

## A scan applies the files; an open session can still show the old view

**Value changes reach an open session; a change to a view's structure need
not.** `session.custom.style` is a *bound* value on a poll, so an open theming
session re-themes within seconds. Nothing about that promises a session will
pick up a component that did not exist when it connected — the gateway had the
new component on disk, the scan had run, and the browser kept showing the
previous one until a hard reload. It is indistinguishable from a deploy that
did not land, and it sends you to look at signatures and view JSON everywhere
except the browser.

Two things follow:

- **Verify a structural change in a fresh browser.** `make shot` opens one by
  construction, which is why it can measure correctly on one machine and look
  broken in a session that has been open since before the change. What it
  shows is what a new session gets.
- **Ask the gateway what it has before touching the deploy** — one grep for
  the new code inside the container separates "the browser is stale" from
  "the deploy really did skip it".

## Gateway timer scripts (8.3, file-based)

`<project>/ignition/timer/<Name>/{handleTimerEvent.py, resource.json}`.
**`handleTimerEvent.py` must be tab-indented** — Ignition wraps it as the body
of `def handleTimerEvent():`, so unindented code raises
`PySyntaxError ... expecting INDENT` on every tick. `resource.json` attributes:
`sharedThread`, `delay` (ms), `fixedDelay`, `enabled`. Scripting is Jython 2.7
— `except Exception, e:`, not `as e`.

## Verifying a view

```
make shot PROJECT=Site1 PAGE=demo          # -> .shots/ignition-Site1-demo.png
make shot GATEWAY=ignition-edge1 PROJECT=Edge PAGE=demo
make shot PROJECT=GatewayAdmin PAGE= TAB=Redundancy
```

`TAB` selects a tab on the demo console's single page by the button's
**label**, the way a customer reads it (`TAB="Store & Forward"`), because the
console renders each tab's button label into a child element rather than the
button's own text — a selector matching the button's own text matches nothing.
`CLICK=` takes any Playwright selector for anything else.

`make shot` is read-only against the gateway, headless, and always opens a
fresh browser context, which is the point — an already-open session can keep
showing a view's previous structure. Site pages need the `demo` route: `/` is
the launcher and does not read session styling.

It reports more than the file it wrote, because three failures all look like
"the deploy is broken" and are not: the **Trial Expired** splash (the gateways
are unlicensed and run a rolling trial — see [TRIALS.md](TRIALS.md)), a red
component **ERROR box** (a Perspective script transform that raises renders
one and logs nothing anywhere a script can see), and nothing rendered at all.
It exits non-zero on each and names the actual repair.
