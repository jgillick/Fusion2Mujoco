from __future__ import annotations
from typing import TYPE_CHECKING
import adsk, adsk.fusion

from . import utils
from .body import MjcfBody

if TYPE_CHECKING:
    from .exporter import Exporter


class MjcfBodyCollection:
    """
    An iterable collection of MjcfBody objects.
    """

    def __init__(self, items: list[MjcfBody] | None = None) -> None:
        self._items: list[MjcfBody] = items or []

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]

    @staticmethod
    def collect(
        exporter: Exporter, use_short_names: bool = False
    ) -> MjcfBodyCollection:
        """
        Build a collection from all visible occurrences that have direct
        BRepBodies in the design.

        Iterates over every occurrence in the root component, skipping those
        that are hidden or have no directly visible BRepBodies.

        Args:
            exporter (Exporter): The Exporter instance, used to access the
                root component and emit log messages.
            use_short_names (bool): When True, call ``shorten_names()`` on
                the collection before returning so each body uses the
                shortest name that remains unique.

        Returns:
            MjcfBodyCollection: The populated collection.
        """
        items: list[MjcfBody] = []
        name_to_tokens: dict[str, list[str]] = {}

        root: adsk.fusion.Component = exporter.root
        occs: list[adsk.fusion.Occurrence] = root.allOccurrences
        for occ in occs:
            comp = occ.component
            mjcf_body = MjcfBody(exporter, occ)

            if not occ.isLightBulbOn:
                continue
            if not comp.isBodiesFolderLightBulbOn:
                continue
            if len(mjcf_body.mesh.visible_bodies) == 0:
                continue

            # Track entity tokens per component name to detect distinct
            # components that happen to share the same name.
            tokens = name_to_tokens.setdefault(comp.name, [])
            if comp.entityToken not in tokens:
                tokens.append(comp.entityToken)

            # When multiple distinct components share a name, suffix the mesh
            # base name with a 1-based index to keep filenames unique.
            comp_base_name = comp.name
            if len(tokens) > 1:
                entity_index = tokens.index(comp.entityToken) + 1
                comp_base_name += f"_{entity_index}"

            mjcf_body.mesh.base_name = comp_base_name
            items.append(mjcf_body)

        collection = MjcfBodyCollection(items)
        if use_short_names:
            collection.shorten_names()
        return collection

    def shorten_names(self) -> None:
        """
        Assign each body a ``short_name`` using the minimum set of path
        segments needed to keep all names unique.

        All names remain unique; only the segments strictly necessary to
        distinguish each individual name from every other are retained.

        The algorithm uses per-name position sets rather than a single
        global set. This prevents a segment that is required to distinguish
        one group of names (e.g. Hip vs Tibia for Motor entries) from being
        unnecessarily injected into unrelated names (e.g. Foot entries where
        the joint-level segment adds no information).

        Steps:

        1. Split each full name by ``_`` into a segment list.
        2. Pad all lists to the same length with ``None`` for comparison.
        3. Seed each name's retained-position set with its last segment.
        4. Repeatedly find collision groups (names that currently share the
           same projected short name). For each group, add the single
           position that produces the most distinct values within the group
           (ties broken by earliest position) to every member's set.
        5. Repeat until no collisions remain or no progress can be made.
        6. Write the joined short name back onto each body's
           ``short_name`` attribute.
        """
        if not self._items:
            return

        full_names = [
            utils.get_valid_filename(occ.occurrence.fullPathName) for occ in self._items
        ]
        seg_lists: list[list[str]] = [name.split("_") for name in full_names]
        max_len = max(len(s) for s in seg_lists)

        # Pad shorter lists with None so all rows have the same width.
        padded: list[list[str | None]] = [
            segs + [None] * (max_len - len(segs)) for segs in seg_lists
        ]
        n = len(padded)

        # Per-name retained positions, each seeded with the name's last segment.
        kept: list[set[int]] = [{len(segs) - 1} for segs in seg_lists]

        def _short_name(i: int) -> str:
            segs = seg_lists[i]
            return "_".join(segs[p] for p in sorted(kept[i]) if p < len(segs))

        while True:
            # Group indices by their current projected short name.
            groups: dict[str, list[int]] = {}
            for i in range(n):
                groups.setdefault(_short_name(i), []).append(i)

            collision_groups = [g for g in groups.values() if len(g) > 1]
            if not collision_groups:
                break

            progress = False
            for group in collision_groups:
                # Positions already used by any member of this group.
                used = set().union(*(kept[i] for i in group))
                candidates = [p for p in range(max_len) if p not in used]
                if not candidates:
                    continue

                # Pick the position that creates the most distinct values
                # within this group; break ties by preferring the earliest.
                best_pos = min(
                    candidates,
                    key=lambda p: (-len({padded[i][p] for i in group}), p),
                )
                if len({padded[i][best_pos] for i in group}) < 2:
                    continue  # no position can split this group

                for i in group:
                    kept[i].add(best_pos)
                progress = True

            if not progress:
                break  # remaining collisions are unresolvable (truly identical names)

        # Assign final short names.
        for i, occ in enumerate(self._items):
            occ.short_name = _short_name(i)
