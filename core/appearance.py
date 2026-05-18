from __future__ import annotations
from dataclasses import dataclass
import os
import adsk, adsk.fusion, adsk.core


@dataclass
class AppearanceData:
    """
    Appearance properties extracted from a Fusion 360 occurrence.

    Attributes:
        rgba: Normalized (r, g, b, a) tuple in 0.0–1.0, or None if unavailable.
        roughness: Surface roughness 0.0–1.0, or None if not provided.
        metallic: Metalness 0.0–1.0, or None if not provided.
        texture_src_path: Absolute path to a texture image file, or None.
        tex_scale: Real-world size of one texture tile in metres as (sx, sy),
            sourced from Fusion's ``texture_RealWorldScaleX/Y`` properties.
            Used to derive MuJoCo ``texrepeat``.  None if unavailable.
    """

    rgba: tuple[float, float, float, float] | None = None
    roughness: float | None = None
    metallic: float | None = None

    @property
    def needs_material(self) -> bool:
        """True when a named <material> asset is required (vs inline rgba on geom)."""
        return self.roughness is not None or self.metallic is not None

    def load(
        self, occurrence: adsk.fusion.Occurrence, bodies: list[adsk.fusion.BRepBody]
    ) -> AppearanceData | None:
        """
        Extract color, roughness, metalness, and optional texture path from
        the Fusion 360 appearance assigned to this occurrence.

        Returns:
            AppearanceData | None
        """
        appearance = self.get_fusion_appearance(occurrence, bodies)
        if appearance is None:
            return None

        props = appearance.appearanceProperties

        # Solid color
        # Check albedo slots in priority order; take the first ColorProperty found.
        for prop_id in (
            "opaque_albedo",
            "layered_diffuse",
            "metal_f0",
            "transparent_color",
        ):
            prop = props.itemById(prop_id)
            if prop is None:
                continue
            if prop.objectType == adsk.core.ColorProperty.classType():
                try:
                    c = prop.value
                    self.rgba = (c.red / 255.0, c.green / 255.0, c.blue / 255.0, 1.0)
                except Exception:
                    pass
            break

        # Roughness
        for prop_id in ("surface_roughness", "opaque_roughness"):
            prop = props.itemById(prop_id)
            if prop is not None:
                try:
                    self.roughness = float(prop.value)
                except Exception:
                    pass
                break

        # Metalness
        prop = props.itemById("surface_metalness")
        if prop is not None:
            try:
                self.metallic = float(prop.value)
            except Exception:
                pass

    def get_fusion_appearance(
        self, occurrence: adsk.fusion.Occurrence, bodies: list[adsk.fusion.BRepBody]
    ) -> adsk.core.Appearance | None:
        """
        Return the first valid Fusion Appearance form a component
        occurrence, or it's bodies

        Returns:
            adsk.core.Appearance | None
        """
        # Prefer appearance props on the component
        # NOTE: Fusion raises an exception when accessing appearance on an occurrence backed by
        #       a linked/external component — i.e. a component inserted from another Fusion document.
        #       It's a known API quirk mentioned in multiple Autodesk community threads
        try:
            if occurrence.appearance is not None:
                return occurrence.appearance
        except Exception:
            pass

        # Find the fist visible body with an appearance, and use that
        for body in bodies:
            if body.isLightBulbOn and body.appearance is not None:
                return body.appearance

        # Lastly, try the component material
        mat = occurrence.component.material
        if mat is not None and mat.appearance is not None:
            return mat.appearance
        return None
