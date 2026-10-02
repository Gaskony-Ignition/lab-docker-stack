"""The theme the demonstration is wearing, and how a choice reaches an edge.

A gateway THEME carries the token layer -- ~136 CSS custom properties that
restyle every stock IA component -- and it is a gateway config resource, so it
CANNOT travel in a project export or an inheritance chain. The inherited
`Themes` project carries the 69 style classes a theme categorically cannot
reach. This library is the third piece: the CHOICE, which is the only part that
has to reach an edge, and the only one of the three that is a project resource
an EAM push can carry.

WHY A SOURCE REWRITE RATHER THAN THE TEMPLATE'S TABLE
-----------------------------------------------------
The template persists the chosen pack with `styles.set_current()`, which writes
a `style_prefs` row. That is right for a single gateway and useless here: **EAM's
Send Project carries project RESOURCES and nothing else**, so a database row
themes this gateway and never reaches an edge. The pack therefore has to end up
in a file the push can carry, which means rewriting this module's own source.

WHY IT WRITES EVERY COPY
------------------------
Ignition inheritance is single-parent, and this project, Site1 and Site2 are
siblings under `Themes` -- there is no common local ancestor to hold
one copy. So each carries its own, and a commit has to write all of them or the
sites drift onto different themes. The list is DISCOVERED from the projects
directory rather than hardcoded, so adding a Site needs no edit here.

Jython 2.7 -- no f-strings, no dict comprehensions with complex targets.
"""

# NOTE: `styles` (the inherited template library) is imported INSIDE each
# function, never at module scope. Gateway scripts are restarted per project and
# the parent is not guaranteed to be loaded when this module is first executed;
# a module-level import of an inherited library therefore raises during startup,
# and a Perspective script transform that raises renders the component as a red
# ERROR box while logging NOTHING. Every library in this project follows the
# same lazy-import idiom for the same reason.

# ---------------------------------------------------------------------------
# THE ONE LINE THE DEMO CHANGES.
#
# Every project that carries this library binds session.custom.style to
# chosen_pack(), so this single value themes the whole gateway -- and, because
# it is a project resource, an EAM Send Project push carries it to the edges.
#
# It cannot live in session props: a child project's session-props resource
# REPLACES the parent's wholesale rather than merging with it (verified
# 03/08/2026 -- a Site rendered completely unstyled with session.custom.style
# undefined). The Sites need their own session props for their line data, so
# anything shared has to reach them another way, and a script library is it.
#
# THIS VALUE IS THREE THINGS AT ONCE, which is what makes the whole design work:
# a gateway THEME id (session.props.theme is bound to it), a style-class PREFIX
# ({session.custom.style} + '/containers/card', 417 bindings), and the string
# EAM carries to the edges. One name, so the token layer and the class layer
# cannot drift onto different looks.
CHOSEN_PACK = "leather-dark"

# The nine themes, GENERATED -- scripts/gen-themes.py writes this block from
# ignition/themes/themes.json and `make validate` fails if the two disagree.
# It is written down here rather than read at runtime because a gateway script
# cannot see this repo, and because the picker needs the labels.
# --- BEGIN GENERATED THEMES ---
THEMES = [
    ("glass-violet", "Glass Violet"),
    ("glass-green", "Glass Green"),
    ("leather-light", "Leather Light"),
    ("leather-dark", "Leather Dark"),
    ("finance-ledger", "Finance Ledger"),
    ("newsprint-dark", "Newsprint Dark"),
    ("nord-light", "Nord Light"),
    ("nord-dark", "Nord Dark"),
    ("industrial-light", "Industrial Light"),
    ("industrial-dark", "Industrial Dark"),
    ("light", "Ignition Light"),
    ("dark", "Ignition Dark"),
    ("light-cool", "Ignition Light Cool"),
    ("dark-cool", "Ignition Dark Cool"),
    ("light-warm", "Ignition Light Warm"),
    ("dark-warm", "Ignition Dark Warm"),
]
# --- END GENERATED THEMES ---

