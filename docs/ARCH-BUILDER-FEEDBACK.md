# Architecture Builder — feedback for the module author

Paste-ready notes for [`ia-tgoetz/ArchitectureBuilderReactFlow`](https://github.com/ia-tgoetz/ArchitectureBuilderReactFlow).
Nothing here is specific to our gateway; it should all reproduce on a stock install.

**Tested against:** module `com.wargoetz.archbuilder` **1.1.2 (b20260722)**, component
`com.wargoetz.reactflow.architecturebuilder`, on **Ignition 8.3.8** with Perspective
3.3.8. Installed via the gateway's own module API; self-signed, `freeModule`, no licence
to activate. Runs clean — no faults, nothing in the log.

First: thank you for the component. It does the thing we wanted (draw a customer's
architecture live, in a browser, in front of them) and it dropped into a project with no
fuss.

---

## 1. Undo/redo — implemented outside the component, and what that cost

There is no undo/redo in the component (we found no `undo`/`redo`/`history` anywhere in
the source at v1.1.2), so we built it in Perspective. **It works well**, and the only
reason it is possible is a design decision you made: `nodes` and `edges` are ordinary
Perspective props and the component writes back to them. The canvas is just data sitting
in the view, which a project script can read and write. That is worth keeping.

The implementation is ~60 lines: a `{key, nodes, edges}` stack in `view.custom.history`
with a cursor, pushed by a property-change script on `props.nodes`, and restored by
writing both props back.

Five things it has to get right — offered because they are the same things an
in-component implementation would face, and because two of them are Perspective traps
rather than anything to do with your module:

1. **`self.props.nodes` is not a dict.** It is a Java-backed property tree, and
   `json.dumps()` raises on it. `system.util.jsonEncode` / `jsonDecode` handle it.
2. **Seed the stack with the empty canvas.** Otherwise the first entry recorded is the
   state *after* the first edit, undo has nowhere to go, and it reports "nothing to
   undo" — indistinguishable from the change script never firing.
3. **Guard the restore by comparing state, not with a flag.** Writing the props fires the
   change script again; a `restoring` boolean races, because the change arrives after the
   flag is cleared. Comparing the incoming state's key against the snapshot at the cursor
   is idempotent, so the restore can fire the script as often as it likes.
4. **Debounce.** A drag emits a stream of prop writes. Without collapsing writes closer
   together than ~500 ms, one undo step is one pixel of movement.
5. **Whole-canvas snapshots only.** We cannot see individual operations from outside, so
   every entry is the full `nodes`+`edges` set. Fine at demo scale; it would not be fine
   on a large diagram.

### Why this still belongs in the component

- **History is per-session and dies on reload**, because `view.custom` is per-session.
  In-component (or persisted) state would survive.
- **Granularity is guesswork.** You know a drag ended; we only know props changed.
- **Every project that uses the component has to rebuild this.**

If you would take a PR for React Flow-native history + `Ctrl+Z`/`Ctrl+Y`, we would be
glad to hear it — happy to contribute what we learned above.

---

## 2. Two small rendering issues

### 2a. The screenshot control's icon is invisible on any dark theme

`.react-flow__controls-button` for **Download Full Screenshot** contains an SVG with
`fill="none"` and a hard-coded **`stroke: #555`**. The other three controls (zoom in,
zoom out, fit view) are fill-drawn and inherit colour normally.

On a dark background the first three icons can be recoloured by setting `fill` on the
button, but the screenshot icon stays `#555` and effectively disappears. Our workaround
is a CSS rule scoped to that one button setting `stroke`, which we would rather not
carry:

```css
.react-flow__controls-button[title="Download Full Screenshot"] svg { stroke: currentColor; }
```

**Suggested fix:** use `currentColor` for that icon's `stroke` (and ideally for all four),
so it follows the surrounding text colour like the rest of the component already does.

### 2b. React Flow's controls do not follow the theme, though everything else does

This is the flip side of a genuinely good decision, so it is worth stating plainly:
**the component itself themes beautifully.** Its internals carry no class names, but it
paints with Ignition's own `--neutral-00…100` custom properties — and because custom
properties inherit, redefining them on an ancestor makes the whole palette, search box,
group headers and canvas resolve to a project's colours. No `!important`, no fork, and it
keeps working if the markup changes. That is a much better outcome than most third-party
components allow, and it should be documented as a supported way to skin it.

The exception is React Flow's bundled `.react-flow__controls`, which ships its own
class-based CSS and stays light on every dark theme. Having those read the same
`--neutral-*` scale would make the component uniformly themeable.

---

## 3. Small things that would help

- **Document the `--neutral-*` theming hook** in the README. We found it by reading
  computed styles; it deserves to be a feature.
- **A version/build readout** somewhere in the component or its props would make it
  easier to report issues against a specific build.
