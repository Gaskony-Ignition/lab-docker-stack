#!/usr/bin/env bash
#
# Install third-party Ignition modules (.modl) into a gateway, headlessly.
#
#   scripts/ign-modules.sh --list [gateway]     what is registered now
#   scripts/ign-modules.sh        [gateway]     install everything in modules/
#
# .modl files are licensed binaries, so modules/ is gitignored. Each gateway gets
# its own subfolder because the hub and the spokes need different modules:
#
#   modules/all/              installed on every gateway
#   modules/ignition/         hub      (MQTT Engine, Embr Charts)
#   modules/ignition-edge1/   spoke    (MQTT Transmission)
#   modules/ignition-edge2/   spoke
#
# ---------------------------------------------------------------------------
# Edge gateways barely take third-party modules at all
# ---------------------------------------------------------------------------
# "Third party modules will not run on an Edge Gateway, with the exception of any
# third party module explicitly stated by a product, such as the Edge IIoT
# product." That is a platform allowlist, not a compatibility problem -- there is
# no way to sideload around it. Cirrus Link MQTT Transmission is one of the
# allowlisted exceptions; Embr Charts is not, which is why it sits under
# modules/ignition/ and not modules/all/.
#
# ---------------------------------------------------------------------------
# This triggers a one-time commissioning gate -- read before running
# ---------------------------------------------------------------------------
# Registering a third-party module makes the gateway demand certificate
# acceptance on next start. Until a human completes it, the gateway reports
# {"state":"RUNNING","details":"COMMISSIONING"} and **302-redirects every request
# to /welcome** -- Perspective sessions included. Verified 31/07/2026: it took the
# hub's projects from HTTP 200 to HTTP 302 until the entries were removed again.
#
# So: run this when nobody is watching, and finish the wizard at
# http://<gateway>/welcome straight afterwards. If you need to back out, the
# pre-change registry is kept as data/modules.json.bak.
#
# The alternative is the gateway's own upload/install UI, which handles the
# certificate prompt inline and never locks the gateway. Prefer it if a session
# matters more than the scripting.
#
# ---------------------------------------------------------------------------
# How 8.3 actually installs a module
# ---------------------------------------------------------------------------
# This is NOT what 8.1 did, and getting it wrong fails silently in both of the
# ways that waste the most time. The recipe below is the proven one:
#
#   stop the gateway
#     -> copy the .modl into data/var/ignition/modl/
#     -> add an entry to data/modules.json
#     -> chown modules.json back to the ignition user
#     -> start the gateway
#
# 1. **`user-lib/modules/` is the wrong place.** On 8.3 a .modl dropped there is
#    silently ignored -- no error, no log line, the module simply does not exist.
#    The gateway reads data/modules.json, an explicit registry keyed by module id,
#    and loads the file each entry points at.
#
# 2. **Ownership of modules.json is a trap that has cost a full day before.**
#    Writing it from the host (docker cp, a volume write, sed from outside)
#    leaves it owned by uid 1000 rather than the `ignition` user. The gateway
#    then fails EVERY module install/uninstall silently: the staged operation
#    never commits, the UI still toasts "Module Installed", the old version keeps
#    running, and the only evidence is an AccessDeniedException in the boot log.
#    Everything here therefore edits the file INSIDE the container and always
#    finishes with an explicit chown.
#
# 3. The gateway must be stopped while this happens, or it rewrites modules.json
#    from memory on the way down and discards the new entries.
#
# certFingerprint is the SHA-1 of the LEAF certificate in the .modl's
# certificates.p7b, lowercase and without colons. Verified by recomputing it for
# a module the gateway already trusted and matching the registry byte for byte.
#
# It does NOT substitute for accepting the certificate: an earlier version of
# this comment claimed the fingerprint alone was enough to skip the prompt, and
# testing disproved it -- the gateway still raised the commissioning gate
# described above. The fingerprint identifies the signer; a human still has to
# say they trust it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --yes: skip the commissioning-gate prompt. For bootstrap only -- see below.
ASSUME_YES=0
for a in "$@"; do [ "$a" = "--yes" ] && ASSUME_YES=1; done
set -- $(printf '%s\n' "$@" | grep -v '^--yes$' || true)

MODL_DIR=/usr/local/bin/ignition/data/var/ignition/modl
REGISTRY=/usr/local/bin/ignition/data/modules.json
MODULES_SRC="$REPO_ROOT/modules"

