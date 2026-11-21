"""
Minecraft Biome Application Operators.
Allows applying biome color palettes across active or selected Minecraft mesh objects.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty

from ..utils.materials.biome import (
    BIOME_ENUM_ITEMS,
    is_mtk_object,
    update_object_biome,
)


class MOZI_OT_set_object_biome(bpy.types.Operator):
    """Apply selected biome palette to the active or all selected Minecraft objects."""

    bl_idname = "mozi.set_object_biome"
    bl_label = "Apply Biome"
    bl_options = {"REGISTER", "UNDO"}

    biome_preset: EnumProperty(
        name="Biome",
        description="Choose the Minecraft Biome color palette preset",
        items=BIOME_ENUM_ITEMS,
        default="PLAINS",
    )
    apply_to_selected: BoolProperty(
        name="Apply to All Selected",
        description="Apply this biome preset to all selected Minecraft mesh objects",
        default=True,
    )

    def execute(self, context):
        target_objs = [
            obj for obj in context.selected_objects
            if is_mtk_object(obj)
        ] if self.apply_to_selected else ([context.object] if is_mtk_object(context.object) else [])

        if not target_objs:
            self.report({"WARNING"}, "No Minecraft mesh objects found in selection.")
            return {"CANCELLED"}

        src = context.object
        count = 0
        for obj in target_objs:
            if self.biome_preset == "CUSTOM" and src and src != obj:
                obj.mtk_biome_temp = src.mtk_biome_temp
                obj.mtk_biome_humidity = src.mtk_biome_humidity
                obj.mtk_biome_use_custom_grass = src.mtk_biome_use_custom_grass
                obj.mtk_biome_grass_color = src.mtk_biome_grass_color
                obj.mtk_biome_use_custom_foliage = src.mtk_biome_use_custom_foliage
                obj.mtk_biome_foliage_color = src.mtk_biome_foliage_color
                obj.mtk_biome_use_custom_dry_foliage = src.mtk_biome_use_custom_dry_foliage
                obj.mtk_biome_dry_foliage_color = src.mtk_biome_dry_foliage_color
                obj.mtk_biome_water_color = src.mtk_biome_water_color

            if update_object_biome(obj, self.biome_preset):
                count += 1

        self.report({"INFO"}, f"Applied '{self.biome_preset}' biome palette to {count} object(s).")
        return {"FINISHED"}


OPERATOR_CLASSES = (
    MOZI_OT_set_object_biome,
)
