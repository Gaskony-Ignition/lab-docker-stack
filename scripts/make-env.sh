#!/usr/bin/env bash
#
# Build every stacks/*/.env from its committed .env.example.
#
# The point of this script is that real passwords never enter git. Each
# .env.example carries the variable names, the comments and the non-secret
# values; the two placeholders are filled in here:
#
#   __GEN__   a strong password, generated once and remembered
#   __SET__   something only you can decide (an email address)
#
# Generated values live in the gitignored root .secrets.env, keyed by variable
# NAME. That single detail is what keeps one stack's copy of a shared value --
# POSTGRES_PASSWORD, say -- in lockstep with the stack that owns it: same key,
# same value, no manual syncing, which is the failure the upstream README
# warns about.
#
# Idempotent. Re-running never rotates an existing password and never clobbers
# an existing .env unless you pass --force.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# --- 1. make sure every __GEN__ / __SET__ key has a value in .secrets.env -----

touch "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"

# Defaults for the handful of keys a human has to own. Edit .secrets.env after
# the first run if you want something other than these.
# Not `.test`: it is reserved by RFC 6761, and some email validators reject
# reserved and special-use TLDs outright ("does not appear to be a valid email
# address"). `.local` fails the same way. pgAdmin was the one that crash-looped
# its container over it, and that stack is gone (24/08/2026) -- but these are
# local logins that never receive mail, so an ordinary-looking domain costs
# nothing and is not worth churning to find out who else is strict.
declare -A SET_DEFAULT=(
  [INITIAL_ADMIN_EMAIL]="admin@admin.com"
)

has_secret() { grep -qE "^$1=" "$SECRETS_FILE"; }

gen_password() {
  # 24 URL-safe characters. Deliberately avoids characters that need quoting in
  # a Compose .env file or a JDBC URL.
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24
}

added=0
while IFS= read -r example; do
  while IFS= read -r line; do
    key="${line%%=*}"; val="${line#*=}"
    case "$val" in
      __GEN__)
        has_secret "$key" || { printf '%s=%s\n' "$key" "$(gen_password)" >> "$SECRETS_FILE"; added=$((added+1)); } ;;
      __SET__)
        has_secret "$key" || { printf '%s=%s\n' "$key" "${SET_DEFAULT[$key]:-CHANGEME}" >> "$SECRETS_FILE"; added=$((added+1)); } ;;
    esac
  done < <(grep -E '^[A-Z_][A-Z0-9_]*=(__GEN__|__SET__)$' "$example" || true)
done < <(find "$STACKS_DIR" -name '.env.example' | sort)

# Secrets no stack's .env carries: read straight from .secrets.env by whatever
# needs them. MQTT_PASSWORD is the broker login (distributor-setup.sh,
# ign-mqtt.sh, control/wire.py); WD_CONTROL_TOKEN guards wd-control's MQTT cut
# (control/mqttcut.py), and the hub gets it as a secret via ign-secrets.sh.
for key in MQTT_PASSWORD WD_CONTROL_TOKEN; do
  has_secret "$key" || { printf '%s=%s\n' "$key" "$(gen_password)" >> "$SECRETS_FILE"; added=$((added+1)); }
done

[ "$added" -gt 0 ] && ok "generated $added new secret(s) into .secrets.env"

# --- 2. render each .env from its example ------------------------------------

written=0; skipped=0
while IFS= read -r example; do
  target="${example%.example}"
  stack="$(basename "$(dirname "$example")")"

  if [ -f "$target" ] && [ "$FORCE" -eq 0 ]; then
    # Don't clobber a machine's live .env -- but do carry across any variable
    # the example has gained since it was written. Without this, adding a
    # setting to a .env.example silently has no effect on a machine that
    # already ran this script, and the stack comes up with the compose default
    # instead of the value you just added.
    drift=0
    while IFS= read -r line; do
      key="${line%%=*}"; key="${key#\#}"
      grep -qE "^#?${key}=" "$target" && continue
      [ "$drift" -eq 0 ] && printf '\n# --- added by make-env.sh (new in .env.example) ---\n' >> "$target"
      case "${line#*=}" in
        __GEN__|__SET__) printf '%s=%s\n' "$key" "$(secret "$key")" >> "$target" ;;
        *)               printf '%s\n' "$line" >> "$target" ;;
      esac
      drift=$((drift + 1))
    done < <(grep -E '^#?[A-Z_][A-Z0-9_]*=' "$example" || true)

    if [ "$drift" -gt 0 ]; then
      warn "stacks/$stack/.env was missing $drift setting(s) from the example -- appended"
    fi
    skipped=$((skipped + 1)); continue
  fi

  : > "$target"
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(__GEN__|__SET__)$ ]]; then
      key="${BASH_REMATCH[1]}"
      printf '%s=%s\n' "$key" "$(secret "$key")" >> "$target"
    else
      printf '%s\n' "$line" >> "$target"
    fi
  done < "$example"

  # These files hold gateway and database passwords.
  chmod 600 "$target"
  written=$((written + 1))
  ok "stacks/$stack/.env"