THEME_IDS = [t[0] for t in THEMES]
DEFAULT_THEME = THEME_IDS[0]


def themes():
    """[(id, label)] for the picker. A copy, so a caller cannot edit the list."""
    return list(THEMES)


def is_valid(theme_id):
    return theme_id in THEME_IDS

# The library folder name, used both to find the sibling copies and to rewrite
# them. Kept as a constant because it appears in three places below and a
# mismatch would silently write nothing.
LIB = "demo_styles"


def chosen_pack():
    """The pack every project here inherits. Bound from session.custom.style.

    Falls back to the template's default if CHOSEN_PACK is edited to something
    that is not a real pack, so a typo costs the wrong theme rather than an
    unstyled application with 1,300 unresolved classes.
    """
    # Never raises. This is called from a session-prop binding AND from
    # gateway_admin.snapshot(), and a Perspective script transform that raises
    # renders the whole component as a red ERROR box while logging NOTHING --
    # so a bad value here would take out three gateway cards with no way to see
    # why. A typo costs the default theme, not an unstyled application with 69
    # unresolved classes and no page background.
    if is_valid(CHOSEN_PACK):
        return CHOSEN_PACK
    system.util.getLogger("demo_styles").warn(
        "CHOSEN_PACK '%s' is not one of the nine themes -- falling back to '%s'"
        % (CHOSEN_PACK, DEFAULT_THEME))
    return DEFAULT_THEME


def preview_warning(override):
    """"You are previewing X; a push sends Y" -- or "" when they agree.

    THE TRAP THIS EXISTS FOR, hit on 24/08/2026. The Theme button opens the
    template's switcher, which themes THIS SESSION by writing
    `session.custom.styleOverride`. A session does not travel. So picking a pack
    and then clicking Push sends the COMMITTED pack -- whatever `CHOSEN_PACK`
    says -- and both edges correctly receive the pack you did not choose.

    Nothing was broken: the push was perfect, the edges matched the hub exactly,
    and the EAM card said `aurora-violet` in plain text beside a page rendering
    itself in teal. The page held every fact needed to warn and connected none
    of them, which is the failure -- not the mechanism.

    `Commit theme everywhere` is the missing click, and this says so at the
    moment it is missing rather than in a document.

    Takes the override as an ARGUMENT because a script library cannot see the
    session: `runScript('demo_styles.preview_warning', 5000,
    {session.custom.styleOverride})`. The poll rate is what makes it clear the
    instant the switcher is used, without a reload.
    """
    if not override:
        return ""
    committed = chosen_pack()
    if override == committed:
        return ""
    return ("Previewing '" + str(override) + "' in this session only. A push "
            "sends the COMMITTED pack, '" + str(committed)
            + "' -- click Commit theme everywhere first.")


def preview_pack(override):
    """The previewed pack id when it differs from the committed one, else "".

    Feeds the header's `Apply ... everywhere` button: its label and whether it
    is on screen at all. Separate from preview_warning() because a button wants
    the id and a sentence wants the sentence, and deriving one from the other by
    string-slicing is how a label ends up saying "Apply Previewing 'x' in".
    """
    if not override:
        return ""
    return "" if override == chosen_pack() else str(override)


def _projects_dir():
    """Absolute path to this gateway's data/projects, or None.

    Tried in order rather than hardcoded because the container path and a
    Windows install path differ, and this has to work on the work machine too.
    Returns None rather than guessing, so the caller can say so out loud.
    """
    import os
    from java.lang import System as JSystem

    candidates = []
    for prop in ("ignition.installdir", "user.dir"):
        base = JSystem.getProperty(prop)
        if base:
            candidates.append(os.path.join(base, "data", "projects"))
            candidates.append(os.path.join(base, "projects"))
    candidates.append("/usr/local/bin/ignition/data/projects")
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _copies(base):
    """Every project on this gateway carrying a copy of this library.

    Discovered rather than listed, so a new Site scaffolded by `make new-site`
    is picked up with no edit here -- and so a Site that has been retired stops
    being written to without anyone remembering to remove it.
    """
    import os

    found = []
    for name in sorted(os.listdir(base)):
        folder = os.path.join(base, name, "ignition", "script-python", LIB)
        if os.path.isfile(os.path.join(folder, "code.py")):
            found.append((name, folder))
    return found


