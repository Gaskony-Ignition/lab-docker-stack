# Ignition-Demos-Stack -- Ignition demonstration stack
#
# Everything in this repo is driven from here. If you find yourself typing a
# docker or python command by hand more than once, it belongs in this file.
#
#   make            list the targets
#   make bootstrap  first run on a new machine, start to finish

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Which gateway a project command talks to. The hub is the default because it
# is where projects are authored; edges receive projects via EAM, not via a
# direct deploy.
GATEWAY ?= ignition

# `?=` still DEFINES the variable, so `$(if $(GATEWAY),...)` can never take its
# else-branch -- a target wanting "all gateways unless one was named" has to ask
# where the value came from instead of whether it is empty. `origin` is `file`
# for the default above and `command line` / `environment` for a real override.
# trial-reset got this wrong and silently reset only the hub for weeks, which is
# why the redundant backup sat expired: the one command meant to fix it never
# looked at it. Use GATEWAY_OR_ALL for anything that should sweep by default.
GATEWAY_OR_ALL = $(if $(filter-out file default,$(origin GATEWAY)),$(GATEWAY),--all)
PROJECT ?=
STACK   ?=

.PHONY: help bootstrap env up down restart status ps logs images \
        validate normalise check-secrets certs themes \
        deploy deploy-all pull-project projects scan new-site \
        core demos demo-start demo-stop demos-stop \
        proxy-hosts prune-names trust-ca signin secrets secrets-list designer-idp redundancy redundancy-status redundancy-address public-address edge-visual converge redundancy-failover redundancy-fail \
        hosts \
        destroy-all \
        trial trial-reset shot layout-check modules modules-list \
        sparkplug-setup sparkplug-deploy mqtt-reset mqtt-udt-rollout mqtt-udt-check \
        hooks clean-backups update update-check ignition-version \
        version release migrate migrate-list snapshot snapshot-list drift drift-show

# ---------------------------------------------------------------- meta ------

help:  ## show this help
	@echo "Ignition-Demos-Stack -- Ignition demonstration stack"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Variables:  GATEWAY=$(GATEWAY)  PROJECT=<name>  STACK=<name>"
	@echo
	@echo "Examples:"
	@echo "  make up                             start every stack, in order"
	@echo "  make deploy PROJECT=Site1           deploy one project to the hub"
	@echo "  make logs STACK=ignition            follow one stack's logs"
	@echo "  make new-site NAME=Site3 STYLE=crm-cloud"
	@echo
	@echo "Sessions showing \"Trial Expired\"?  https://console.test/_wd/trials  (or make trial-reset)"

# ------------------------------------------------------------ lifecycle -----

bootstrap:  ## first run on a new machine: env, certs, network, start everything
	@scripts/bootstrap.sh

env:  ## create stacks/*/.env from the committed .env.example templates
	@scripts/make-env.sh

certs:  ## (re)generate the local CA and the *.test leaf
	@bash stacks/npm/create-certs.sh

up:  ## start stacks in dependency order (STACK=<name> for just one)
	@scripts/stack.sh up $(STACK)

down:  ## stop containers -- data volumes survive
	@scripts/stack.sh down $(STACK)

restart:  ## down then up
	@scripts/stack.sh restart $(STACK)

status:  ## health and URL for every service
	@scripts/stack.sh status

ps:  ## docker ps, narrowed to this stack
	@scripts/stack.sh ps

logs:  ## follow one stack's logs -- requires STACK=<name>
	@test -n "$(STACK)" || { echo "usage: make logs STACK=ignition"; exit 1; }
	@scripts/stack.sh logs $(STACK)

images:  ## pull newer images for every stack
	@scripts/stack.sh pull $(STACK)

# ----------------------------------------------------- ignition projects ----

projects:  ## list the projects on a gateway and their inheritance
	@scripts/ign-pull.sh --list $(GATEWAY)

deploy:  ## deploy one project to a gateway -- requires PROJECT=<name>
	@test -n "$(PROJECT)" || { echo "usage: make deploy PROJECT=Site1 [GATEWAY=ignition]"; exit 1; }
	@scripts/ign-deploy.sh $(PROJECT) $(GATEWAY)

deploy-all:  ## deploy every project in ignition/projects to a gateway
	@scripts/ign-deploy.sh --all $(GATEWAY)