done < <(find "$STACKS_DIR" -name '.env.example' | sort)

[ "$skipped" -gt 0 ] && dim "  $skipped .env file(s) already existed and were left alone (--force to rewrite)"

# --- 3. the gateway credentials file -----------------------------------------
#
# `ign-gw.js` and everything built on it need a login per gateway. That used to
# come from a machine-wide file outside the repo, which is exactly the kind of
# invisible dependency that makes a repo work on one laptop and nowhere else --
# a teammate's clone had no such file and every script failed at the first API
# call with "no credentials for local".
#
# So it is generated here, from the same .secrets.env every stack already draws
# on, and gitignored like the rest. Nothing new to set up, nothing to keep in
# sync, and no password in a second place.
#
# TWO URLs PER GATEWAY, and the difference matters:
#
#   .url      by CONTAINER NAME on the backbone network. What the toolbox uses,
#             and what makes the scripts identical on every machine -- the hub
#             is http://ignition:8088 whether the host published it on 8088,
#             8090 or nothing at all.
#   .hosturl  by host port. What a browser on this machine uses, and what the
#             scripts fall back to when they are run natively rather than
#             through ./wd.
#
# Reading the port from the stack's own .env rather than assuming the default:
# this machine remaps the hub to 8090 because an unrelated gateway owns 8088,
# and a hard-coded 8088 here would point every native run at that other gateway
# -- which answers, authenticates, and is the wrong gateway entirely.
say "gateway credentials"

port_from() {  # port_from <stack> <var> <default>
  local env_file="$STACKS_DIR/$1/.env" value=""
  [ -f "$env_file" ] && value="$(grep -E "^$2=" "$env_file" | head -1 | cut -d= -f2-)"
  printf '%s' "${value:-$3}"
}

GATEWAYS_FILE="$REPO_ROOT/.gateways.env"
ADMIN_USER="$(grep -E '^GATEWAY_ADMIN_USERNAME=' "$STACKS_DIR/ignition/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
ADMIN_USER="${ADMIN_USER:-admin}"

{
  printf '# Generated by scripts/make-env.sh. Gitignored -- never commit this.\n'
  printf '# Regenerate at any time: it is derived entirely from .secrets.env\n'
  printf '# and the host ports in stacks/*/.env.\n#\n'
  printf '# <name>.url      container-name address, used from inside the toolbox\n'
  printf '# <name>.hosturl  host-port address, used when running natively\n\n'

  emit() {  # emit <stanza> <container> <hostport>
    printf '%s.url=http://%s:8088\n'      "$1" "$2"
    printf '%s.hosturl=http://localhost:%s\n' "$1" "$3"
    printf '%s.user=%s\n'                 "$1" "$ADMIN_USER"
    printf '%s.password=%s\n\n'           "$1" "$(secret GATEWAY_ADMIN_PASSWORD)"
  }

  # One stanza per gateway manifest. This used to be a third copy of the
  # gateway list -- with its own stanza map AND its own port map -- and adding
  # the redundant backup meant finding it by the script that broke.
  for gw in $(gateways); do
    emit "$(meta_get "$gw" STANZA)" "$gw" \
         "$(port_from "$gw" "$(meta_get "$gw" PORT_VAR)" "$(meta_get "$gw" PORT_DEFAULT)")"
  done
} > "$GATEWAYS_FILE"

chmod 600 "$GATEWAYS_FILE"
ok ".gateways.env ($(gateways | wc -l | tr -d ' ') gateways)"

say "done -- $written written, $skipped kept"
echo
dim "Secrets live in .secrets.env (chmod 600, gitignored). To see a password:"
dim "    grep GATEWAY_ADMIN_PASSWORD .secrets.env"
