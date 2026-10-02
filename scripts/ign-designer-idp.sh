#!/usr/bin/env bash
#
# Point the Designer's login at the gateway's identity provider, so that a
# browser which is already signed in does not ask again.
#
#   scripts/ign-designer-idp.sh                 every gateway that is running
#   scripts/ign-designer-idp.sh local           just that one
#   scripts/ign-designer-idp.sh --classic       put it back
#   scripts/ign-designer-idp.sh --check         REPORT only -- writes nothing
#
# --check reports which gateways do not hold the two settings below and exits 1
# if any do not. It is what scripts/drift.sh calls. Note that the same change
# ALSO removes a pointless write from the normal path: the comparison below was
# already computed and printed, and the PUT went out regardless, so every run
# rewrote a setting that was already right -- three gateway calls apiece for no
# change at all. Now the comparison decides, in both modes.
#
# WHY
#
# The console's Open buttons establish a real gateway session in the browser
# (control/autologin.py). `designerAuthStrategy = IDENTITY_PROVIDER` is what
# lets the Designer spend that session: it sends the launcher to the browser at
# /login/idp/start instead of showing its own username/password box, and the
# browser -- already holding the IdP cookie -- completes the round trip without
# a prompt. Click Open, then Launch Designer, and nothing is typed.
#
# WHAT THIS DELIBERATELY DOES NOT TOUCH
#
#   systemIdentityProvider   left as `default`, the gateway's own. Pointing it
#                            at the demo Keycloak would make EVERY gateway
#                            login depend on the identity stack, which is a
#                            demo stack that is normally stopped -- and an
#                            unreachable IdP means no way into the config UI
#                            at all. The identity chain stays a demonstration.
#
#   allowDesignerSSO         left alone. It reads like the setting that matters
#                            and is not: nothing in DesignerRoutes consults it.
#                            It is published to the Designer LAUNCHER through
#                            /system/gwinfo, and it is a legacy 8.1 carry-over
#                            (SystemPropertiesMigrator). Verified by decompiling
#                            gateway-8.3.8.jar -- the only readers are
#                            GatewayInfoServlet and the migrator.
#
# forceIdpAuth IS SET OFF, AND WITHOUT IT THE REST OF THIS BUYS NOTHING.
#
# It is the "always ask the IdP to re-authenticate users by default" checkbox,
# and it is not advisory: with it on, every authorization request the gateway
# makes carries `prompt=login&max_age=1`, which obliges the IdP to ask again
# however fresh the session is. Measured on 8.3.8 by reading the redirect:
#
#   forceIdpAuth=true    ...&prompt=login&max_age=1     -> always challenged
#   forceIdpAuth=false   (neither parameter)            -> existing session passes
#
# So a Designer pointed at the IdP would still stop and ask for a password,
# which reads as designerAuthStrategy not having worked. With it off, a browser
# already holding the session is redirected straight to /app -- verified.
#
# This IS a security decision and it is deliberate: on a demonstration rig the
# whole point is not typing a password in front of an audience, and the session
# still expires on its own. `--keep-reauth` leaves it alone.
#
# THE VALUE IS AN ENUM AND ONLY TWO ARE LEGAL: CLASSIC, IDENTITY_PROVIDER
# (common.jar -> com/inductiveautomation/ignition/common/auth/strategy/
# AuthenticationStrategy). Anything else is rejected by validation.
#
# The write is a SINGLETON write: PUT the plain type path with a ONE-ELEMENT
# ARRAY, carrying the signature back as an optimistic lock. The per-name path
# does not exist and 404s, which reads exactly like the wrong endpoint.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WANT=IDENTITY_PROVIDER
REAUTH=false          # forceIdpAuth: off, so a live session is not challenged
CHECK=0
DRIFT=0
args=()
for a in "$@"; do
  case "$a" in
    --classic) WANT=CLASSIC; REAUTH=true ;;
    --idp)     WANT=IDENTITY_PROVIDER ;;
    --keep-reauth) REAUTH=keep ;;
    --check)   CHECK=1 ;;
    -*)        echo "unknown option: $a" >&2; exit 2 ;;
    *)         args+=("$a") ;;
  esac
done