# --- --list -------------------------------------------------------------------

if [ "${1:-}" = "--list" ]; then
  GATEWAY="${2:-ignition}"
  need_docker; require_gateway "$GATEWAY"
  say "modules registered on $GATEWAY"
  docker exec "$GATEWAY" jq -r 'to_entries[] | "  \(.key)\n      \(.value.filename)"' "$REGISTRY"
  echo
  dim "Verify in the UI at /app/platform/system/modules -- the status column reads"
  dim "ACTIVE (not \"Running\"), and the table paginates at 20 rows."
  exit 0
fi

GATEWAY="${1:-ignition}"
need_docker; require_gateway "$GATEWAY"

as_root() { docker exec -u 0 "$GATEWAY" "$@"; }

STAGED="$(mktemp)"
trap 'rm -f "$STAGED" "$STAGED.json"' EXIT
: > "$STAGED"

# --- 1. work out what to install ---------------------------------------------

for dir in "$MODULES_SRC/all" "$MODULES_SRC/$GATEWAY"; do
  [ -d "$dir" ] || continue
  while IFS= read -r modl; do
    [ -n "$modl" ] || continue
    printf '%s\t%s/%s\n' "$modl" "$MODL_DIR" "$(basename "$modl")" >> "$STAGED"
  done < <(find "$dir" -maxdepth 1 -name '*.modl' 2>/dev/null | sort)
done

count=$(wc -l < "$STAGED")
if [ "$count" -eq 0 ]; then
  warn "no .modl files in modules/all or modules/$GATEWAY"
  dim "Cirrus Link MQTT: inductiveautomation.com/downloads"
  dim "Embr Charts:      Ignition Exchange"
  exit 0
fi

say "installing $count module(s) on $GATEWAY"

# --- 2. read id + signing fingerprint out of each .modl ----------------------

python3 - "$STAGED" > "$STAGED.json" <<'PY'
import json, re, subprocess, sys, zipfile

entries = {}
for line in open(sys.argv[1]):
    line = line.rstrip('\n')
    if not line:
        continue
    local, remote = line.split('\t')
    with zipfile.ZipFile(local) as z:
        xml = z.read('module.xml')
        mid = re.search(rb'<id>([^<]+)</id>', xml).group(1).decode()
        name = re.search(rb'<name>([^<]+)</name>', xml).group(1).decode()
        ver = re.search(rb'<version>([^<]+)</version>', xml).group(1).decode()
        p7b = z.read('certificates.p7b')

    # The signing certificate is the first in the chain; the rest are CAs.
    # certificates.p7b is three different things depending on who signed the
    # module: PEM PKCS7 (Embr), DER PKCS7 (Cirrus Link), or -- despite the
    # extension -- a BARE PEM CERTIFICATE (the Architecture Builder). openssl
    # pkcs7 refuses that last one in both encodings, and the first from-scratch
    # bootstrap died on it, taking every other hub module down with it: the
    # sys.exit here aborts the whole staged install, so one odd certificate
    # cost MQTT Engine, Embr and the Architecture tab at once, while the step
    # read as ok because the gateway was still serving.
    blob = p7b.decode('latin-1')
    if '-----BEGIN CERTIFICATE-----' in blob:
        pem = blob
    else:
        pem = ''
        for enc in ('PEM', 'DER'):
            out = subprocess.run(['openssl', 'pkcs7', '-inform', enc, '-print_certs'],
                                 input=p7b, capture_output=True).stdout.decode()
            if '-----BEGIN CERTIFICATE-----' in out:
                pem = out
                break
    if not pem:
        sys.exit(f'could not read certificates.p7b from {local} as PEM or DER')

    start = pem.index('-----BEGIN CERTIFICATE-----')
    end = pem.index('-----END CERTIFICATE-----') + len('-----END CERTIFICATE-----')
    fp = subprocess.run(['openssl', 'x509', '-noout', '-fingerprint', '-sha1'],
                        input=pem[start:end].encode(), capture_output=True).stdout.decode()
    fp = fp.split('=')[1].strip().replace(':', '').lower()

    entries[mid] = {'filename': remote, 'onStartup': 'enabled', 'certFingerprint': fp}
    print(f'  {name} {ver}\n      {mid}', file=sys.stderr)

json.dump(entries, sys.stdout)
PY

