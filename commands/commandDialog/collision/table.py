"""
Per-component collision table for the export dialog.
"""

from __future__ import annotations

from dataclasses import dataclass

import adsk.core
import adsk.fusion

from ..constants import (
    ATTR_GROUP_NAMESPACE,
    ATTR_PER_COMPONENT_COLLISION,
)
from ..logger import Logger
from .components import (
    ComponentRow,
    get_component_list,
    read_component_settings,
    write_component_settings,
)
from .config import (
    THRESHOLD_DEFAULT,
    THRESHOLD_MAX,
    THRESHOLD_MIN,
)


@dataclass
class CollisionTableRow:
    entity_token: str
    component: adsk.fusion.Component
    enable_input: adsk.core.BoolValueCommandInput | None = None
    name_input: adsk.core.StringValueCommandInput | None = None
    threshold_input: adsk.core.FloatSpinnerCommandInput | None = None


class CollisionTable:
    """Builds and manages the per-component collision table."""

    def __init__(
        self,
        parent_inputs: adsk.core.CommandInputs,
        design: adsk.fusion.Design,
        settings: dict,
        logger: Logger | None = None,
    ):
        self.design = design
        self.settings = settings
        self.parent_inputs = parent_inputs
        self.table_inputs: adsk.core.CommandInputs = None
        self.logger = logger
        self.component_list: list[ComponentRow] = []
        self.rows: list[CollisionTableRow] = []
        self.per_component_default_value: bool = False

        self.per_component_input: adsk.core.BoolValueCommandInput | None = None
        self.table_group: adsk.core.GroupCommandInput | None = None
        self.table: adsk.core.TableCommandInput | None = None

        self.load()

    @property
    def is_per_component_enabled(self) -> bool:
        return bool(self.per_component_input and self.per_component_input.value)

    def build(self) -> bool:
        """Build the component table."""

        self.component_list = get_component_list(self.design.rootComponent)
        if not self.component_list:
            self.rows = []
            return False

        # Enable per-component collision settings
        self.per_component_input = self.parent_inputs.addBoolValueInput(
            "per_component_collision_settings",
            "Per-component settings",
            True,
            "",
            self.per_component_default_value,
        )
        self.per_component_input.tooltip = "Override collision settings per component"
        self.per_component_input.tooltipDescription = (
            "When enabled, configure collision settings per component."
        )

        # Create section group around the table
        self.table_group = self.parent_inputs.addGroupCommandInput(
            "collision_table_group", "Component collision settings"
        )
        self.table_group.isExpanded = True
        self.table_group.isVisible = False

        # Create table
        self.table = self.table_group.children.addTableCommandInput(
            "collision_components_table", "Components", 3, "1:4:1"
        )
        self.table_inputs = adsk.core.CommandInputs.cast(self.table.commandInputs)
        self.table.maximumVisibleRows = 12
        self.table.minimumVisibleRows = 1
        self.build_rows()
        self.show_table(self.per_component_default_value)

        return True

    def load(self) -> None:
        """Load the per components enabled state from the design attributes."""
        attr = self.design.attributes.itemByName(
            ATTR_GROUP_NAMESPACE, ATTR_PER_COMPONENT_COLLISION
        )
        if attr is not None and attr.value == "true":
            self.per_component_default_value = True

    def save(self) -> None:
        """Retain the input values for the next session."""
        # Global enable value
        value = "true" if self.is_per_component_enabled else "false"
        self.design.attributes.add(
            ATTR_GROUP_NAMESPACE, ATTR_PER_COMPONENT_COLLISION, value
        )

        # Per-component settings
        for row in self.rows:
            enabled = row.enable_input.value
            threshold = row.threshold_input.value
            write_component_settings(
                row.component,
                enabled=enabled,
                threshold=threshold if enabled else None,
            )

    def build_rows(self, global_threshold_value: float | None = None) -> None:
        """Build the rows of the table."""

        # Don't build if per-component settings are not enabled
        if not self.is_per_component_enabled:
            return

        self.rows = []
        for comp in self.component_list:
            self.add_row(comp, global_threshold_value)

    def add_row(
        self, comp: ComponentRow, global_threshold_value: float | None = None
    ) -> CollisionTableRow:
        row_index = self.table.rowCount
        name_id = f"collision_table_{row_index}_name"
        enable_id = f"collision_table_{row_index}_enabled"
        threshold_id = f"collision_table_{row_index}_threshold"

        # Get saved settings
        (saved_collision_enabled, saved_threshold) = read_component_settings(
            comp.component
        )
        default_threshold = global_threshold_value or self.settings.get(
            "convex_threshold", THRESHOLD_DEFAULT
        )

        # Enable collision mesh checkbox
        enable_input = self.table_inputs.addBoolValueInput(
            enable_id,
            "Collision",
            True,
            "",
            saved_collision_enabled,
        )
        self.table.addCommandInput(enable_input, row_index, 0)

        # Component name
        name_input = self.table_inputs.addStringValueInput(
            name_id, "Component", comp.display_name
        )
        name_input.isReadOnly = True
        name_input.isFullWidth = True
        name_input.tooltip = f"e.g. {comp.first_occurrence_path}"
        self.table.addCommandInput(name_input, row_index, 1)

        # CoACD threshold
        threshold_input = self.table_inputs.addFloatSpinnerCommandInput(
            threshold_id,
            "Threshold",
            "",
            THRESHOLD_MIN,
            THRESHOLD_MAX,
            0.05,
            saved_threshold if saved_threshold is not None else default_threshold,
        )
        self.table.addCommandInput(threshold_input, row_index, 2)

        # Save row configuration
        row = CollisionTableRow(
            entity_token=comp.entity_token,
            component=comp.component,
            name_input=name_input,
            enable_input=enable_input,
            threshold_input=threshold_input,
        )
        self.rows.append(row)
        return row

    def set_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the table."""
        self.per_component_input.isEnabled = enabled
        self.show_table(enabled)

    def show_table(
        self, show: bool, global_threshold_value: float | None = None
    ) -> None:
        """Show or hide the table."""
        if self.table_group is None:
            return
        self.table_group.isVisible = show

        # Build rows, if needed
        if show and not self.rows:
            self.build_rows(global_threshold_value)

    def read_component_collision_settings(self) -> dict[str, float | None] | None:
        """Read the data from the table inputs."""

        enabled = self.is_per_component_enabled
        if not enabled:
            return None

        thresholds: dict[str, float] = {}
        for row in self.rows:
            thresholds[row.entity_token] = None
            if row.enable_input.value:
                thresholds[row.entity_token] = row.threshold_input.value
        return thresholds

    def handle_input_changed(
        self,
        input: adsk.core.CommandInput,
        global_threshold: float,
    ) -> None:
        if input.id == self.per_component_input.id:
            self.show_table(self.is_per_component_enabled, global_threshold)

        for row in self.rows:
            if input.id == row.enable_input.id:
                row.threshold_input.isEnabled = row.enable_input.value
                row.name_input.isEnabled = row.enable_input.value

    def validate(self) -> bool:
        """Validate the table inputs."""
        if not self.is_per_component_enabled:
            return True

        for row in self.rows:
            enable_input = row.enable_input
            threshold_input = row.threshold_input
            if (
                enable_input.isEnabled
                and enable_input.value
                and threshold_input.isEnabled
            ):
                if not (THRESHOLD_MIN <= threshold_input.value <= THRESHOLD_MAX):
                    return False
        return True
