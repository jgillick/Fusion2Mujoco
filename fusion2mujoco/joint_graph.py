# -*- coding: utf-8 -*-
"""
Render the model's kinematic (joint) graph as text diagrams, for debugging.

MJCF requires a kinematic *tree*: every body has exactly one parent, and
there are no loops. Fusion assemblies allow much more than that, so a design
that looks right in Fusion can produce a model whose bodies are missing,
floating free, or attached to the wrong parent.

This module renders what the exporter actually derived from the design --
including the joints it had to discard -- in two zero-dependency formats:

- **HTML** (``<model>_joint_graph.html``), a self-contained report that draws
  the graph in a browser. This is what the "Preview joint graph" button opens.
- **Mermaid** (``<model>_joint_graph.md``), which renders in GitHub, GitLab
  and VS Code. Paste it into a bug report and the graph shows up inline.
- **Graphviz DOT** (``<model>_joint_graph.dot``), for larger assemblies where
  ``dot -Tpng <file> -o graph.png`` gives a better layout.

Nothing here touches the Fusion API: the graph is assembled from plain
strings by :meth:`MjcfBuilder.parse_joint_relationships`, so it can be built
and rendered even when the export itself is about to fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from os import path

from .. import config

MERMAID_SUFFIX = "_joint_graph.md"
DOT_SUFFIX = "_joint_graph.dot"
HTML_SUFFIX = "_joint_graph.html"

# Drawn by the HTML report. Pinned to an exact version, and the page still
# shows the diagram source as text if it cannot be fetched (no internet).
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

LEGEND = (
    ("Blue node", "Root body -- attached to the world, not to another body"),
    ("Red node", "Part of a parent loop; MJCF cannot represent it"),
    ("Solid arrow", "Movable joint (hinge or slide)"),
    ("Grey dashed arrow", "Rigid joint -- nests the body, emits no <joint>"),
    ("Red dashed arrow", "Joint the exporter dropped"),
)

# Edge status values.
STATUS_OK = "ok"  # a real, movable joint
STATUS_RIGID = "rigid"  # nests the body, but emits no <joint> element
STATUS_SKIPPED = "skipped"  # dropped: one end has no exported body
STATUS_CONFLICT = "conflict"  # dropped: the child already had a parent joint

_DROPPED = (STATUS_SKIPPED, STATUS_CONFLICT)

WORLD_ID = "world"
MODEL_ROOT_ID = "model_root"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """``singular`` when ``count`` is 1, otherwise ``plural`` (default +s)."""
    return singular if count == 1 else (plural or singular + "s")


def _html_text(text: str) -> str:
    """
    Escape a string for use as HTML text.

    The Mermaid source is escaped with this before being placed in the page.
    The browser decodes the entities back when it builds the text node, so
    Mermaid still receives exactly the source written to the ``.md`` file --
    including the ``<br/>`` it expects inside node labels.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mermaid_text(text: str) -> str:
    """Escape a string for use inside a quoted Mermaid label."""
    return (
        str(text)
        .replace("\\", "/")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("|", "#124;")
    )


