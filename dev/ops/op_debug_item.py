"""
MoziToolKit Dev Operator: Load & Inspect Prebaked 3D Item Models (Single & Full Showcase Gallery).
Retrieves prebaked item models from .mtkcache (BakedModelDatabase) with sharp PBR Atlas materials.
"""

from __future__ import annotations

import logging
from typing import Optional
import bpy

from ... import bridge
from ...bridge.engine import get_libmtk
from ...bridge.mesh import inject_mesh_data
from ...utils.assets.guard import (
    can_execute_asset_pipeline,
    check_asset_cache_status,
    ensure_pipeline_assets_ready_sync,
)
from ...utils.system.dependencies import get_prefs
from ...utils.materials.builder.world_binding import ensure_world_materials

logger = logging.getLogger("MoziToolKit.Dev.Item")

PRESET_ITEMS = [
    ("diamond_sword", "Diamond Sword", "Iconic weapon with detailed edge bevels and pommel"),
    ("golden_apple", "Golden Apple", "Circular fruit with stem and leaf silhouette"),
    ("enchanted_golden_apple", "Enchanted Golden Apple", "Circular fruit with purple glint"),
    ("iron_pickaxe", "Iron Pickaxe", "T-shaped harvesting tool with diagonal handle"),
    ("netherite_axe", "Netherite Axe", "Heavy battle axe blade with sharp bevels"),
    ("bow", "Bow", "Non-symmetric bow curvature with hollow middle interior"),
    ("potion", "Potion Bottle", "Bottle neck, cork stopper, and liquid body"),
    ("netherite_chestplate", "Netherite Chestplate", "Armor plate with shoulder curves and neck hole"),
    ("clock", "Clock", "Ornate round golden frame with dial"),
    ("compass", "Compass", "Round compass casing with dial needle"),
    ("custom", "Custom Item ID...", "Enter arbitrary vanilla item ID"),
]


