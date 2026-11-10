"""
MoziToolKit Add-on Preferences interface and tab views.
Orchestrates Resource Packs & Base JARs stack, Context Menu presets, and Environment/Storage tabs.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..utils.config import get_config_manager
from ..utils.materials.biome import BIOME_ENUM_ITEMS

from .preferences_utils import _safe_get_prefs, refresh_ui_and_menus
from .preferences_packs import (
    MOZI_PG_resource_pack_entry,
    MOZI_UL_resource_packs_list,
    MOZI_OT_pack_add,
    MOZI_OT_pack_remove,
    MOZI_OT_pack_move,
    MOZI_OT_pack_detect,
    MOZI_OT_scan_instances,
    MOZI_OT_pick_custom_instance_dir,
    on_detected_instance_changed,
    on_detected_instances_launcher_filter_changed,
    get_detected_instances_enum_items,
    _CACHED_DISCOVERED_INSTANCES,
    schedule_pending_pack_detection,
    reorder_resource_packs_by_tier,
    populate_resource_packs,
    PACKS_CLASSES,
)
from .preferences_menus import (
    MOZI_PG_context_menu_item,
    MOZI_PG_available_menu_item,
    MOZI_UL_added_items_list,
    MOZI_UL_unadded_items_list,
    MOZI_OT_menu_add_item,
    MOZI_OT_menu_remove_item,
    MOZI_OT_menu_move_item,
    MOZI_OT_menu_reset_config,
    MOZI_OT_menu_export_config,
    MOZI_OT_menu_import_config,
    on_item_label_changed,
    MENUS_CLASSES,
)
from .preferences_draw_packs import draw_resource_packs_tab
from .preferences_draw_views import (
    draw_material_tab,
    draw_context_menus_tab,
    draw_misc_tab,
)

__all__ = [
    "refresh_ui_and_menus",
    "_safe_get_prefs",
    "on_material_setting_changed",
    "on_item_label_changed",
    "reorder_resource_packs_by_tier",
    "populate_resource_packs",
    "sync_prefs_from_json",
    "save_prefs_to_json",
    "MOZI_PG_resource_pack_entry",
    "MOZI_UL_resource_packs_list",
    "MOZI_PG_context_menu_item",
    "MOZI_PG_available_menu_item",
    "MOZI_UL_added_items_list",
    "MOZI_UL_unadded_items_list",
    "MOZI_OT_pack_add",
    "MOZI_OT_pack_remove",
    "MOZI_OT_pack_move",
    "MOZI_OT_pack_detect",
    "MOZI_OT_scan_instances",
    "MOZI_OT_pick_custom_instance_dir",
    "schedule_pending_pack_detection",
    "MOZI_OT_menu_add_item",
    "MOZI_OT_menu_remove_item",
    "MOZI_OT_menu_move_item",
    "MOZI_OT_menu_reset_config",
    "MOZI_OT_menu_export_config",
    "MOZI_OT_menu_import_config",
    "MOZI_AddonPreferences",
    "PREFERENCES_CLASSES",
]


_is_updating_material_settings = False


def on_material_setting_changed(self, context):
    """Callback when global material replacement preferences are edited."""
    global _is_updating_material_settings
    if _is_updating_material_settings or get_config_manager().is_syncing():
        return
    _is_updating_material_settings = True
    try:
        prefs = _safe_get_prefs(self)
        if prefs:
            get_config_manager().sync_from_preferences(prefs)
            refresh_ui_and_menus(context)
    finally:
        _is_updating_material_settings = False


def on_dna_setting_changed(self, context):
    """Callback when DNA acceleration toggle is changed."""
    try:
        from ..bridge.dna import set_dna_enabled
        set_dna_enabled(self.enable_dna_acceleration)
    except Exception:
        pass
    refresh_ui_and_menus(context)


def sync_prefs_from_json(prefs):
    """Populate preferences PropertyGroups from central ConfigManager."""
    get_config_manager().sync_to_preferences(prefs)


def save_prefs_to_json(prefs):
    """Save preferences PropertyGroups state to central ConfigManager."""
    get_config_manager().sync_from_preferences(prefs)


def _detect_addon_idname():
    pkg = __package__ or ""
    if pkg.startswith("bl_ext."):
        parts = pkg.split(".")
        if len(parts) >= 3:
            return ".".join(parts[:3])
    elif "MoziToolKit" in pkg:
        return "MoziToolKit"
    return "MoziToolKit"


class MOZI_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _detect_addon_idname()

    category_tab: EnumProperty(
        name="Category",
        description="Select preferences category",
        items=[
            ("RESOURCE_PACKS", "Resource Packs & Base JARs", "Manage prioritized resource packs, Minecraft vanilla JARs, and mod JARs for fallback and texture/model baking"),
            ("MATERIAL", "Material & Shading", "Configure standard PBR shading and subsurface options"),
            ("CONTEXT_MENU", "Context Menu Presets", "Configure right-click context menu options"),
            ("MISC", "Performance & Storage", "Worker thread concurrency, direct memory acceleration, and cache settings"),
        ],
        default="RESOURCE_PACKS",
    )

    resource_packs: CollectionProperty(type=MOZI_PG_resource_pack_entry)
    resource_packs_index: IntProperty(default=0)

    show_advanced_packs: BoolProperty(
        name="Advanced Resource Pack & Stack Settings",
        description="Expand or collapse the manual resource pack list, prioritization, and detailed controls",
        default=False,
    )

    detected_instances_launcher_filter: EnumProperty(
        name="Launcher Filter",
        description="Filter detected instances by launcher type",
        items=[
            ("ALL", "All Launchers", "Show instances from all detected launchers"),
            ("PRISM", "Prism Launcher", "Show Prism Launcher instances"),
            ("OFFICIAL", "Official Launcher", "Show official Minecraft / HMCL instances"),
            ("MODRINTH", "Modrinth App", "Show Modrinth App profiles"),
            ("CURSEFORGE", "CurseForge", "Show CurseForge instances"),
            ("CUSTOM", "Custom / External", "Show instances imported from custom directories"),
        ],
        default="ALL",
        update=on_detected_instances_launcher_filter_changed,
    )

    detected_instance_id: EnumProperty(
        name="Game Instance",
        description="Select a locally discovered Minecraft instance",
        items=get_detected_instances_enum_items,
        update=on_detected_instance_changed,
    )

    initial_auto_setup_done: BoolProperty(
        name="Initial Auto Setup Done",
        description="Tracks whether zero-configuration initial discovery has executed",
        default=False,
        options={'HIDDEN'},
    )

    material_mode: EnumProperty(
        name="Material Mode",
        description="Choose how imported materials are structured and generated",
        items=[
            ('ATLAS', "Atlas", "Combine all textures into a single texture atlas material (Default)"),
        ],
        default='ATLAS',
        update=on_material_setting_changed,
    )

    biome_preset: EnumProperty(
        name="Biome Palette",
        description="Choose the Minecraft Biome color palette preset for grass, foliage, and water tinting",
        items=BIOME_ENUM_ITEMS,
        default='PLAINS',
        update=on_material_setting_changed,
    )

    pack_textures: BoolProperty(
        name="Pack Textures into Blend File",
        description="Embed imported textures directly into the Blender file. When unchecked and the .blend file is saved, textures will be saved externally to '//textures/block/' in your project directory",
        default=True,
        update=on_material_setting_changed,
    )

    enable_ctm: BoolProperty(
        name="Connected Textures (CTM)",
        description="Enable OptiFine and Continuity connected textures resolution",
        default=True,
    )

    enable_animated_textures: BoolProperty(
        name="Animated Textures",
        description="Enable animated texture frame playback for water, lava, fire, portals, and animated blocks",
        default=True,
    )

    smooth_biome_blend: BoolProperty(
        name="Smooth Biome Blending",
        description="Enable bilinear interpolation of biome tints across quad corners for film-grade continuous gradients",
        default=True,
    )

    unknown_block_strategy: EnumProperty(
        name="Unknown Blocks",
        description="Choose how missing or unrecognized blocks are handled during import and meshing",
        items=[
            ('FALLBACK_CUBE', "Magenta/Black Cube", "Construct unit cubes with canonical mozi:fallback checkerboard material for unknown blocks (Default)"),
            ('IGNORE_AS_AIR', "Ignore (Air)", "Skip unknown blocks completely without emitting geometry"),
        ],
        default='FALLBACK_CUBE',
    )

    material_render_engine: EnumProperty(
        name="Render Engine Adaptation",
        description="Choose how standard PBR shading adapts to the active render engine",
        items=[
            ('AUTO', "Auto (Follow Active Scene)", "Automatically optimize alpha and subsurface settings for the active engine"),
            ('CYCLES', "Cycles (Full Physical)", "Use full physical subsurface scattering where the material provides it"),
            ('EEVEE', "EEVEE (Realtime Optimized)", "Use realtime-friendly alpha blending; subsurface can be disabled below"),
        ],
        default='AUTO',
        update=on_material_setting_changed,
    )

    material_disable_subsurface: BoolProperty(
        name="Disable Subsurface Scattering (SSS)",
        description="Disable SSS on materials (Recommended in EEVEE to eliminate screen-space whiteout noise on leaves)",
        default=False,
        update=on_material_setting_changed,
    )

    material_subsurface_method: EnumProperty(
        name="Subsurface Method",
        description="Subsurface scattering calculation model in Principled BSDF",
        items=[
            ('BURLEY', "Christensen-Burley", "Fast, high-fidelity subsurface scattering with excellent performance and visual stability in both Cycles and EEVEE"),
            ('RANDOM_WALK', "Random Walk", "Volumetric path-traced random walk scattering (Cycles only, high noise in EEVEE)"),
        ],
        default='BURLEY',
        update=on_material_setting_changed,
    )

    material_enable_vertex_ao: BoolProperty(
        name="Vertex Ambient Occlusion (AO)",
        description="Multiply Base Color by corner vertex ambient occlusion (smooth lighting) baked into mesh colors",
        default=True,
        update=on_material_setting_changed,
    )

    cache_dir: StringProperty(
        name="Cache Directory",
        description="Directory used to store precompiled texture atlases, standalone materials, and model caches. Leave empty to use Blender's default user cache folder",
        subtype='DIR_PATH',
        default="",
        update=on_material_setting_changed,
    )

    thread_count: IntProperty(
        name="Worker Threads",
        description="Number of worker threads for parallel asset precompilation and mesh operations (0 for auto / all logical CPU cores)",
        default=0,
        min=0,
        max=128,
        update=on_material_setting_changed,
    )

    enable_dna_acceleration: BoolProperty(
        name="DNA Direct Memory Acceleration",
        description="Enable hardware-speed zero-copy DNA direct memory injection into Blender mesh buffers. "
                    "Disable to enforce 100% stable standard BPy batch writing API (recommended for strict production stability)",
        default=True,
        update=on_dna_setting_changed,
    )

    context_menu_tab: EnumProperty(
        name="Context Menu View",
        description="Select view context to configure right-click menu",
        items=[
            ("mesh", "Mesh Edit Mode", "Mesh Edit Mode Context Menu"),
            ("object", "Object Mode", "Object Mode Context Menu"),
            ("uv", "UV Editor", "UV Editor Context Menu"),
        ],
        default="mesh",
    )

    added_mesh: CollectionProperty(type=MOZI_PG_context_menu_item)
    added_mesh_index: IntProperty(default=0)
    unadded_mesh: CollectionProperty(type=MOZI_PG_available_menu_item)
    unadded_mesh_index: IntProperty(default=0)

    added_object: CollectionProperty(type=MOZI_PG_context_menu_item)
    added_object_index: IntProperty(default=0)
    unadded_object: CollectionProperty(type=MOZI_PG_available_menu_item)
    unadded_object_index: IntProperty(default=0)

    added_uv: CollectionProperty(type=MOZI_PG_context_menu_item)
    added_uv_index: IntProperty(default=0)
    unadded_uv: CollectionProperty(type=MOZI_PG_available_menu_item)
    unadded_uv_index: IntProperty(default=0)

    is_initialized: BoolProperty(default=False)

    def draw(self, context):
        if not self.is_initialized:
            mgr = get_config_manager()
            mgr.sync_to_preferences(self)
            self.is_initialized = True
            schedule_pending_pack_detection(self)

        if len(self.resource_packs) == 0:
            try:
                from ..utils.launcher_scan import auto_configure_initial_asset_stack
                from .preferences_packs import _CACHED_DISCOVERED_INSTANCES
                if _CACHED_DISCOVERED_INSTANCES:
                    auto_configure_initial_asset_stack(self)
            except Exception:
                pass

        layout = self.layout

        cat_row = layout.row(align=True)
        cat_row.prop(self, "category_tab", expand=True)

        layout.separator()

        if self.category_tab == "RESOURCE_PACKS":
            self.draw_resource_packs(layout, context)
        elif self.category_tab == "MATERIAL":
            self.draw_material(layout, context)
        elif self.category_tab == "CONTEXT_MENU":
            self.draw_context_menus(layout, context)
        elif self.category_tab == "MISC":
            self.draw_misc(layout, context)

    def draw_resource_packs(self, layout, context):
        draw_resource_packs_tab(self, layout, context)

    def draw_material(self, layout, context):
        draw_material_tab(self, layout, context)

    def draw_context_menus(self, layout, context):
        draw_context_menus_tab(self, layout, context)

    def draw_misc(self, layout, context):
        draw_misc_tab(self, layout, context)


PREFERENCES_CLASSES = (
    *PACKS_CLASSES,
    *MENUS_CLASSES,
    MOZI_AddonPreferences,
)