pull-project:  ## pull a project OUT of a gateway into the repo -- PROJECT=<name>
	@test -n "$(PROJECT)" || { echo "usage: make pull-project PROJECT=Toolbox_Styles [GATEWAY=ignition]"; exit 1; }
	@scripts/ign-pull.sh $(PROJECT) $(GATEWAY)

scan:  ## make deployed files live on a gateway (external edits never auto-apply)
	@scripts/ign-scan.sh $(GATEWAY)

core:  ## start ONLY the core -- front door, hub gateway, demo console
	@scripts/stack.sh up $$(python3 -c "import json;print(' '.join(json.load(open('demos.json'))['core']['stacks']))")

demos:  ## what demos exist, and which are running
	@scripts/wd-demos.sh list

demo-start:  ## start a demo and everything it needs -- DEMO=<id>
	@test -n "$(DEMO)" || { echo "usage: make demo-start DEMO=eam  (make demos to list)"; exit 1; }
	@scripts/wd-demos.sh start $(DEMO)

demo-stop:  ## stop a demo, unless another running demo needs its stacks -- DEMO=<id>
	@test -n "$(DEMO)" || { echo "usage: make demo-stop DEMO=eam"; exit 1; }
	@scripts/wd-demos.sh stop $(DEMO)

demos-stop:  ## stop every demo, leaving the core up
	@scripts/wd-demos.sh stop-all

modules:  ## install third-party .modl files -- prompts, needs a cert wizard after
	@scripts/ign-modules.sh $(GATEWAY)

modules-list:  ## what modules are registered on a gateway
	@scripts/ign-modules.sh --list $(GATEWAY)

sf-setup:  ## set up the store-and-forward demo: historian, broker, both edge roads
	@scripts/sf-historian.sh
	@scripts/pg-history-guard.sh
	@scripts/ign-mqtt.sh setup
	@scripts/sf-arm.sh
	@scripts/sf-gan-history.sh
	@scripts/sf-audit-alarms.sh

# The broker: MQTT Distributor on the hub's redundant pair (docs/MQTT-DISTRIBUTOR.md).
# distributor-setup makes each half a TLS broker with its OWN certificate (the
# backup only when it is running); mqtt-setup points Engine and every
# MQTT-speaking edge at it; pg-history-guard keeps the historian's tables
# duplicate-tolerant on every partition, next month's included.
distributor-setup:  ## make the hub pair's MQTT Distributor a TLS broker (each half's certificate, users)
	@scripts/distributor-setup.sh ignition
	@if docker ps --format '{{.Names}}' | grep -qx ignition-backup; then scripts/distributor-setup.sh ignition-backup; fi

mqtt-setup:  ## point hub Engine and the MQTT edges at the hub pair's Distributor (idempotent)
	@scripts/ign-mqtt.sh setup $(GATEWAY_EXPLICIT)

pg-history-guard:  ## historian tables: skip replayed duplicates, text strings, on every partition
	@scripts/pg-history-guard.sh

# GATEWAY_EXPLICIT, not GATEWAY -- the same `?=` trap that made trial-reset
# silently skip the backup for a day, and it bit twice more here. Both scripts
# default, correctly, to the edges their OWN manifests declare
# (`gateways_where SF_TRANSPORT gan|mqtt`); passing a bare $(GATEWAY) handed
# them `ignition` instead, because line 15 DEFINES it for every target. So
# `make sf-gan-history` -- the repair verify-demos names by name -- died with
# `ignition has no GAN_PROVIDER in its stack.meta`, and `make sf-arm` went off
# to arm the hub, which carries Engine rather than Transmission. Bootstrap was
# unaffected throughout: it calls both scripts directly, with no argument.
sf-arm:  ## (re)arm buffering on the edge transmitters and prove it from the log
	@scripts/sf-arm.sh $(GATEWAY_EXPLICIT)

sf-gan-history:  ## (re)arm the Gateway Network edge's history sync to the hub
	@scripts/sf-gan-history.sh $(GATEWAY_EXPLICIT)

# The Sparkplug alarms demo. GATEWAY_EXPLICIT for the reason given above sf-arm:
# a bare $(GATEWAY) is always `ignition`, and the hub is not an isolated edge.
# Empty means every ROLE=edge-isolated gateway running MQTT_MODULE=transmission.
sparkplug-setup:  ## build the MQTT demo: edge project, token, tags, transmitters, hub Engine
	@scripts/sparkplug-setup.sh $(GATEWAY_EXPLICIT)