def _dot_text(text: str) -> str:
    """Escape a string for use inside a quoted Graphviz label."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


# The preview page. Kept as a template rather than an f-string so the CSS
# and JavaScript braces need no escaping, and raw so the regex in the
# script survives. Placeholders are the "@@NAME@@" tokens, filled in by
# JointGraph.to_html().
_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@ joint graph</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px 16px 48px;
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    color: #1b1f24; background: #fff;
  }
  .wrap { max-width: 1280px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .04em;
       color: #57606a; margin: 32px 0 10px; }
  .meta { color: #57606a; font-size: 13px; margin: 0 0 20px; }
  .status { padding: 10px 14px; border-radius: 6px; font-weight: 600;
            border: 1px solid transparent; }
  .status.good { background: #e8f5ec; border-color: #9fd3b0; color: #116329; }
  .status.warn { background: #fff6e0; border-color: #e6c67a; color: #7a5a00; }
  .status.bad  { background: #ffeceb; border-color: #e5a3a0; color: #9b1c17; }
  .chips { display: flex; flex-wrap: wrap; gap: 10px; margin: 18px 0 0; }
  .chip { border: 1px solid #d8dee4; border-radius: 6px; padding: 10px 14px;
          min-width: 104px; }
  .chip .n { display: block; font-size: 20px; font-weight: 650; }
  .chip .l { display: block; font-size: 12px; color: #57606a; }
  ol.problems { margin: 0; padding-left: 22px; }
  ol.problems li { margin-bottom: 6px; }

  .toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
             margin-bottom: 8px; }
  .toolbar button {
    font: inherit; font-size: 13px; padding: 4px 10px; cursor: pointer;
    border: 1px solid #d8dee4; border-radius: 6px; background: #f6f8fa;
    color: #1b1f24;
  }
  .toolbar button:hover { background: #eaeef2; }
  .toolbar .zoom { font-size: 13px; color: #57606a; min-width: 48px;
                   text-align: center; font-variant-numeric: tabular-nums; }
  .toolbar .hint { font-size: 12px; color: #8c959f; margin-left: auto; }

  #pane {
    border: 1px solid #d8dee4; border-radius: 6px; background: #fff;
    overflow: auto; height: 70vh; min-height: 180px; resize: vertical;
    cursor: grab; padding: 16px;
  }
  #pane.grabbing { cursor: grabbing; }
  #graph svg { display: block; max-width: none; }
  #source { white-space: pre-wrap; font: 12px/1.4 ui-monospace, SFMono-Regular,
            Menlo, monospace; margin: 0; }
  #fallback { display: none; }
  #fallback .why { color: #9b1c17; font-size: 13px; margin: 0 0 10px; }

  table { border-collapse: collapse; font-size: 14px; }
  td { border-bottom: 1px solid #eaeef2; padding: 6px 16px 6px 0;
       vertical-align: top; }
  td:first-child { white-space: nowrap; color: #57606a; }
  footer { margin-top: 36px; color: #57606a; font-size: 13px;
           border-top: 1px solid #eaeef2; padding-top: 14px; }
  footer p { margin: 4px 0; word-break: break-all; }
  @media (max-width: 600px) { body { padding: 16px; } #pane { height: 60vh; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>@@TITLE@@ joint graph</h1>
  <p class="meta">Fusion2Mujoco v@@VERSION@@</p>
  <div class="status @@STATUS_CLASS@@">@@STATUS_TEXT@@</div>
  <div class="chips">@@CHIPS@@</div>
  @@PROBLEMS@@
  <section>
    <h2>Graph</h2>
    <div class="toolbar">
      <button type="button" id="zoomOut" title="Zoom out">&minus;</button>
      <span class="zoom" id="zoomLabel">100%</span>
      <button type="button" id="zoomIn" title="Zoom in">+</button>
      <button type="button" id="fit">Fit</button>
      <button type="button" id="actual">100%</button>
      <button type="button" id="flip">Left to right</button>
      <span class="hint">Drag to pan &middot; Ctrl/&#8984; + scroll to zoom &middot;
            drag the bottom edge to resize</span>
    </div>
    <div id="pane"><div id="graph"></div></div>
    <div id="fallback">
      <p class="why">Could not draw the diagram. Its source is below.</p>
    </div>
    <pre id="source" hidden>@@SOURCE@@</pre>
  </section>
  <section>
    <h2>Legend</h2>
    <table>@@LEGEND@@</table>
  </section>
  <footer>
    @@FOOTER@@
    <p>Generated by Fusion2Mujoco &mdash; @@REPO@@</p>
  </footer>
</div>

<script src="@@MERMAID_CDN@@"></script>
<script>
(function () {
  var source = document.getElementById("source").textContent;
  var pane = document.getElementById("pane");
  var host = document.getElementById("graph");
  var label = document.getElementById("zoomLabel");
  var flip = document.getElementById("flip");
  var natural = { w: 0, h: 0 };
  var zoom = 1;
  var direction = "TD";
  var counter = 0;

  // Below this, labels stop being readable, so "Fit" stops shrinking and
  // lets the graph overflow for panning instead.
  var MIN_FIT = 0.4;

  function showSource(why) {
    var fallback = document.getElementById("fallback");
    if (why) { fallback.querySelector(".why").textContent = why; }
    fallback.style.display = "block";
    document.getElementById("source").hidden = false;
    pane.style.display = "none";
    document.querySelector(".toolbar").style.display = "none";
  }

  if (!window.mermaid) {
    showSource("Could not load the diagram renderer (no internet?). " +
               "Its source is below.");
    return;
  }

  function applyZoom() {
    var svg = host.querySelector("svg");
    if (!svg || !natural.w) { return; }
    svg.style.width = (natural.w * zoom) + "px";
    svg.style.height = (natural.h * zoom) + "px";
    label.textContent = Math.round(zoom * 100) + "%";
  }

  function setZoom(next) {
    zoom = Math.min(4, Math.max(0.1, next));
    applyZoom();
  }

  function fit() {
    if (!natural.w) { return; }
    // Scale so the whole graph is visible, but never blow it up past 100%
    // nor shrink it past the point of being readable.
    var room = pane.clientWidth - 32;
    setZoom(Math.max(MIN_FIT, Math.min(1, room / natural.w)));
    sizePane();
  }

  function sizePane() {
    // A left-to-right graph is wide and short, so a fixed-height pane would
    // be mostly empty. Only "Fit" and a redraw resize the pane, leaving any
    // height the user dragged for themselves alone.
    var cap = Math.max(180, Math.round(window.innerHeight * 0.7));
    pane.style.height = Math.min(cap, natural.h * zoom + 34) + "px";
  }

  function draw() {
    // Mermaid needs the direction on the first line; swap it in place.
    var text = source.replace(/^graph\s+(TD|LR)/m, "graph " + direction);
    counter += 1;
    mermaid.render("jointGraph" + counter, text).then(function (result) {
      host.innerHTML = result.svg;
      var svg = host.querySelector("svg");
      // Mermaid sizes the SVG itself; take over so zoom is ours to control.
      var box = svg.viewBox && svg.viewBox.baseVal;
      natural = box && box.width
        ? { w: box.width, h: box.height }
        : { w: svg.getBoundingClientRect().width,
            h: svg.getBoundingClientRect().height };
      svg.removeAttribute("width");
      svg.removeAttribute("height");
      fit();
    }).catch(function (error) {
      showSource("Could not draw the diagram (" + error.message + "). " +
                 "Its source is below.");
    });
  }

  mermaid.initialize({
    startOnLoad: false,
    theme: "default",
    flowchart: { useMaxWidth: false, htmlLabels: true }
  });

  document.getElementById("zoomIn").onclick = function () { setZoom(zoom * 1.25); };
  document.getElementById("zoomOut").onclick = function () { setZoom(zoom / 1.25); };
  document.getElementById("fit").onclick = fit;
  document.getElementById("actual").onclick = function () { setZoom(1); };
  flip.onclick = function () {
    direction = direction === "TD" ? "LR" : "TD";
    flip.textContent = direction === "TD" ? "Left to right" : "Top down";
    draw();
  };

  pane.addEventListener("wheel", function (event) {
    if (!event.ctrlKey && !event.metaKey) { return; }
    event.preventDefault();
    setZoom(zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1));
  }, { passive: false });

  var drag = null;
  pane.addEventListener("mousedown", function (event) {
    if (event.button !== 0) { return; }
    drag = { x: event.clientX, y: event.clientY,
             left: pane.scrollLeft, top: pane.scrollTop };
    pane.classList.add("grabbing");
    event.preventDefault();
  });
  window.addEventListener("mousemove", function (event) {
    if (!drag) { return; }
    pane.scrollLeft = drag.left - (event.clientX - drag.x);
    pane.scrollTop = drag.top - (event.clientY - drag.y);
  });
  window.addEventListener("mouseup", function () {
    drag = null;
    pane.classList.remove("grabbing");
  });

  draw();
}());
</script>
</body>
</html>
"""


