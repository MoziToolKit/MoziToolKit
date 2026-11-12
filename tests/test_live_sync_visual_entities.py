# coding: utf-8
"""
Tests for Live Sync visual entities (RFC v3.2).
Validates baseline visual entities ingestion on STREAM_FINISHED and real-time
incremental visual entity UPSERT / REMOVE via VISUAL_ENTITIES_UPDATED events in real Blender.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import bpy
from MoziToolKit.bridge.engine import get_libmtk
from MoziToolKit.operators.sync import op_sync_connect
from MoziToolKit.operators.sync.op_sync_connect import _sync_timer_tick
from MoziToolKit.utils.entities.local_ops import find_entity_object


class MockSessionWithEvents:
    def __init__(self, events, storage=None):
        self._events = events
        self.is_active = True
        self.is_connected = True
        self.connection_status = "CONNECTED"
        self._storage = storage
        self.atlas = None

    def poll_events(self):
        evs = self._events
        self._events = []
        return evs

    def get_storage(self):
        return self._storage


class TestLiveSyncVisualEntities(unittest.TestCase):
    def setUp(self):
        # Create container object in scene
        self.container = bpy.data.objects.new("Sync_Test_Container", None)
        bpy.context.scene.collection.objects.link(self.container)
        self.container["mtk:is_sync_container"] = True
        self.container["mtk_bounds"] = [0, 0, 0, 16, 16, 16]
        self.container["mtk:origin_centered"] = True

        # Tag scene with active container
        bpy.context.scene["mtk_active_sync_container"] = self.container.name

    def tearDown(self):
        # Cleanup created objects safely
        to_remove = [obj for obj in bpy.data.objects if "Sync_Test_Container" in obj.name]
        for obj in to_remove:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    def test_baseline_and_delta_visual_entities_flow(self):
        painting_uuid = "11111111-2222-3333-4444-555555555555"
        painting_json = json.dumps({
            "id": "minecraft:painting",
            "uuid": painting_uuid,
            "position": [1.0, 64.0, 2.0],
            "facing": "north",
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "kind": {
                "type": "minecraft:painting",
                "variant": "kebab",
                "width": 1,
                "height": 1,
                "anchor_block": [1, 64, 2]
            }
        })

        mtk = get_libmtk()
        storage = mtk.VoxelStorage()
        storage.set_bounds(0, 0, 0, 16, 16, 16)
        storage.set_visual_entities_palette([painting_json])

        # 1. Simulate baseline STREAM_FINISHED
        session = MockSessionWithEvents(
            events=[
                {"type": "STREAM_FINISHED", "built_sections": 1}
            ],
            storage=storage,
        )

        with patch.object(op_sync_connect, "get_sync_bridge_session", return_value=session), \
             patch.object(op_sync_connect, "get_active_sync_container", return_value=self.container):
            _sync_timer_tick()

        # Verify painting object was created under container
        painting_obj = find_entity_object(self.container, f"uuid:{painting_uuid}")
        self.assertIsNotNone(painting_obj, "Painting object should be created on baseline STREAM_FINISHED")
        self.assertEqual(painting_obj.get("mtk:entity_uuid"), painting_uuid)

        # 2. Simulate incremental VISUAL_ENTITIES_UPDATED with UPSERT (adding a second painting)
        painting2_uuid = "22222222-3333-4444-5555-666666666666"
        painting2_json = json.dumps({
            "id": "minecraft:painting",
            "uuid": painting2_uuid,
            "position": [2.0, 65.0, 3.0],
            "facing": "south",
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "kind": {
                "type": "minecraft:painting",
                "variant": "alban",
                "width": 1,
                "height": 1,
                "anchor_block": [2, 65, 3]
            }
        })

        session._events = [
            {
                "type": "VISUAL_ENTITIES_UPDATED",
                "upserted": [painting2_json],
                "deleted_uuids": []
            }
        ]

        with patch.object(op_sync_connect, "get_sync_bridge_session", return_value=session), \
             patch.object(op_sync_connect, "get_active_sync_container", return_value=self.container):
            _sync_timer_tick()

        # Verify both paintings exist
        painting2_obj = find_entity_object(self.container, f"uuid:{painting2_uuid}")
        self.assertIsNotNone(painting2_obj, "Second painting object should be created on VISUAL_ENTITIES_UPDATED UPSERT")
        self.assertEqual(painting2_obj.get("mtk:entity_uuid"), painting2_uuid)

        # 3. Simulate incremental VISUAL_ENTITIES_UPDATED with REMOVE (removing first painting)
        session._events = [
            {
                "type": "VISUAL_ENTITIES_UPDATED",
                "upserted": [],
                "deleted_uuids": [painting_uuid]
            }
        ]

        with patch.object(op_sync_connect, "get_sync_bridge_session", return_value=session), \
             patch.object(op_sync_connect, "get_active_sync_container", return_value=self.container):
            _sync_timer_tick()

        # First painting should be removed, second painting should remain
        painting_after_remove = find_entity_object(self.container, f"uuid:{painting_uuid}")
        self.assertIsNone(painting_after_remove, "First painting object should be removed on VISUAL_ENTITIES_UPDATED REMOVE")

        painting2_after_remove = find_entity_object(self.container, f"uuid:{painting2_uuid}")
        self.assertIsNotNone(painting2_after_remove, "Second painting object should remain when first painting was removed")

        # 4. Simulate baseline STREAM_FINISHED with empty visual entities (clearing surviving entities)
        empty_storage = mtk.VoxelStorage()
        empty_storage.set_bounds(0, 0, 0, 16, 16, 16)
        session._storage = empty_storage
        session._events = [
            {"type": "STREAM_FINISHED", "built_sections": 1}
        ]

        with patch.object(op_sync_connect, "get_sync_bridge_session", return_value=session), \
             patch.object(op_sync_connect, "get_active_sync_container", return_value=self.container):
            _sync_timer_tick()

        # Both paintings should now be completely cleaned up
        painting2_after_empty_baseline = find_entity_object(self.container, f"uuid:{painting2_uuid}")
        self.assertIsNone(painting2_after_empty_baseline, "All visual entities should be purged on authoritative empty baseline")
