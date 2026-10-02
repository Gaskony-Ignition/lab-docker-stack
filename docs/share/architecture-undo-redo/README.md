# Undo/redo for the Architecture Builder — importable Perspective view

A self-contained Perspective view that adds **undo/redo** to the WARGoetz
Architecture Builder component without forking or modifying the module.

Built and verified on **Ignition 8.3.8** / Perspective **3.3.8** against module
`com.wargoetz.archbuilder` **1.1.2 (b20260722)**
(component `com.wargoetz.reactflow.architecturebuilder`).

## What you get

- `↶ Undo` / `↷ Redo` / `Clear canvas` buttons and a step counter
- **Ctrl+Z**, **Ctrl+Y** and **Ctrl+Shift+Z** on the canvas
- 60 steps of history, with drags collapsed so one gesture is one step

## Install

The module must already be installed and ACTIVE, otherwise the component renders
as an error box.

**Designer:** right-click a folder in Perspective → Views → **Import**, and pick
`view.json`.

**File-based:** copy both files into the project, then run a project scan
(Config → Projects → Scan File System, or `system.project.requestScan()`):

```text
<project>/com.inductiveautomation.perspective/views/<YourViewName>/
    view.json
    resource.json
```

No style classes, bindings or scripts outside the view — it renders on a stock
project. Restyle the three buttons to taste.

## How it works

`nodes` and `edges` are ordinary Perspective props that the component writes back
to, so the canvas is just data in the view. A property-change script on
`props.nodes` pushes a `{key, nodes, edges}` snapshot onto `view.custom.history`;
undo/redo move `view.custom.cursor` and write the snapshot back.

Five things it has to get right — all commented inline in the scripts:

1. `self.props.nodes` is a **Java-backed property tree, not a dict** —
   `json.dumps()` raises. Use `system.util.jsonEncode` / `jsonDecode`.
2. **Seed the stack with the empty canvas.** Otherwise the first entry recorded
   is the state *after* the first edit, undo has nowhere to go, and it reports
   "nothing to undo" — indistinguishable from the script never firing.
3. **Guard the restore by comparing state, not with a flag.** Writing the props
   fires the change script again; a `restoring` boolean races, because the change
   arrives after the flag is cleared. Comparing keys is idempotent.
4. **Debounce (500 ms).** A drag emits a stream of prop writes; without this one
   undo step is one pixel of movement.
5. **Zero `lastEditMillis` on restore**, so the next real edit is never collapsed
   into the step just returned to.

## Known limitations

- **History is per-session and is lost on a full page reload**, because it lives
  in `view.custom`. Persisting it (or exporting the canvas) is separate work.
- **If this view sits in a tab container, set that tab's `runWhileHidden: true`.**
  With the default `false`, leaving the tab unmounts the view; returning remounts
  it with a blank canvas and an empty history, and undo then correctly reports
  "nothing to undo" about a drawing you can no longer see.
- **Snapshots are whole-canvas.** From outside the component there is no way to
  see individual operations. Fine at diagram scale; not for very large canvases.
- **A freshly loaded page has focus on the document, not the view**, so the first
  keystroke lands only after one click anywhere on the page. Dragging a node from
  the palette counts, so this is rarely noticeable.
- **`Clear canvas` is not undoable** — it re-seeds the history deliberately.
  Remove the re-seed in that button's script if you would rather it were.
