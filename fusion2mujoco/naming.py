"""
Turn Fusion 360 occurrence paths into clean names for MJCF bodies.

Fusion names every occurrence ``<component name>:<instance number>`` and
builds an occurrence's full path by joining it to its ancestors with ``+``::

    "Robot:1+Leg:2+Motor:1"

Every prefix of that path (``"Robot:1"``, ``"Robot:1+Leg:2"``) is itself the
full path of an ancestor occurrence.
"""

from __future__ import annotations
from collections import Counter
from typing import TYPE_CHECKING, Iterable
import re

if TYPE_CHECKING:
    import adsk.fusion

# Component names may contain "+" themselves, so paths are split on the
# ":<number>+" boundary rather than on "+" alone.
_PATH_SEPARATOR = re.compile(r":(\d+)\+")
_INSTANCE_SUFFIX = re.compile(r":\d+$")
_UNSAFE_CHARS = re.compile(r"(?u)[^-\w.]")


def split_path(full_path: str) -> list[tuple[str, str]]:
    """
    Split a Fusion occurrence path into ``(component name, instance)`` pairs.

    Example::

        "Robot:1+Leg:2+Motor:1"  →  [("Robot", "1"), ("Leg", "2"), ("Motor", "1")]
    """
    tokens = _PATH_SEPARATOR.split(f"{full_path}+")
    return [(tokens[i], tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)]


def path_prefixes(full_path: str) -> list[str]:
    """
    The full path of every occurrence along ``full_path``, from the top-level
    one down to ``full_path`` itself.

    Example::

        "Robot:1+Leg:2+Motor:1"  →  ["Robot:1", "Robot:1+Leg:2", "Robot:1+Leg:2+Motor:1"]
    """
    prefixes = []
    prefix = ""
    for name, instance in split_path(full_path):
        prefix = f"{prefix}+{name}:{instance}" if prefix else f"{name}:{instance}"
        prefixes.append(prefix)
    return prefixes


def _drop_instance(occurrence_path: str) -> str:
    """
    Remove the final instance number from an occurrence path. Sibling
    occurrences (same component, same parent) all produce the same result.

    Example::

        "Robot:1+Leg:2"  →  "Robot:1+Leg"
    """
    return _INSTANCE_SUFFIX.sub("", occurrence_path)


class OccurrenceNamer:
    """
    Builds filename-safe names from Fusion occurrence paths.

    Each occurrence along the path becomes one name segment, and the segments
    are joined with ``_``. A segment is the component name, plus
    ``-<instance>`` only when the occurrence has siblings, i.e. when the design
    holds more than one instance of that component under the same parent.

    For a design with one ``Robot`` containing two ``Leg``s, each with one
    ``Hip Motor``::

        "Robot:1+Leg:2+Hip Motor:1"  →  segments ["Robot", "Leg-2", "Hip-Motor"]
                                     →  name     "Robot_Leg-2_Hip-Motor"

    ``Robot`` and ``Hip Motor`` have no siblings so their instance numbers are
    dropped, while ``Leg`` keeps its number. Names are made filename-safe.
    """

    def __init__(self, occurrences: Iterable[adsk.fusion.Occurrence] = ()) -> None:
        """
        Args:
            occurrences: Every occurrence in the design (typically
                ``root.allOccurrences``). These are only used to find out
                which occurrences have siblings.
        """
        # How many occurrences share each instance-less path. For the design
        # above this holds {"Robot": 1, "Robot:1+Leg": 2, ...}.
        self._instances_per_path: Counter[str] = Counter(
            _drop_instance(occurrence.fullPathName) for occurrence in occurrences
        )

    def has_siblings(self, occurrence_path: str) -> bool:
        """
        Whether the design holds other instances of this occurrence's
        component under the same parent. For example ``"Robot:1+Leg:2"`` has
        siblings when ``"Robot:1+Leg:1"`` also exists.
        """
        return self._instances_per_path[_drop_instance(occurrence_path)] > 1

    def segments(self, full_path: str) -> list[str]:
        """
        One filename-safe segment per occurrence along ``full_path``.

        Example, for the design described in the class docstring::

            "Robot:1+Leg:2+Hip Motor:1"  →  ["Robot", "Leg-2", "Hip-Motor"]
        """
        segments = []
        for occurrence_path in path_prefixes(full_path):
            name, instance = split_path(occurrence_path)[-1]
            if self.has_siblings(occurrence_path):
                name = f"{name}-{instance}"
            safe_filename = _UNSAFE_CHARS.sub("", name.strip().replace(" ", "-"))
            segments.append(safe_filename)
        return segments

    def name(self, full_path: str) -> str:
        """
        A single filename-safe name for ``full_path``: its segments joined
        with ``_``.

        Example, for the design described in the class docstring::

            "Robot:1+Leg:2+Hip Motor:1"  →  "Robot_Leg-2_Hip-Motor"
        """
        return "_".join(self.segments(full_path))