sparkplug-deploy:  ## push the isolated edges' Edge project and apply it (no gateway config)
	@scripts/sparkplug-setup.sh --deploy-only $(GATEWAY_EXPLICIT)

# The MQTT demo's second purpose is that an engineer opens the gateways and
# CHANGES configuration (docs/SPARKPLUG.md, "The reference setup"). These two
# are the way back and the one procedure that is an engineer's rather than a
# customer's -- neither belongs on the demo page.
mqtt-reset:  ## put the MQTT demo back to default: the cut, the fault, the UDTs, the settings
	@scripts/mqtt-reset.sh $(if $(QUICK),--quick,)

mqtt-udt-rollout:  ## Fix 2: roll the changed UDT out to every edge and re-birth (VARIANT=, SETTLE=)
	@scripts/mqtt-udt-rollout.sh $(if $(VARIANT),VARIANT=$(VARIANT),) $(if $(SETTLE),SETTLE=$(SETTLE),)

# Read-only, and the one check that catches a UDT divergence the cloud never
# reports: Engine keeps the first definition it learnt and says nothing.
mqtt-udt-check:  ## does each edge's UDT still match what the cloud holds? (exit 1 if not)
	@scripts/mqtt-udt-check.sh

redundancy:  ## pair the hub with its redundant backup (idempotent)
	@scripts/ign-redundancy.sh setup

redundancy-status:  ## what each half of the redundant pair reports
	@scripts/ign-redundancy.sh status

redundancy-address:  ## point both halves at the shared front door (part of `redundancy`)
	@scripts/ign-redundancy.sh public-address

# Every gateway, not just the pair. A containerised gateway that auto-detects
# its address advertises a docker-internal one, and the home page's Launch
# Perspective button then leads somewhere no browser can reach.
public-address:  ## tell EVERY gateway the address a browser should use (auto-detect off)
	@scripts/ign-public-address.sh $(GATEWAY_EXPLICIT)

# An Edge gateway has ONE Panel session and it defaults to Vision. Perspective
# then answers "Sessions Exceeded" on a healthy gateway -- a false negative that
# reads exactly like a licence limit.
edge-visual:  ## give each edge's Panel session to Perspective, not Vision
	@scripts/ign-edge-visual.sh $(GATEWAY_EXPLICIT)

converge:  ## re-apply every gateway setting bootstrap creates, to running gateways (restarts nothing)
	@scripts/converge.sh $(GATEWAY_EXPLICIT)

redundancy-failover:  ## hand responsibility to the other half -- nothing restarts
	@scripts/ign-redundancy.sh failover

redundancy-fail:  ## stop the ACTIVE half for real, then bring it back (SECS=60)
	@scripts/ign-redundancy.sh fail $(if $(SECS),$(SECS),60)

# Rule 6 -- self-verify a view change -- used to name a tool outside this repo,
# in a directory that does not exist on this machine or on anyone else's. A
# mandatory check nobody can run is worse than no check: it reads as done.
shot:  ## screenshot a Perspective page and check it rendered (PROJECT=, PAGE=, GATEWAY=)
	@scripts/ign-shot.sh

layout-check:  ## does a screen FIT, headlessly -- PROJECT=, PAGE=, TAB=, STEPS="a|b", SIZES=
	@scripts/layout-check.sh

hosts:  ## add every .test name to THIS machine's hosts file (runs on the host, may sudo)
	@scripts/hosts-setup.sh

# Host-only, like `update`: the CA has to land in the HOST's trust stores, and
# the toolbox cannot reach those. Re-run it after any from-scratch rebuild --
# the CA is machine-local and regenerated, and every .test name warns until the
# new one is trusted, which looks exactly like the stack being broken.
trust-ca:  ## trust this machine's CA so .test HTTPS gets a padlock (host, sudo)
	@sudo stacks/npm/finish-setup.sh

# Only bootstrap.sh ran this, so adding a TEST_HOST to a manifest meant either
# re-bootstrapping or clicking through the NPM UI. It is idempotent and it
# repoints changed targets, so there is no reason it could not be run directly.
proxy-hosts:  ## (re)create the .test proxy hosts in NPM from the stack manifests
	@bash stacks/npm/create-proxy-hosts.sh $(if $(PRUNE),--prune,)

