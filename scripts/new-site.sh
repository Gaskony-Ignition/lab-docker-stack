#!/usr/bin/env bash
#
# Scaffold a new Site project that inherits from the Toolbox_Styles template.
#
#   scripts/new-site.sh <Name> <style-pack> ["Display Title"]
#
#   scripts/new-site.sh Site3 glass-aurora "Site 3 - Northern Plant"
#
# A Site project is deliberately tiny. It carries no views, no style classes and
# no page routes of its own -- all of that is inherited from Toolbox_Styles. The
# only thing it owns is the session-props resource, which pins
# session.custom.style to one style pack and session.custom.line to a line name.
#
# That is what makes the EAM demo land: the thing you push to an edge gateway is
# a handful of kilobytes, but the edge renders a completely different-looking
# application, because the 1100 style classes it resolves against came down with
# the inherited template.
#
# Run scripts/validate.py afterwards -- it checks the parent exists and is
# marked inheritable, which is the one way this can silently fail.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TEMPLATE="Toolbox_Styles"

[ $# -ge 2 ] || die "usage: new-site.sh <Name> <style-pack> [\"Display Title\"]"
NAME="$1"; STYLE="$2"; TITLE="${3:-$1}"

# Ignition project names become directory names and URL segments.
[[ "$NAME" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] \
  || die "'$NAME' is not a valid project name (letters, digits, underscore; must start with a letter)"

dest="$PROJECTS_DIR/$NAME"
[ -e "$dest" ] && die "ignition/projects/$NAME already exists"

# The style pack has to be one the template actually ships, or the session prop
# resolves to style classes that do not exist and every component renders unstyled.
styles_dir="$PROJECTS_DIR/$TEMPLATE/com.inductiveautomation.perspective/style-classes"
[ -d "$styles_dir" ] || die "template '$TEMPLATE' has no style-classes directory"
if [ ! -d "$styles_dir/$STYLE" ]; then
  echo "unknown style pack: $STYLE" >&2
  echo "available:" >&2
  ls -1 "$styles_dir" | sed 's/^/  /' >&2
  exit 1
fi

mkdir -p "$dest/com.inductiveautomation.perspective/session-props"

cat > "$dest/project.json" <<EOF
{
  "title": "$TITLE",
  "description": "Site project. Inherits every view and style class from $TEMPLATE and pins session.custom.style to '$STYLE'.",
  "enabled": true,
  "inheritable": false,
  "parent": "$TEMPLATE"
}
EOF

cat > "$dest/com.inductiveautomation.perspective/session-props/resource.json" <<'EOF'
{
  "scope": "G",
  "version": 1,
  "restricted": false,
  "overridable": true,
  "files": [
    "props.json"
  ],
  "attributes": {
    "lastModification": {
      "actor": "external",
      "timestamp": "1970-01-01T00:00:00Z"
    }
  }
}
EOF

# Overriding the parent's session-props is the whole mechanism. `custom.style`
# is read by every style binding in the inherited StyleDemo view; `custom.site`
# is here so a view can label which gateway it is running on.
#
# appBar.togglePosition MUST be "hidden" -- see validate.py. A child's
# session-props REPLACES the parent's rather than merging, so hiding the app bar
# on the template does not reach the sites; every project carries it itself.
cat > "$dest/com.inductiveautomation.perspective/session-props/props.json" <<EOF
{
  "custom": {
    "style": "$STYLE",
    "site": "$TITLE"
  },
  "props": {
    "locale": "en-US",
    "appBar": {
      "togglePosition": "hidden"
    }
  }
}
EOF

ok "created ignition/projects/$NAME  (parent=$TEMPLATE, style=$STYLE)"
echo
dim "next:"
dim "  make validate"
dim "  make deploy PROJECT=$NAME"