# --- 3. prepare while the gateway is still up --------------------------------
# `docker exec` needs a running container, so everything that needs a shell in
# the gateway happens now: create the modl directory and learn the numeric uid
# of the `ignition` user, which is what the ownership repair in step 5 needs
# (a stock alpine helper container has no `ignition` user to resolve by name).

IGN_UID_GID="$(docker exec "$GATEWAY" stat -c '%u:%g' "$REGISTRY")"
docker exec -u 0 "$GATEWAY" mkdir -p "$MODL_DIR"
docker exec -u 0 "$GATEWAY" chown "$IGN_UID_GID" "$MODL_DIR"

# Merge on the host, where python3 lives, then push one finished file. Merging
# rather than replacing matters: modules.json lists every built-in module, so
# overwriting it would uninstall all of them. Re-running updates an existing
# entry instead of duplicating it.
docker cp "$GATEWAY:$REGISTRY" "$STAGED.current" >/dev/null
python3 - "$STAGED.current" "$STAGED.json" > "$STAGED.merged" <<'PY'
import json, sys
reg = json.load(open(sys.argv[1]))
new = json.load(open(sys.argv[2]))
before = set(reg)
reg.update(new)
for k in new:
    print(f"  {'updated' if k in before else 'added  '} {k}", file=sys.stderr)
json.dump(reg, sys.stdout, indent=2)
PY

# --- 4. stop, copy, start -----------------------------------------------------
# Stopping is required: a running gateway rewrites modules.json from memory as it
# shuts down, which discards anything written underneath it.

warn "this puts $GATEWAY into a commissioning gate: it will redirect every"
warn "request to /welcome, including live Perspective sessions, until a human"
warn "accepts the third-party certificates there."

# --yes exists for bootstrap on a machine where these gateways have just been
# created and there is nothing to interrupt. It is NOT a general convenience:
# on a running stack the prompt is the last thing standing between a module
# install and every Perspective session in the building bouncing to /welcome.
# bootstrap.sh follows the install with `ign-gw.js commission`, which clears the
# gate straight away; a bare --yes on its own leaves the gateway locked.
if [ "${ASSUME_YES:-0}" != "1" ]; then
  printf 'Continue? [y/N] '
  read -r reply
  case "$reply" in [yY]*) ;; *) die "aborted -- nothing was changed" ;; esac
else
  dim "  --yes given: continuing without a prompt"
fi

say "stopping $GATEWAY to install"
docker stop "$GATEWAY" >/dev/null

# Keep the pre-change registry so a bad install can be backed out without
# reconstructing 32 built-in entries by hand.
docker cp "$STAGED.current" "$GATEWAY:$REGISTRY.bak"
docker cp "$STAGED.merged" "$GATEWAY:$REGISTRY"
while IFS=$'\t' read -r local remote; do
  [ -n "$local" ] || continue
  docker cp "$local" "$GATEWAY:$remote"
  ok "$(basename "$local")"
done < "$STAGED"

# --- 5. repair ownership BEFORE starting -------------------------------------
# This is the step that has cost a day before. Everything docker cp just wrote is
# root-owned; a root-owned modules.json makes the gateway fail every future
# module install silently -- the UI still says "Module Installed" and the old
# version keeps running. --volumes-from works on a stopped container, and the
# uid:gid is numeric because this helper image has no `ignition` user.
docker run --rm -u 0 --volumes-from "$GATEWAY" alpine:3.20 \
  chown -R "$IGN_UID_GID" "$REGISTRY" "$REGISTRY.bak" "$MODL_DIR"
ok "ownership repaired to $IGN_UID_GID"

say "starting $GATEWAY"
docker start "$GATEWAY" >/dev/null
wait_for_gateway "$GATEWAY" 420

echo
warn "$GATEWAY is now in a commissioning gate and is serving nothing else."
echo
say "finish it in a browser, now:"
dim "    http://localhost:$(grep -E "^(IGNITION|EDGE1|EDGE2)_HTTP_HOST_PORT=" "$STACKS_DIR/$GATEWAY/.env" 2>/dev/null | cut -d= -f2- | tail -1)/welcome"
echo
dim "To back out instead:"
dim "    docker exec -u 0 $GATEWAY sh -c 'cd /usr/local/bin/ignition/data \\"
dim "      && cp modules.json.bak modules.json && chown ignition:ignition modules.json'"
dim "    cd stacks/$GATEWAY && docker compose restart"