# No gateway named: every gateway stanza whose container is actually up. The
# edges and the redundant backup are demo stacks and are usually stopped, so
# insisting on all of them would make this fail on a core-only machine.
#
# THE BACKUP IS SKIPPED, AND NOT AS A CONVENIENCE. A redundant backup refuses
# every config write -- `403 NotPrimaryException: Cannot modify settings on
# backup node` -- because redundancy SYNCHRONISES this setting from the master.
# Writing it there is not merely unnecessary, it is impossible, and doing it
# anyway printed `local-backup WRITE FAILED` with a stack trace in the middle
# of an otherwise clean bootstrap: a red failure for a gateway that gets the
# setting anyway, seconds later, from its master. Name a backup explicitly and
# it still tries, which is the right shape -- the skip is for the sweep.
# Same reasoning as ign-mqtt.sh leaving the backup alone.
if [ ${#args[@]} -eq 0 ]; then
  targets=()
  for g in $(gateways); do
    stanza="$(meta_get "$g" STANZA)"
    [ -n "$stanza" ] || continue
    if [ "$(meta_get "$g" ROLE)" = backup ]; then
      printf '%-16s SKIP (backup node -- redundancy syncs this from the master)\n' "$g"
      continue
    fi
    if docker ps --format '{{.Names}}' | grep -qx "$g"; then
      targets+=("$stanza")
    else
      printf '%-16s SKIP (not running)\n' "$g"
    fi
  done
else
  targets=("${args[@]}")
fi

[ ${#targets[@]} -gt 0 ] || { echo "no gateway is running -- start one first"; exit 0; }

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

for stanza in "${targets[@]}"; do
  cur="$work/$stanza.json"
  if ! node "$(dirname "${BASH_SOURCE[0]}")/ign-gw.js" api --gateway "$stanza" \
        --path /data/api/v1/resources/singleton/ignition/security-properties \
        > "$cur" 2>"$work/err"; then
    printf '%-16s FAILED to read security properties\n  %s\n' \
      "$stanza" "$(head -c 200 "$work/err")"
    continue
  fi

  # ign-gw.js prints a status line before the body; drop everything up to the
  # first brace rather than assuming a fixed number of header lines.
  # Removed first: a verdict left over from the PREVIOUS stanza would otherwise
  # be read as this one's if the python below failed to run at all.
  rm -f "$work/verdict"
  # argv[5] is a VERDICT FILE, and it is the whole of the change here: the
  # comparison was always computed in this block and then thrown away, so the
  # PUT below could not be conditional on it. A file rather than parsing the
  # printed line, because that line is for a human and is free to be reworded.
  python3 - "$cur" "$WANT" "$work/put.json" "$REAUTH" "$work/verdict" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
doc = json.loads(raw[raw.index("{"):])
want, reauth = sys.argv[2], sys.argv[4]
have = doc["config"].get("designerAuthStrategy")
have_reauth = doc["config"].get("forceIdpAuth")
doc["config"]["designerAuthStrategy"] = want
if reauth != "keep":
    doc["config"]["forceIdpAuth"] = (reauth == "true")
# The body is an ARRAY even for a singleton, and the signature has to travel
# with it -- omitting it earns a 400 naming the missing field.
open(sys.argv[3], "w").write(json.dumps([{
    "name": doc.get("name", "security-properties"),
    "collection": doc.get("collection", "core"),
    "enabled": doc.get("enabled", True),
    "signature": doc["signature"],
    "config": doc["config"],
}]))
# forceIdpAuth is part of the answer, not a detail: with it on, a Designer
# pointed at the IdP is challenged anyway (the measurement above), so a gateway
# holding the right strategy and the wrong flag HAS drifted.
differs = have != want or (reauth != "keep" and have_reauth != (reauth == "true"))
open(sys.argv[5], "w").write("%s %s %s\n" % (
    "differs" if differs else "same", have, json.dumps(have_reauth)))
print("  already %s" % want if have == want else "  %s -> %s" % (have, want))
PY

  verdict=differs; have_strategy='?'; have_reauth='?'
  if [ -s "$work/verdict" ]; then
    read -r verdict have_strategy have_reauth < "$work/verdict" || true
  fi

  if [ "$CHECK" -eq 1 ]; then
    if [ "$verdict" = same ]; then
      printf '%-16s designerAuthStrategy=%s  forceIdpAuth=%s\n' \
        "$stanza" "$have_strategy" "$have_reauth"
    else
      printf '  drift: %s designerAuthStrategy=%s forceIdpAuth=%s -- should be %s / %s (Config > Security > General)\n' \
        "$stanza" "$have_strategy" "$have_reauth" "$WANT" "$REAUTH"
      DRIFT=1
    fi
    continue
  fi

  # THE GATE, and the pointless write it removes. A gateway already holding both
  # settings is reported from the values JUST READ -- no PUT, and no re-read
  # either, because there is nothing to verify. That is three gateway calls
  # saved per already-correct gateway, on a rig where each one costs ~17s.
  if [ "$verdict" = same ]; then
    printf '%-16s designerAuthStrategy=%s  forceIdpAuth=%s\n' \
      "$stanza" "$have_strategy" "$have_reauth"
    continue
  fi

  if node "$(dirname "${BASH_SOURCE[0]}")/ign-gw.js" api --gateway "$stanza" \
       --method PUT --path /data/api/v1/resources/ignition/security-properties \
       --body-file "$work/put.json" >"$work/out" 2>&1; then
    # Verify by READING IT BACK, not from the write's status. A 200 here means
    # the request was accepted, which is not the same as the value having
    # changed -- the same distinction EAM's "Success" hides.
    got="$(node "$(dirname "${BASH_SOURCE[0]}")/ign-gw.js" api --gateway "$stanza" \
        --path /data/api/v1/resources/singleton/ignition/security-properties 2>/dev/null \
        | sed -n 's/.*"designerAuthStrategy": *"\([A-Z_]*\)".*/\1/p' | head -1)"
    if [ "$got" = "$WANT" ]; then
      reauth_now="$(node "$(dirname "${BASH_SOURCE[0]}")/ign-gw.js" api --gateway "$stanza" \
          --path /data/api/v1/resources/singleton/ignition/security-properties 2>/dev/null \
          | sed -n 's/.*"forceIdpAuth": *\(true\|false\).*/\1/p' | head -1)"
      printf '%-16s designerAuthStrategy=%s  forceIdpAuth=%s\n' \
        "$stanza" "$got" "${reauth_now:-?}"
    else
      printf '%-16s WROTE but reads back as %s (wanted %s)\n' "$stanza" "${got:-?}" "$WANT"
    fi
  else
    printf '%-16s WRITE FAILED\n  %s\n' "$stanza" "$(head -c 300 "$work/out")"
  fi
done

# `if`, not `&&` -- a && list whose test fails takes over the script's exit
# status, which under `set -e` ends it right here (CLAUDE.md).
if [ "$CHECK" -eq 1 ] && [ "$DRIFT" -ne 0 ]; then exit 1; fi
exit 0