class MOZI_OT_dev_load_debug_item(bpy.types.Operator):
    """Load prebaked 3D item models from the asset cache into the active scene (Single or All Tiled Gallery)"""

    bl_idname = "mozi.dev_load_debug_item"
    bl_label = "Load Debug Items"
    bl_description = "Generate prebaked 3D item models with PBR Atlas material from active cache into active scene"
    bl_options = {"REGISTER", "UNDO"}

    generation_mode: bpy.props.EnumProperty(
        name="Mode",
        description="Choose whether to tile all prebaked items into a showcase gallery or load a single item",
        items=[
            ("ALL", "All Items (Tiled Gallery)", "Tile all prebaked items into an organized showcase grid"),
            ("SINGLE", "Single Item", "Load a single specific item"),
        ],
        default="ALL",
    )

    columns: bpy.props.IntProperty(
        name="Columns",
        description="Number of item columns per row in the gallery grid",
        default=36,
        min=4,
        max=128,
    )

    item_filter: bpy.props.EnumProperty(
        name="Item Filter",
        description="Filter item models in gallery (pure 2D items vs all models including block items)",
        items=[
            ("ITEMS_ONLY", "Items Only (2D Flat)", "Only 2D extruded item models from textures/item/ (swords, tools, food, etc.)"),
            ("ALL", "All Items & Blocks", "All models from models/item/ (including 3D block-items like stone, lantern, etc.)"),
            ("BLOCKS_ONLY", "Block Items Only", "Only 3D block-items from models/item/"),
        ],
        default="ITEMS_ONLY",
    )

    spacing: bpy.props.FloatProperty(
        name="Spacing",
        description="Distance between adjacent items in blocks",
        default=1.5,
        min=0.5,
        max=10.0,
        step=10,
    )

    layout_plane: bpy.props.EnumProperty(
        name="Layout Plane",
        description="Orientation plane for tiling items",
        items=[
            ("HORIZONTAL", "Horizontal (Ground)", "Tile items flat across ground (XY plane)"),
            ("VERTICAL", "Vertical (Wall)", "Tile items upright on vertical gallery wall (XZ plane)"),
        ],
        default="HORIZONTAL",
    )

    center_grid: bpy.props.BoolProperty(
        name="Center Grid",
        description="Center the gallery grid around origin / 3D cursor",
        default=True,
    )

    item_preset: bpy.props.EnumProperty(
        name="Preset Item",
        description="Choose a Minecraft vanilla item preset to load",
        items=PRESET_ITEMS,
        default="diamond_sword",
    )

    custom_id: bpy.props.StringProperty(
        name="Custom Item ID",
        description="Item identifier (e.g. stick, golden_sword, apple)",
        default="stick",
    )

    weld_vertices: bpy.props.BoolProperty(
        name="Weld Vertices",
        description="Weld adjacent coplanar vertices into manifold topology",
        default=True,
    )

    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Visual display scale factor",
        default=1.0,
        min=0.1,
        max=10.0,
    )

    location_mode: bpy.props.EnumProperty(
        name="Placement",
        description="Where to place the generated item model in the scene",
        items=[
            ("ORIGIN", "World Origin (0, 0, 0)", "Place at scene origin"),
            ("CURSOR", "3D Cursor", "Place at active 3D Cursor location"),
        ],
        default="ORIGIN",
    )

    @classmethod
    def poll(cls, context):
        prefs = get_prefs(context)
        if prefs:
            try:
                can_run, reason = can_execute_asset_pipeline(prefs)
                if not can_run and hasattr(cls, "poll_message_set"):
                    cls.poll_message_set(reason)
                return can_run
            except Exception:
                pass
        return True

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "generation_mode", expand=True)

        if self.generation_mode == "ALL":
            box = layout.box()
            box.label(text="Gallery Layout Settings", icon="GRID")
            box.prop(self, "item_filter")
            box.prop(self, "columns")
            box.prop(self, "spacing")
            box.prop(self, "layout_plane")
            box.prop(self, "center_grid")
        else:
            box = layout.box()
            box.label(text="Single Item Selection", icon="TOOL_SETTINGS")
            box.prop(self, "item_preset")
            if self.item_preset == "custom":
                box.prop(self, "custom_id")

        layout.separator(factor=0.5)
        layout.prop(self, "weld_vertices")
        layout.prop(self, "scale")
        layout.prop(self, "location_mode")

    def execute(self, context):
        mtk = get_libmtk()
        if mtk is None:
            self.report({"ERROR"}, "libmtk_py native engine is not available")
            return {"CANCELLED"}

        testing = getattr(getattr(mtk, "unstable", None), "testing", None)
        if testing is None:
            self.report({"ERROR"}, "libmtk_py.unstable.testing module is unavailable")
            return {"CANCELLED"}

        prefs = get_prefs(context)

        # 1. Base pack gate
        can_run, reason = can_execute_asset_pipeline(prefs)
        if not can_run:
            self.report({"WARNING"}, reason or "Valid Minecraft Vanilla Client JAR base pack is required.")
            return {"CANCELLED"}

        # 2. Asset cache gate: verify and ensure precompiled .mtkcache is ready
        status = check_asset_cache_status(prefs)
        if status in ("NO_PACKS", "NO_VANILLA"):
            self.report({"WARNING"}, reason or "Valid Minecraft Vanilla Client JAR base pack is required.")
            return {"CANCELLED"}
        if status in ("MISSING", "STALE"):
            try:
                self.report(
                    {"INFO"},
                    f"Asset cache is {status.lower()}; automatically precompiling before loading items...",
                )
                success, err_msg = ensure_pipeline_assets_ready_sync(prefs)
                if not success:
                    self.report({"ERROR"}, f"Asset precompilation failed: {err_msg}")
                    return {"CANCELLED"}
            except Exception as e:
                self.report({"ERROR"}, f"Failed to precompile asset cache: {e}")
                return {"CANCELLED"}

        # 3. Load active prebaked model database and atlas
        model_db = bridge.load_baked_model_database(prefs)
        if model_db is None:
            self.report({"ERROR"}, "Failed loading prebaked model database from asset cache.")
            return {"CANCELLED"}

        atlas = bridge.load_baked_atlas_from_cache(prefs)

        # Base placement coordinates
        base_loc = (0.0, 0.0, 0.0)
        if self.location_mode == "CURSOR" and context and context.scene:
            cursor_loc = context.scene.cursor.location
            base_loc = (cursor_loc.x, cursor_loc.y, cursor_loc.z)

        # -------------------------------------------------------------
        # Branch A: Full Showcase Gallery (Tiled Grid of All Items)
        # -------------------------------------------------------------
        if self.generation_mode == "ALL":
            is_vertical = (self.layout_plane == "VERTICAL")
            try:
                mesh_data, textures, total_items = testing.model_database_build_items_gallery(
                    model_db,
                    self.columns,
                    self.spacing,
                    is_vertical,
                    self.item_filter.lower(),
                    self.weld_vertices,
                )
            except Exception as e:
                self.report({"ERROR"}, f"Failed building items gallery mesh: {e}")
                return {"CANCELLED"}

            if mesh_data is None or mesh_data.is_empty() or total_items == 0:
                self.report({"WARNING"}, "No prebaked items found in active asset database.")
                return {"CANCELLED"}

            mesh_name = "MTK_Debug_Items_Gallery"
            blender_mesh = bpy.data.meshes.new(mesh_name)

            inject_mesh_data(
                mesh_data=mesh_data,
                mesh_or_obj=blender_mesh,
                update_topology=True,
                update_normals=True,
                inject_attributes=True,
                shade_smooth=False,
            )

            obj_name = "MTK_Debug_Items_Gallery"
            obj = bpy.data.objects.new(obj_name, blender_mesh)

            # Center grid calculation
            offset_x, offset_y, offset_z = 0.0, 0.0, 0.0
            if self.center_grid:
                grid_cols = min(self.columns, total_items)
                grid_rows = (total_items + self.columns - 1) // self.columns
                extent_x = (grid_cols - 1) * self.spacing
                extent_step = (grid_rows - 1) * self.spacing
                if is_vertical:
                    offset_x = -extent_x * 0.5
                    offset_z = extent_step * 0.5
                else:
                    offset_x = -extent_x * 0.5
                    offset_y = extent_step * 0.5

            final_loc = (
                base_loc[0] + offset_x,
                base_loc[1] + offset_y,
                base_loc[2] + offset_z,
            )
            obj.location = final_loc

            if abs(self.scale - 1.0) > 1e-4:
                obj.scale = (self.scale, self.scale, self.scale)

            target_col = context.collection if context and context.collection else bpy.context.scene.collection
            target_col.objects.link(obj)

            # Bind Official PBR Atlas Materials
            if atlas is not None:
                try:
                    used_chunk_ids = mesh_data.used_materials()
                    ensure_world_materials(
                        world_obj=obj,
                        prefs=prefs,
                        atlas=atlas,
                        used_chunk_ids=used_chunk_ids,
                    )
                except Exception as e:
                    logger.warning(f"Failed binding atlas materials for items gallery: {e}")

            try:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                if context:
                    context.view_layer.objects.active = obj
            except Exception:
                pass

            obj["mtk:is_debug_item"] = True
            obj["mtk:is_debug_items_gallery"] = True
            obj["mtk:item_count"] = total_items
            obj["mtk:total_items"] = total_items

            q_count = mesh_data.quad_count
            v_count = mesh_data.vertex_count
            self.report(
                {"INFO"},
                f"Loaded All {total_items} Prebaked Items in {self.columns} cols: {q_count:,} quads, {v_count:,} verts",
            )
            return {"FINISHED"}

        # -------------------------------------------------------------
        # Branch B: Single Item Loading
        # -------------------------------------------------------------
        raw_name = self.custom_id if self.item_preset == "custom" else self.item_preset
        clean_name = raw_name.strip()
        if clean_name.startswith("minecraft:"):
            clean_name = clean_name[len("minecraft:") :]
        if clean_name.startswith("item/"):
            clean_name = clean_name[len("item/") :]
        if clean_name.endswith(".png"):
            clean_name = clean_name[:-4]

        candidates = [
            f"minecraft:{clean_name}",
            f"minecraft:item/{clean_name}",
            f"item/{clean_name}",
            clean_name,
        ]

        mesh_res = None
        for cand in candidates:
            mesh_res = testing.model_database_get_mesh(
                model_db, cand, False, self.weld_vertices
            )
            if mesh_res is not None:
                break

        if mesh_res is None:
            self.report({"WARNING"}, f"Item '{clean_name}' not found in prebaked model database.")
            return {"CANCELLED"}

        mesh_data, textures = mesh_res
        if mesh_data is None or mesh_data.is_empty():
            self.report({"WARNING"}, f"Baked item '{clean_name}' generated no geometry")
            return {"CANCELLED"}

        mesh_name = f"MTK_Item_{clean_name}"
        blender_mesh = bpy.data.meshes.new(mesh_name)

        inject_mesh_data(
            mesh_data=mesh_data,
            mesh_or_obj=blender_mesh,
            update_topology=True,
            update_normals=True,
            inject_attributes=True,
            shade_smooth=False,
        )

        obj_name = f"MTK_{clean_name.capitalize()}"
        obj = bpy.data.objects.new(obj_name, blender_mesh)
        obj.location = base_loc

        if abs(self.scale - 1.0) > 1e-4:
            obj.scale = (self.scale, self.scale, self.scale)

        target_col = context.collection if context and context.collection else bpy.context.scene.collection
        target_col.objects.link(obj)

        if atlas is not None:
            try:
                used_chunk_ids = mesh_data.used_materials()
                ensure_world_materials(
                    world_obj=obj,
                    prefs=prefs,
                    atlas=atlas,
                    used_chunk_ids=used_chunk_ids,
                )
            except Exception as e:
                logger.warning(f"Failed binding atlas materials for item '{clean_name}': {e}")

        try:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            if context:
                context.view_layer.objects.active = obj
        except Exception:
            pass

        obj["mtk:is_debug_item"] = True
        obj["mtk:item_id"] = f"minecraft:{clean_name}"

        q_count = mesh_data.quad_count
        v_count = mesh_data.vertex_count
        self.report(
            {"INFO"},
            f"Loaded Prebaked Item '{clean_name}': {q_count} quads, {v_count} verts",
        )
        return {"FINISHED"}