@dataclass
class GraphNode:
    """One body in the graph, exported or not."""

    path: str
    name: str
    exported: bool = True
    note: str = ""

    @property
    def label_lines(self) -> list[str]:
        return [self.name] + ([self.note] if self.note else [])


@dataclass
class GraphEdge:
    """One joint, whether or not it made it into the model."""

    parent_path: str
    child_path: str
    joint_name: str
    joint_type: str | None = None
    limits: list[float] | None = None
    status: str = STATUS_OK
    note: str = ""

    @property
    def label(self) -> str:
        kind = self.joint_type or "rigid"
        label = f"{self.joint_name} ({kind}"
        if self.limits is not None:
            lower, upper = self.limits
            label += f" {lower:g}..{upper:g}"
        label += ")"
        if self.note:
            label += f" -- {self.note}"
        return label


@dataclass
class JointGraph:
    """
    The joint graph the exporter derived from the design, plus the problems
    it found while deriving it.

    Attributes:
        model_name: Name of the exported model, used for the file names.
        with_environment: Whether the model is wrapped in a ``model_root``
            body carrying the free joint. When False each root body gets its
            own ``<freejoint>`` instead, and the graph is drawn to match.
        nodes: Every body in the graph by occurrence path, including bodies
            that were not exported but are referenced by some joint.
        edges: Every joint found, including the dropped ones.
        root_paths: Bodies with no parent joint, which attach to the world.
        conflicts: ``(child path, kept joint, dropped joint)`` for each body
            that was the child of more than one joint.
        cycles: Each closed parent loop, as a list of occurrence paths.
    """

    model_name: str
    with_environment: bool = True
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    root_paths: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    # -- assembly ---------------------------------------------------------

    def add_node(
        self, occ_path: str, name: str, exported: bool = True, note: str = ""
    ) -> None:
        """
        Record a body. An exported body always wins over a placeholder added
        earlier by a skipped joint, so the order of calls does not matter.
        """
        existing = self.nodes.get(occ_path)
        if existing is not None and existing.exported:
            return
        self.nodes[occ_path] = GraphNode(occ_path, name, exported, note)

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    @property
    def has_problems(self) -> bool:
        return bool(self.conflicts or self.cycles or self.dropped_edges)

    @property
    def dropped_edges(self) -> list[GraphEdge]:
        return [edge for edge in self.edges if edge.status in _DROPPED]

    @property
    def cyclic_paths(self) -> set[str]:
        return {occ_path for cycle in self.cycles for occ_path in cycle}

    def problem_lines(self) -> list[str]:
        """One human-readable line per problem found, for logs and reports."""
        lines: list[str] = []
        for child, kept, dropped in self.conflicts:
            lines.append(
                f"'{self.display_name(child)}' is the child of more than one "
                f"joint: kept '{kept}', dropped '{dropped}'"
            )
        for cycle in self.cycles:
            names = " -> ".join(self.display_name(p) for p in cycle)
            lines.append(f"Parent loop: {names} -> {self.display_name(cycle[0])}")

        for edge in self.dropped_edges:
            if edge.status != STATUS_SKIPPED:
                continue
            # The bodies behind a skipped joint are not drawn, so this line is
            # the only place their names and the reason appear.
            ends = []
            for role, occ_path in (
                ("parent", edge.parent_path),
                ("child", edge.child_path),
            ):
                node = self.nodes.get(occ_path)
                if node is None or node.exported:
                    continue
                reason = f" ({node.note})" if node.note else ""
                ends.append(f"{role} '{node.name}' was not exported{reason}")
            lines.append(
                f"Joint '{edge.joint_name}' skipped: "
                + (" and ".join(ends) or edge.note)
            )
        return lines

    def display_name(self, occ_path: str) -> str:
        node = self.nodes.get(occ_path)
        return node.name if node else occ_path

    # -- rendering --------------------------------------------------------

    def _node_ids(self) -> dict[str, str]:
        """
        Stable, syntax-safe ids for the nodes that get drawn.

        Only exported bodies are drawn: the diagram is a picture of the model
        that comes out, so an occurrence with no body in it would be showing
        something that does not exist. Joints attached to one are reported in
        the problem list instead, and drop out of the diagram with it, since
        an edge needs both ends.

        Fusion occurrence paths carry ``+``, ``:`` and spaces, none of which
        are safe in Mermaid, so nodes are numbered instead and the readable
        name goes in the label. Sorted by name so the same design always
        renders the same file.
        """
        drawn = [node for node in self.nodes.values() if node.exported]
        ordered = sorted(drawn, key=lambda node: (node.name, node.path))
        return {node.path: f"n{i}" for i, node in enumerate(ordered)}

    def _world_edges(self, ids: dict[str, str]) -> list[tuple[str, str, str]]:
        """
        ``(source id, target id, label)`` for the attachments to the world.

        With an environment the model hangs off a single ``model_root`` body
        that carries the free joint; without one, every root body carries its
        own ``<freejoint>``.
        """
        roots = sorted(self.root_paths, key=self.display_name)
        if self.with_environment:
            edges = [(WORLD_ID, MODEL_ROOT_ID, "freejoint")]
            edges += [(MODEL_ROOT_ID, ids[p], "") for p in roots if p in ids]
            return edges
        return [(WORLD_ID, ids[p], "freejoint") for p in roots if p in ids]

    def to_mermaid(self) -> str:
        ids = self._node_ids()
        cyclic = self.cyclic_paths
        lines = ["graph TD"]

        # World / model_root scaffolding
        lines.append(f'    {WORLD_ID}(("worldbody"))')
        if self.with_environment:
            lines.append(f'    {MODEL_ROOT_ID}["model_root<br/>freejoint"]')

        for occ_path, node_id in ids.items():
            node = self.nodes[occ_path]
            label = "<br/>".join(_mermaid_text(line) for line in node.label_lines)
            lines.append(f'    {node_id}["{label}"]')

        link_styles: list[str] = []
        link_index = 0

        for source, target, label in self._world_edges(ids):
            arrow = f'-->|"{_mermaid_text(label)}"|' if label else "-->"
            lines.append(f"    {source} {arrow} {target}")
            link_index += 1

        for edge in sorted(
            self.edges,
            key=lambda e: (self.display_name(e.parent_path), e.joint_name),
        ):
            source = ids.get(edge.parent_path)
            target = ids.get(edge.child_path)
            if source is None or target is None:
                continue
            arrow = "-.->" if edge.status in _DROPPED else "-->"
            lines.append(
                f'    {source} {arrow}|"{_mermaid_text(edge.label)}"| {target}'
            )
            if edge.status in _DROPPED:
                link_styles.append(
                    f"    linkStyle {link_index} stroke:#d33,stroke-width:2px;"
                )
            elif edge.status == STATUS_RIGID:
                link_styles.append(
                    f"    linkStyle {link_index} stroke:#999,stroke-dasharray:6 3;"
                )
            link_index += 1

        lines += link_styles

        # Classes
        lines.append("    classDef root fill:#dceeff,stroke:#0366d6,stroke-width:2px;")
        lines.append(
            "    classDef cyclic fill:#ffdddd,stroke:#d33333,stroke-width:2px;"
        )

        roots = [
            ids[p] for p in sorted(self.root_paths, key=self.display_name) if p in ids
        ]
        cyclic_ids = [ids[p] for p in sorted(cyclic, key=self.display_name) if p in ids]

        if roots:
            lines.append(f"    class {','.join(roots)} root;")
        if cyclic_ids:
            lines.append(f"    class {','.join(cyclic_ids)} cyclic;")

        return "\n".join(lines)

    def to_dot(self) -> str:
        ids = self._node_ids()
        cyclic = self.cyclic_paths
        lines = [
            f'digraph "{_dot_text(self.model_name)}" {{',
            "    rankdir=TB;",
            '    graph [fontname="Helvetica", splines=true];',
            '    node [fontname="Helvetica", fontsize=10, shape=box, style=rounded];',
            '    edge [fontname="Helvetica", fontsize=9];',
            "",
            f'    {WORLD_ID} [label="worldbody", shape=doublecircle, '
            "style=solid, fontsize=9];",
        ]
        if self.with_environment:
            lines.append(
                f'    {MODEL_ROOT_ID} [label="model_root\\nfreejoint", '
                'style="rounded,filled", fillcolor="#dceeff"];'
            )
        lines.append("")

        for occ_path, node_id in ids.items():
            node = self.nodes[occ_path]
            label = "\\n".join(_dot_text(line) for line in node.label_lines)
            attrs = [f'label="{label}"']
            if occ_path in cyclic:
                attrs += [
                    'style="rounded,filled"',
                    'fillcolor="#ffdddd"',
                    'color="#d33333"',
                    "penwidth=2",
                ]
            elif occ_path in self.root_paths:
                attrs += [
                    'style="rounded,filled"',
                    'fillcolor="#dceeff"',
                    'color="#0366d6"',
                    "penwidth=2",
                ]
            lines.append(f"    {node_id} [{', '.join(attrs)}];")

        lines.append("")
        for source, target, label in self._world_edges(ids):
            attrs = f' [label="{_dot_text(label)}"]' if label else ""
            lines.append(f"    {source} -> {target}{attrs};")

        for edge in sorted(
            self.edges,
            key=lambda e: (self.display_name(e.parent_path), e.joint_name),
        ):
            source = ids.get(edge.parent_path)
            target = ids.get(edge.child_path)
            if source is None or target is None:
                continue
            attrs = [f'label="{_dot_text(edge.label)}"']
            if edge.status in _DROPPED:
                attrs += ["style=dashed", 'color="#d33333"', 'fontcolor="#d33333"']
            elif edge.status == STATUS_RIGID:
                attrs += ["style=dashed", 'color="#999999"', 'fontcolor="#666666"']
            lines.append(f"    {source} -> {target} [{', '.join(attrs)}];")

        lines.append("}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """The Mermaid diagram wrapped in a pasteable report."""
        lines = [
            f"# {self.model_name} joint graph",
            "",
            f"Generated by [Fusion2Mujoco]({config.REPO_URL}) "
            f"v{config.ADDIN_VERSION}.",
            "",
        ]
        lines += [f"- **{value}** {label}" for value, label in self.summary_counts()]

        problems = self.problem_lines()
        if problems:
            lines += ["", "## Problems", ""]
            lines += [f"{i}. {line}" for i, line in enumerate(problems, start=1)]
        else:
            lines += ["", "No problems found in the joint graph.", ""]

        lines += [
            "",
            "## Graph",
            "",
            "```mermaid",
            self.to_mermaid(),
            "```",
            "",
            "## Legend",
            "",
            "| Shown as | Meaning |",
            "| --- | --- |",
        ]
        lines += [f"| {shown} | {meaning} |" for shown, meaning in LEGEND]
        lines.append("")
        return "\n".join(lines)

    def summary_counts(self) -> list[tuple[str, str]]:
        """``(value, label)`` pairs summarising the graph, for the reports."""
        exported = sum(1 for node in self.nodes.values() if node.exported)
        kept = [edge for edge in self.edges if edge.status not in _DROPPED]
        movable = sum(1 for edge in kept if edge.status == STATUS_OK)
        counts = [
            (str(exported), _plural(exported, "body", "bodies")),
            (str(movable), "movable " + _plural(movable, "joint")),
            (
                str(len(kept) - movable),
                "rigid " + _plural(len(kept) - movable, "joint"),
            ),
            (
                str(len(self.root_paths)),
                "root " + _plural(len(self.root_paths), "body", "bodies"),
            ),
        ]
        dropped = self.dropped_edges
        if dropped:
            counts.append(
                (str(len(dropped)), "dropped " + _plural(len(dropped), "joint"))
            )
        return counts

    def to_html(self, source_note: str = "") -> str:
        """
        A self-contained HTML report that draws the graph with Mermaid.

        The diagram is scaled to fit the page on load and can then be zoomed,
        dragged, and flipped between top-down and left-right, because a real
        robot's graph is routinely wider than any page: a body with several
        children fans out, and nothing about the tree bounds that width.

        Args:
            source_note: Optional line shown in the footer, used to say where
                the companion ``.md`` and ``.dot`` files were written.

        Returns:
            str: A complete HTML document. Mermaid is loaded from a CDN; with
                no internet the page still shows the diagram source as text
                rather than failing silently.
        """
        problems = self.problem_lines()
        if self.conflicts or self.cycles:
            status_class, status_text = "bad", "This model cannot be exported as-is."
        elif problems:
            status_class, status_text = (
                "warn",
                "Exportable, but some joints were dropped.",
            )
        else:
            status_class, status_text = (
                "good",
                "No problems found in the joint graph.",
            )

        chips = "".join(
            f'<div class="chip"><span class="n">{_html_text(value)}</span>'
            f'<span class="l">{_html_text(label)}</span></div>'
            for value, label in self.summary_counts()
        )
        problems_html = ""
        if problems:
            items = "".join(f"<li>{_html_text(line)}</li>" for line in problems)
            problems_html = (
                f"<section><h2>Problems</h2>"
                f'<ol class="problems">{items}</ol></section>'
            )
        legend_rows = "".join(
            f"<tr><td>{_html_text(shown)}</td><td>{_html_text(meaning)}</td></tr>"
            for shown, meaning in LEGEND
        )
        footer = f"<p>{_html_text(source_note)}</p>" if source_note else ""

        replacements = {
            "@@TITLE@@": _html_text(self.model_name),
            "@@VERSION@@": _html_text(config.ADDIN_VERSION),
            "@@REPO@@": _html_text(config.REPO_URL),
            "@@STATUS_CLASS@@": status_class,
            "@@STATUS_TEXT@@": _html_text(status_text),
            "@@CHIPS@@": chips,
            "@@PROBLEMS@@": problems_html,
            "@@LEGEND@@": legend_rows,
            "@@FOOTER@@": footer,
            "@@MERMAID_CDN@@": MERMAID_CDN,
            "@@SOURCE@@": _html_text(self.to_mermaid()),
        }
        html = _HTML_TEMPLATE
        for token, value in replacements.items():
            html = html.replace(token, value)
        return html

    # -- output -----------------------------------------------------------

    def write(self, destination: str) -> list[str]:
        """
        Write both diagrams into ``destination``.

        Returns:
            list[str]: The paths written, HTML first since that is the one
                meant to be opened. A format that cannot be written is left
                out rather than raising, since this is a debugging aid and
                must never be the reason an export fails.
        """
        written: list[str] = []
        note = f"Also written to this folder: {destination}"
        outputs = (
            (self.model_name + HTML_SUFFIX, self.to_html(source_note=note)),
            (self.model_name + MERMAID_SUFFIX, self.to_markdown()),
            (self.model_name + DOT_SUFFIX, self.to_dot()),
        )
        for file_name, content in outputs:
            file_path = path.join(destination, file_name)
            try:
                with open(file_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                written.append(file_path)
            except Exception:
                continue
        return written
