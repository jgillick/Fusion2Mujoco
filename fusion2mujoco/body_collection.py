from __future__ import annotations
from collections import defaultdict
from typing import TYPE_CHECKING
import adsk, adsk.fusion

from .body import MjcfBody

if TYPE_CHECKING:
    from .exporter import Exporter
    from .naming import OccurrenceNamer


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
                shortest name that is still unique.

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
            collection.shorten_names(exporter.namer)
        return collection

    def shorten_names(self, namer: OccurrenceNamer) -> None:
        """
        Assign each body a ``short_name``: its own component name plus only
        the parent path segments needed to make it unique.

        For example ``Robot_Leg_Hip_Motor``, ``Robot_Leg_Tibia_Motor`` and
        ``Robot_Leg_Foot`` become ``Hip_Motor``, ``Tibia_Motor`` and ``Foot``.

        Segments are the occurrences in the body's assembly path, so a
        component name containing underscores (``Hip_Motor``) is never split
        apart, and the body's own component name is always kept in full.

        Each body keeps its own set of retained segment positions rather than
        sharing one global set. That way a segment needed to tell two bodies
        apart (``Hip`` vs ``Tibia`` for the motors above) is not forced into
        unrelated names (``Foot``).

        Algorithm:

        1. Start each body with only its last segment (its component name).
        2. Group bodies whose current short names collide.
        3. For each group, add the segment position that best splits the
           group (most distinct values, ties going to the earliest position)
           to every member of the group.
        4. Repeat until nothing collides, or no position can split a group
           (which only happens for truly identical paths).

        Args:
            namer (OccurrenceNamer): Splits each body's path into
                filename-safe segments.
        """
        segments = [namer.segments(body.full_name) for body in self._items]
        kept_positions: list[set[int]] = [{len(segs) - 1} for segs in segments]

        def segment_at(i: int, position: int) -> str | None:
            """The segment of body ``i`` at ``position``, or None past the end."""
            return segments[i][position] if position < len(segments[i]) else None

        def short_name(i: int) -> str:
            values = (segment_at(i, p) for p in sorted(kept_positions[i]))
            return "_".join(value for value in values if value is not None)

        def distinct_values(group: list[int], position: int) -> int:
            """How many different segments the group's bodies have at ``position``."""
            return len({segment_at(i, position) for i in group})

        while True:
            groups: dict[str, list[int]] = defaultdict(list)
            for i in range(len(segments)):
                groups[short_name(i)].append(i)
            collisions = [group for group in groups.values() if len(group) > 1]
            if not collisions:
                break

            progress = False
            for group in collisions:
                # A position every member already keeps can't tell them apart.
                # (One kept by only some members can, e.g. when the same
                # component sits at different depths.)
                used = set.intersection(*(kept_positions[i] for i in group))
                longest = max(len(segments[i]) for i in group)
                candidates = [p for p in range(longest) if p not in used]
                best = min(
                    candidates,
                    key=lambda p: (-distinct_values(group, p), p),
                    default=None,
                )
                if best is None or distinct_values(group, best) < 2:
                    continue  # nothing left can split this group

                for i in group:
                    kept_positions[i].add(best)
                progress = True

            if not progress:
                break

        for i, body in enumerate(self._items):
            body.short_name = short_name(i)