# Both halves of the same job: the names a BROWSER resolves and the names the
# PROXY answers on. Retiring a stack leaves a name in each, so this reports
# stale entries on every run and removes them only when asked.
prune-names:  ## remove .test names and proxy hosts that no stack.meta declares
	@bash stacks/npm/create-proxy-hosts.sh --prune
	@scripts/hosts-setup.sh --prune

# ------------------------------------------------------ signing in ---------
#
# The console's Open buttons. Two halves that are useless apart: the sign-in
# door on each gateway's proxy host (proxy-hosts), and the Designer setting
# that lets the Designer spend the session the door created.
signin:  ## make the console's Open buttons work: proxy door + Designer setting + secrets
	@bash stacks/npm/create-proxy-hosts.sh
	@bash scripts/ign-secrets.sh
	@bash scripts/ign-designer-idp.sh

secrets:  ## put the non-Ignition UI logins in the gateway's own secret store
	@bash scripts/ign-secrets.sh

secrets-list:  ## what is stored (names only -- never a value)
	@bash scripts/ign-secrets.sh --list

designer-idp:  ## Designer logs in through the IdP, so an open browser session counts
	@bash scripts/ign-designer-idp.sh $(GATEWAY_EXPLICIT)

trial:  ## Perspective trial status for every gateway (unlicensed = 2h rolling)
	@scripts/ign-trial.sh status

trial-reset:  ## reset the 2-hour Perspective trial (GATEWAY=<name>, or all)
	@scripts/ign-trial.sh reset $(GATEWAY_OR_ALL)

new-site:  ## scaffold an inheriting Site project -- NAME=<name> STYLE=<style-pack>
	@test -n "$(NAME)" -a -n "$(STYLE)" || { \
	  echo 'usage: make new-site NAME=Site3 STYLE=crm-cloud [TITLE="Site 3"]'; exit 1; }
	@scripts/new-site.sh $(NAME) $(STYLE) "$(TITLE)"

# -------------------------------------------------------------- quality -----

ignition-version:  ## which Ignition build the gateways run -- SET=<v> to change it
	@scripts/ign-version.sh $(SET)

update-check:  ## is a newer RELEASE waiting? (looks; changes nothing)
	@scripts/update-check.sh

# Follows TAGS by default -- the newest release. TRACK=main keeps the old
# commit-by-commit behaviour for a development machine; VERSION=v1.2.0 goes to a
# named release, forwards or back. docs/RELEASING.md and docs/SELF-UPDATE.md.
update:  ## go to the newest RELEASE and apply it (TRACK=main, VERSION=v1.2.0, CHECK=1)
	@scripts/self-update.sh $(if $(CHECK),--check,) \
	  $(if $(TRACK),--track $(TRACK),) $(if $(VERSION),--version $(VERSION),)

# ------------------------------------------------------------- releases -----

version:  ## which release this checkout is
	@bash -c '. scripts/lib.sh; echo "$$(stack_version)"'

# Host-only: the push needs your ssh key and the GitHub release needs `gh`.
# The gates it runs (validate, verify-demos) go back through ./wd on their own.
release:  ## cut a release: BUMP=patch|minor|major or VERSION=x.y.z (CHECK=1 to gate only, SKIP_A11Y=1 to skip the console check)
	@scripts/release.sh $(if $(BUMP),--bump $(BUMP),) $(if $(VERSION),--version $(VERSION),) \
	  $(if $(NOTES),--notes $(NOTES),) $(if $(CHECK),--check,) $(if $(SKIP_A11Y),--skip-a11y,)

# One flag only: migrate.sh takes a single mode, and CHECK=1 LIST=1 together
# would concatenate into `--check--list`. LIST has its own target below.
migrate:  ## bring THIS machine up to what the tree assumes (CHECK=1 to look only)
	@scripts/migrate.sh $(if $(CHECK),--check,)

migrate-list:  ## which migrations have run here, and which are pending
	@scripts/migrate.sh --list

