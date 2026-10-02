#!/usr/bin/env bash
#
# Refuse to let a secret reach GitHub.
#
#   scripts/check-secrets.sh            check what git is tracking
#   scripts/check-secrets.sh --all      check every file on disk too
#
# This repo goes to a private GitHub repo that is then shared with a work
# account, so "private" is not the same as "nobody who matters will see it".
# The original stack shipped its .env files and its CA private key inside the
# folder; the whole point of the .env.example layout is that they now stay out.
#
# Runs in CI on every push and as a pre-commit hook.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$REPO_ROOT"

MODE="${1:-tracked}"
fails=0

flag() { printf '%sFAIL%s %s\n' "$C_RED" "$C_RESET" "$*"; fails=$((fails + 1)); }

if [ "$MODE" = "--all" ]; then
  files=$(find . -type f \
            -not -path './.git/*' \
            -not -path './_incoming/*' \
            -not -path './.pull-backup/*')
else
  git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo (use --all to scan the filesystem)"
  files=$(git ls-files)
fi

# --- 1. files that must never be tracked -------------------------------------
# Matched on path, because the contents of a key file are not always obvious.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in
    */.env|.env|.secrets.env)
      flag "$f is an environment file -- commit .env.example instead" ;;
    *.key|*.pem|*.p12|*.pfx|*.jks|*.keystore)
      # A public certificate is fine; a private key is not.
      if grep -qE 'PRIVATE KEY' "$f" 2>/dev/null; then
        flag "$f contains a PRIVATE KEY"
      fi ;;
    *.gwbk)
      flag "$f is a gateway backup -- it contains the gateway's credentials" ;;
    *.sql|backup-*)
      flag "$f looks like a database dump" ;;
  esac
done <<< "$files"

# --- 2. secret-looking content in tracked text -------------------------------
# Deliberately narrow: assignments to a password/token/secret variable with a
# value that is neither a placeholder nor a ${VAR} reference.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in
    *.png|*.jpg|*.gif|*.zip|*.jar|*.modl|*.bin|*.pdf) continue ;;
  esac
  [ -f "$f" ] || continue

  hits=$(grep -nEi '(password|passwd|secret|api[_-]?key|token)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9/+_.-]{8,}' "$f" 2>/dev/null \
         | grep -vE '\$\{?[A-Z_]' \
         | grep -vEi '(__GEN__|__SET__|CHANGEME|example|placeholder|<[a-z-]+>|your[_-]|xxxx|\*\*\*)' \
         | grep -vE '^\s*[0-9]+:\s*[#/]' \
         `# A line that READS a secret is not a line that CONTAINS one. Docs and` \
         `# scripts legitimately show "grep ^X_PASSWORD= .env | cut -d= -f2-";` \
         `# without these the scanner flags its own documentation.` \
         | grep -vE '\$\(' \
         | grep -vE '(^|[^A-Za-z0-9_])\.env([^A-Za-z0-9_]|$)' \
         `# The same exemption in the languages that are not shell:` \
         `# TOKEN = os.environ["TOKEN"] reads one, it does not carry one. This` \
         `# cannot hide a real secret -- a hard-coded value is a literal, and a` \
         `# literal is not a call to the environment.` \
         | grep -vE '(os\.environ|os\.getenv|process\.env|System\.getenv)' \
         `# ...and the general form of the same argument: a value that is a` \
         `# CALL is not a literal. \`user, password = credential(stanza)\` reads` \
         `# one from a file at run time; a hard-coded credential cannot be a` \
         `# call, so this cannot hide one. Narrow on purpose -- the value has to` \
         `# be an identifier immediately followed by an opening bracket.` \
         | grep -vE '[:=][[:space:]]*[A-Za-z_][A-Za-z0-9_.]*\(' \
         | grep -vE '(grep|cut|sed|awk|secret[[:space:]])' \
         `# A DESIGN token is not an API token. The generated theme CSS in` \
         `# ignition/themes/ annotates every value with the pack token it came` \
         `# from -- "#312a47; /* card surface ... (token:surface.card) */" --` \
         `# and "token:" followed by 8+ characters is exactly what the rule` \
         `# above looks for. Nine files, every one a false positive.` \
         `#` \
         `# Exempted by SHAPE, not by file extension or path: the parenthesised` \
         `# form with a lowercase dotted value. Exempting *.css would blind the` \
         `# scanner to a real token in a url(), and exempting the directory` \
         `# would blind it to whatever lands there next.` \
         | grep -vE '\(token:[a-z][a-z0-9._-]*\)' || true)

  if [ -n "$hits" ]; then
    flag "$f may contain a hard-coded credential:"
    printf '%s\n' "$hits" | head -3 | sed 's/^/       /'
  fi
done <<< "$files"

# --- 3. the .gitignore that makes all of the above hold ----------------------
for pat in '.env' '.secrets.env' '*.gwbk' 'certs/'; do
  grep -qxF "$pat" .gitignore 2>/dev/null || grep -qF "$pat" .gitignore 2>/dev/null \
    || flag ".gitignore is missing a rule for '$pat'"
done

echo
if [ "$fails" -eq 0 ]; then
  ok "no secrets found in $(wc -l <<< "$files") file(s)"
else
  die "$fails problem(s) found -- nothing should be pushed until these are clear"
fi
