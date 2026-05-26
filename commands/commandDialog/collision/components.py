from __future__ import annotations

from dataclasses import dataclass
import adsk.fusion

from ..constants import (
    ATTR_COMPONENT_COLLISION,
    ATTR_COMPONENT_THRESHOLD,
    ATTR_GROUP_NAMESPACE,
)
from .config import (
    THRESHOLD_MIN,
    THRESHOLD_MAX,
)


@dataclass(frozen=True)
class ComponentRow:
    """Configuration for a single component, as it should be displayed in the component table"""

    component: adsk.fusion.Component
    entity_token: str
    display_name: str
    first_occurrence_path: str


def get_component_list(root: adsk.fusion.Component) -> list[ComponentRow]:
    """Unique components that would receive a mesh in export (see MjcfBodyCollection)."""
    seen_tokens: set[str] = set()
    rows: list[ComponentRow] = []

    for occ in root.allOccurrences:
        comp = occ.component
        if comp.entityToken in seen_tokens:
            continue
        if not occ.isLightBulbOn:
            continue
        if not comp.isBodiesFolderLightBulbOn:
            continue
        if not any(b.isLightBulbOn for b in comp.bRepBodies):
            continue

        seen_tokens.add(comp.entityToken)
        rows.append(
            ComponentRow(
                component=comp,
                entity_token=comp.entityToken,
                display_name=comp.name,
                first_occurrence_path=occ.fullPathName,
            )
        )

    rows.sort(key=lambda r: r.display_name.lower())
    return rows


def read_component_settings(
    component: adsk.fusion.Component,
) -> (bool, float | None):
    """Read persisted collision settings from a component."""
    attrs = component.attributes

    collision = True
    collision_attr = attrs.itemByName(ATTR_GROUP_NAMESPACE, ATTR_COMPONENT_COLLISION)
    if collision_attr is not None and collision_attr.value.strip():
        collision = collision_attr.value.strip().lower() != "false"

    threshold: float | None = None
    threshold_attr = attrs.itemByName(ATTR_GROUP_NAMESPACE, ATTR_COMPONENT_THRESHOLD)
    if threshold_attr is not None and threshold_attr.value.strip():
        threshold = parse_threshold_value(threshold_attr.value)

    return (collision, threshold)


def write_component_settings(
    component: adsk.fusion.Component,
    enabled: bool,
    threshold: float | None,
) -> None:
    """
    Write or clear collision attributes on a component.

    When inherit is True, stored overrides are removed so export uses dialog defaults.
    """
    attrs = component.attributes
    enabled_value = "true" if enabled else "false"
    attrs.add(ATTR_GROUP_NAMESPACE, ATTR_COMPONENT_COLLISION, enabled_value)
    if enabled and threshold is not None:
        attrs.add(ATTR_GROUP_NAMESPACE, ATTR_COMPONENT_THRESHOLD, f"{threshold:.6g}")


def parse_threshold_value(value: str) -> float | None:
    try:
        threshold = float(value.strip())
    except (TypeError, ValueError):
        return None
    if THRESHOLD_MIN <= threshold <= THRESHOLD_MAX:
        return threshold
    return None