# The read-only counterpart to every setup script: which settings has somebody
# changed by hand, and what would a re-run put back? Same LOOK/SHOW split as
# update-check, and for the same reason -- LOOK costs minutes of Chromium
# logins (one per ign-gw.js invocation), SHOW is a file read, safe anywhere.
#
# `|| true` because drift.sh exits 1 when it finds drift -- correct for a
# caller that asked the question, and `make` decorates a non-zero exit as
# `*** Error 1`, which reads as a broken command for an ordinary answer. Anyone
# who wants the exit code calls the script directly.
drift:  ## which gateway settings differ from what the setup scripts would write (reads only, ~5 min; QUICK=1 for the hub pair)
	@scripts/drift.sh $(if $(QUICK),--quick,) || true

drift-show:  ## print the last drift answer -- no gateway calls, instant
	@scripts/drift.sh --show

# What this machine HAD, before an update overwrites it. For inspection and
# manual restore only -- nothing replays a snapshot.
snapshot:  ## save this machine's .gwbk set and gateway config resources (GATEWAY=<name>)
	@scripts/snapshot.sh $(GATEWAY_EXPLICIT)

snapshot-list:  ## what snapshots exist on this machine
	@scripts/snapshot.sh --list

modules-trim:  ## disable the Ignition modules this stack never uses (needs a restart)
	@scripts/ign-modules-trim.sh $(GATEWAY_EXPLICIT)

modules-trim-list:  ## show which modules are enabled on each gateway
	@scripts/ign-modules-trim.sh --list $(GATEWAY_EXPLICIT)

modules-restore:  ## re-enable every module (undoes modules-trim)
	@scripts/ign-modules-trim.sh --restore $(GATEWAY_EXPLICIT)

themes:  ## install the vendored gateway themes onto every running gateway
	@scripts/ign-themes.sh $(if $(filter-out file default,$(origin GATEWAY)),$(GATEWAY),)

validate:  ## the full CI check: project resources, inheritance, compose stacks
	@python3 scripts/validate.py

# Checks the demos you have STARTED, not every demo that exists -- DEMO=all for
# the old sweep, DEMO=eam for one. Empty is the same as unset, which is why
# passing it through unconditionally is safe.
verify-demos:  ## are the STARTED demos ready? read-only (DEMO=all|<id>, QUICK=1)
	@DEMO=$(DEMO) scripts/verify-demos.sh $(if $(QUICK),--quick,)

console-a11y:  ## WCAG 2.1 AA on the console's own HTML -- host only, part of the release gate
	@scripts/console-a11y.sh

perspective-a11y:  ## WCAG 2.1 AA on GatewayAdmin/Site1/Site2/Edge -- host only, part of the release gate; deploy first
	@scripts/perspective-a11y.sh

# GATEWAY_EXPLICIT, not GATEWAY: `GATEWAY ?= ignition` at the top of this file
# DEFINES it for every target, so a bare `make gwbk` would have backed up only
# the hub and said nothing about the other three -- exactly the bug that made
# trial-reset silently skip the backup for a day. Empty means "all".
GATEWAY_EXPLICIT = $(if $(filter-out file default,$(origin GATEWAY)),$(GATEWAY),)

gwbk:  ## take a .gwbk from every gateway into .gwbk/ (gitignored -- holds credentials)
	@scripts/gwbk.sh $(GATEWAY_EXPLICIT)

gwbk-list:  ## what backups are already on this machine
	@scripts/gwbk.sh --list

check-upstream:  ## has a third-party module published a new release?
	@scripts/watch-upstream.sh

upgrade-modules:  ## take the new release: re-hash it and rewrite the manifest + pins
	@scripts/watch-upstream.sh --upgrade

normalise:  ## strip gateway-written signatures/timestamps so diffs stay readable
	@python3 scripts/normalise.py $(PROJECT)

check-secrets:  ## refuse to let a password or private key reach GitHub
	@scripts/check-secrets.sh

hooks:  ## install the pre-commit hook (validate + secret scan)
	@mkdir -p .git/hooks
	@printf '#!/bin/sh\nexec make -s precommit\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit"

precommit: check-secrets validate  ## what the pre-commit hook runs

destroy-all:  ## DESTROY every stack and data volume (prompts; pipe 'DESTROY ALL' to script)
	@scripts/stack.sh destroy all

clean-backups:  ## delete local pull backups
	@rm -rf .pull-backup && echo "removed .pull-backup"
