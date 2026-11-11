# coding: utf-8
"""
Visual Entities Subsystem for MoziToolKit.

Provides declarative reconciliation, O(K) incremental delta updates, single-entity operations,
and host-to-Minecraft scene extraction.
"""

from .reconciler import (
    EntityReconcileResult,
    destroy_entity_obj,
    reconcile_entity_render_plan,
    safe_set_custom_prop,
)
from .local_ops import (
    apply_entity_delta,
    delete_entities_by_keys,
    delete_single_entity,
    find_entity_object,
    get_or_create_entities_container,
    update_entity_transform,
    update_single_entity,
)
from ..scene.entities import extract_scene_visual_entities

__all__ = [
    "EntityReconcileResult",
    "destroy_entity_obj",
    "reconcile_entity_render_plan",
    "safe_set_custom_prop",
    "apply_entity_delta",
    "delete_entities_by_keys",
    "delete_single_entity",
    "find_entity_object",
    "get_or_create_entities_container",
    "update_entity_transform",
    "update_single_entity",
    "extract_scene_visual_entities",
]