def _rewrite(folder, pack_id):
    """Set CHOSEN_PACK in one copy and make the gateway notice. True on success.

    resource.json has to be rewritten too, for two separate reasons:

      1. A stale lastModificationSignature makes the SCAN skip the resource with
         no error at all -- the file changes on disk and the gateway ignores it.
      2. EAM's Send Project is DIFFERENTIAL and compares resource METADATA, not
         content. Rewriting code.py without touching lastModification.timestamp
         left the hub correctly re-themed while every push reported Success and
         sent nothing, so the edges stayed on the old pack indefinitely. A
         "Success" from EAM means "nothing to do" just as readily as "sent".
    """
    import os
    import re

    code_path = os.path.join(folder, "code.py")
    handle = open(code_path, "r")
    try:
        lines = handle.readlines()
    finally:
        handle.close()

    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("CHOSEN_PACK"):
            lines[i] = 'CHOSEN_PACK = "%s"\n' % pack_id
            replaced = True
            break
    if not replaced:
        return False

    handle = open(code_path, "w")
    try:
        handle.writelines(lines)
    finally:
        handle.close()

    res_path = os.path.join(folder, "resource.json")
    if os.path.isfile(res_path):
        handle = open(res_path, "r")
        try:
            raw = handle.read()
        finally:
            handle.close()
        raw = re.sub(r',?\s*"lastModificationSignature"\s*:\s*"[^"]*"', "", raw)
        raw = re.sub(r'("timestamp"\s*:\s*")[^"]*(")',
                     r'\g<1>' + _utc_now() + r'\g<2>', raw)
        handle = open(res_path, "w")
        try:
            handle.write(raw)
        finally:
            handle.close()
    return True


def commit_pack(pack_id):
    """Set CHOSEN_PACK across this gateway and scan, so every project follows.

    The repo is the source of truth for these files, so after using the picker
    the repo is behind by one line in each -- `make pull-project PROJECT=<name>`,
    or just edit CHOSEN_PACK to match.

    Returns a human-readable status string; never raises, because it is wired
    straight to a button and a silent failure is what wasted an afternoon here.
    """
    if not is_valid(pack_id):
        return "Rejected: '%s' is not one of the nine themes." % pack_id

    base = _projects_dir()
    if base is None:
        return "Failed: could not locate the gateway's data/projects directory."

    copies = _copies(base)
    if not copies:
        return "Failed: no project on this gateway carries a %s library." % LIB

    written = []
    skipped = []
    for name, folder in copies:
        if _rewrite(folder, pack_id):
            written.append(name)
        else:
            skipped.append(name)

    if not written:
        return "Failed: no CHOSEN_PACK line found in any of %s." % ", ".join(
            [n for n, _ in copies])

    system.project.requestScan()

    # Name what was skipped rather than reporting a clean success. A project
    # whose CHOSEN_PACK line went missing would otherwise sit on the old theme
    # while the button said everything was fine -- the exact failure this
    # module's timestamp bump exists to prevent elsewhere.
    note = ""
    if skipped:
        note = " NOT updated (no CHOSEN_PACK line): %s." % ", ".join(skipped)
    return ("Committed '%s' to %s. They re-theme within ~5 s; press Push to send "
            "it to the edges.%s" % (pack_id, ", ".join(written), note))


def _utc_now():
    """Now, as the ISO-8601 UTC string Ignition writes into resource.json."""
    from java.util import Date, TimeZone
    from java.text import SimpleDateFormat

    fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'")
    fmt.setTimeZone(TimeZone.getTimeZone("UTC"))
    return fmt.format(Date())
