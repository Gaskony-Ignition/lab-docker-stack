# Choosing the Ignition build

```bash
make ignition-version                 what each gateway declares, and what it RUNS
make ignition-version SET=8.3.10      switch all four
make ignition-version SET=default     drop the override, back to the repo's
./wd down && ./wd up                  take it -- nothing changes until then
```

Only the four Ignition gateways. Postgres, NPM and HAProxy are pinned in
their compose files and change with a commit, because nobody wants to
demonstrate a specific proxy or database build.

## Where the version lives

```text
stacks/<gw>/compose.yaml   image: inductiveautomation/ignition:${IGNITION_VERSION:-8.3.9}
                                                                                ^^^^^^^^
                           the repo's DECLARED version. Committed. What CI reads.

.wd-local/ignition-version 8.3.10
                           a per-machine OVERRIDE. Gitignored. What this box runs.
                           `stack.sh` exports it for every compose call.
```

Trying a build on one machine should not commit the repo to it; adopting a
version everywhere is the separate, deliberate act of changing the compose
defaults in a commit.

**ONE file, not one `.env` entry per gateway**, for two reasons. `wd-control`
mounts the repo **read-only** and holds the Docker socket, so the demo console
could not record a version in a stack's `.env` without opening the whole repo
to a container that is unauthenticated on `backbone` -- `.wd-local` is the one
narrow writable mount that avoids that. And four files that must agree can
disagree; one cannot, so for an override the mismatch state is impossible by
construction. (It is still possible for the committed compose *defaults*,
which is what `make validate` checks.)

The export is the same shell-beats-`.env` precedence that once made `TZ`
overwrite nine `.env` files by accident. The difference is the name:
`IGNITION_VERSION` is prefixed and referenced by exactly four compose files,
where `TZ` was an ordinary word that already meant something to glibc and the
JVM. Keep new names prefixed.

**Two scripts read that default** — `make validate` and
`scripts/watch-upstream.sh`, which selects module releases *for the Ignition we
run*. Keep the tag in the `${IGNITION_VERSION:-<version>}` shape or both stop
being able to read it; validate fails on a tag it cannot parse rather than
skipping, because the older code skipped and reported success.

**All four move together.** A redundant pair on two versions does not sync —
the backup reports `Incompatible` and restarts itself trying — and an EAM Send
Project to an edge on another minor is a gamble. There is no case for the hub
and an edge disagreeing, so the tool cannot express one and `make validate`
fails a set that does.

## A patch is cheap; a minor is not

**Inside a series — 8.3.8 → 8.3.9 — there is no module work at all.** A
module's ABI is per Ignition *minor*, which is why `modules.manifest` names a
version, and the check compares the series.

**Across a minor — 8.3 → 8.4, or a future calendar release — the modules must
move first**, and this is the failure worth knowing about because it is
completely silent: a module built for another minor does not error, the gateway
logs a mismatch and carries on without it. The Architecture tab and Site 2's
Sparkplug road are simply not there, and everything else looks perfect. So
`ign-version.sh` refuses a series change until `modules.manifest` is pinned to
that series:

```bash
make check-upstream       # has a build for the new series been published?
make upgrade-modules      # fetch it, re-hash it, rewrite the manifest + pins
```

## Forward only

**A gateway upgrades its configuration store the first time a newer build
starts against a volume, and the older build cannot read it back.** Everything
this repo deliberately does not own lives in those volumes — commissioning,
Gateway Network pairing, EAM registration, the database connection, MQTT
config, the redundancy pair, the security zone.

So a downgrade is not a version change, it is a rebuild. The way that hurts is
specific: **nothing complains at switch time.** It complains when a gateway will
not start, which is ten minutes before you show somebody something. The guard
therefore refuses at the moment it is still cheap to reconsider.

It refuses against **what the volumes have actually run**, not what the repo
declares — different numbers the moment anyone has experimented. That comes from
`docker inspect` on the container, which answers even when it is stopped; once
`make down` has *removed* the containers there is nothing left to ask, so every
observation is recorded in the gitignored `.ignition-version-seen`. Asking for a
version is never recorded — only starting one is. Recording the *intent* instead
would poison the guard against a version that does not exist: the record would
claim the volumes had run it, and every real version would then be refused as a
downgrade.

If you mean it:

```bash
./wd gwbk                                    # back it up while it still starts
scripts/ign-version.sh 8.3.7 --force         # clears the record, not the floor
./wd destroy-all && ./wd bootstrap           # 15-20 min, and a new CA
```

`--force` lifts **only** the downgrade guard. The floor and the module check
are not opinions about your data — they are statements about what will run.

## What is checked before anything is written

| | why |
|---|---|
| the version parses | a typo becomes a pull failure, not a silent default |
| series ≥ 8.3 | 8.1 is a different Perspective, resource format and module ABI |
| modules match the series | a mismatched module is skipped **silently** |
| the tag exists **for this architecture** | an amd64-only tag pulls on arm64 and then will not run |
| not a downgrade | see above |

The tag check **proves its own instrument**: a lookup can fail because the tag
is wrong or because Docker Hub is unreachable, and those need opposite answers.
So it asks the same question about the version we already run — if that
resolves, the registry is fine and the tag is simply wrong, which is worth
refusing rather than discovering on the pull.

## A future calendar release

The floor comparison is numeric per component, so a `2027.x` tag reads as newer
than `8.3` rather than older and nothing blocks it — verified in all three
places that compare series (`validate.py`, `watch-upstream.sh`,
`ign-version.sh`), which have to agree or the floor means different things
depending on who asks.

What will actually block it is modules, correctly: a new series needs an
upstream channel to exist for it. `watch-upstream.sh` builds Embr's channel
filter from the series we run (`releases/%IGN%/*`), so it adapts on its own —
but it will report `none-for-2027.1` until upstream publishes one, and that is
the honest answer rather than a silent 8.3 module on a 2027 gateway.

## From the Demos page

The rig strip's version chip is a button. It opens a picker that **offers only
what would be accepted** -- the module series, and nothing older than these
volumes have run -- because a picker that can offer something the script will
refuse teaches people the buttons lie. The script still checks all of it; the
filter keeps the offer honest, the guards keep it safe. `POST
/ignition-version/<v>` refuses anything not on that list, so the endpoint is no
wider than the button.

**Applying recreates only the gateways that are UP.** The version is recorded
for all four; a stopped gateway -- which on this rig is most of them, most of
the time -- picks it up when it next starts. Bringing the edges and the backup
up to change a number on them would cost several GB and begin demonstrations
nobody asked for.

**The hub is one of them, so the page goes away for about a minute.** That is
survivable for one reason: the job runs in `wd-control`, a different container,
so its log outlives the gateway going away and the session reads the outcome
when it reconnects. It is the same reason the redundancy button *schedules* its
restart rather than performing it -- there, the script doing the restarting was
the process writing the response, and the click reported failure for something
that worked.

**The backup age is shown, not enforced.** Taking a `.gwbk` writes to the repo,
which `wd-control` mounts read-only on purpose, so gating on something the page
cannot itself perform would only send you to the terminal you were avoiding.
"no backup on this machine" beside a forward-only switch is the sentence that
makes somebody stop and think; `./wd gwbk` is the answer.
