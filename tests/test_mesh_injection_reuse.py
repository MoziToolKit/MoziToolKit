"""Topology safety and CoW guardrails for mesh buffer injection.

Repeat mesh injection must always reallocate and rebuild edges when `update_topology=True` so
that meshes with identical `(vertex, loop, polygon)` counts but changed connectivity never retain
stale edge tables, and non-topology updates (`update_topology=False`) must use the official
RNA `foreach_set` channel so Blender's Implicit Sharing (Copy-on-Write) unshares evaluated buffers.
"""

from __future__ import annotations

import bmesh
import bpy
import numpy as np
import pytest


def _world_mesh(blocks):
    import libmtk_py

    storage = libmtk_py.VoxelStorage()
    storage.set_bounds(0, 0, 0, 4, 4, 4)
    for x, y, z, state in blocks:
        storage.set_block(x, y, z, state)
    world = libmtk_py.VoxelWorld.from_storage(storage=storage)
    return world.rebuild_all()


@pytest.fixture()
def linked_mesh_object():
    mesh = bpy.data.meshes.new("test_injection_safety")
    obj = bpy.data.objects.new("test_injection_safety_obj", mesh)
    bpy.context.collection.objects.link(obj)
    yield obj, mesh
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)


def test_same_count_topology_change_produces_valid_mesh(linked_mesh_object):
    from MoziToolKit.bridge.mesh import inject_mesh_data

    _, mesh = linked_mesh_object

    # Two adjacent stone blocks along X vs along Z share identical (12, 40, 10) counts,
    # while their edge connectivity differs.
    mesh_x = _world_mesh([(0, 0, 0, "minecraft:stone"), (1, 0, 0, "minecraft:stone")])
    mesh_z = _world_mesh([(0, 0, 0, "minecraft:stone"), (0, 0, 1, "minecraft:stone")])
    assert (mesh_x.vertex_count, mesh_x.face_count) == (mesh_z.vertex_count, mesh_z.face_count)

    assert inject_mesh_data(mesh_x, mesh, update_topology=True, update_normals=True, use_dna=True)
    bpy.context.view_layer.update()
    assert mesh.validate(verbose=True, clean_customdata=False) is False

    assert inject_mesh_data(mesh_z, mesh, update_topology=True, update_normals=True, use_dna=True)
    bpy.context.view_layer.update()
    assert len(mesh.edges) > 0
    assert mesh.validate(verbose=True, clean_customdata=False) is False

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert len(bm.verts) == mesh_z.vertex_count
        assert len(bm.faces) == mesh_z.face_count
    finally:
        bm.free()


def test_dna_fallback_to_standard_still_builds_edges(linked_mesh_object, monkeypatch):
    from MoziToolKit.bridge import mesh as mesh_bridge
    from MoziToolKit.bridge.mesh import inject_mesh_data

    _, mesh = linked_mesh_object
    mesh_x = _world_mesh([(0, 0, 0, "minecraft:stone"), (1, 0, 0, "minecraft:stone")])

    assert inject_mesh_data(mesh_x, mesh, update_topology=True, update_normals=True, use_dna=True)

    def fail_dna(*args, **kwargs):
        raise RuntimeError("simulated DNA failure")

    monkeypatch.setattr(mesh_bridge, "inject_topology_dna", fail_dna)
    assert inject_mesh_data(mesh_x, mesh, update_topology=True, update_normals=True, use_dna=True)
    assert len(mesh.edges) > 0
    assert mesh.validate(verbose=True, clean_customdata=False) is False


def test_non_topology_update_uses_safe_path_and_stays_valid(linked_mesh_object, monkeypatch):
    from MoziToolKit.bridge import mesh as mesh_bridge
    from MoziToolKit.bridge.mesh import inject_mesh_data

    _, mesh = linked_mesh_object
    mesh_a = _world_mesh([(0, 0, 0, "minecraft:stone")])
    assert inject_mesh_data(mesh_a, mesh, update_topology=True, update_normals=True, use_dna=True)
    bpy.context.view_layer.update()

    def forbid_dna(*args, **kwargs):
        raise AssertionError("DNA raw pointer writer must not run when update_topology=False")

    monkeypatch.setattr(mesh_bridge, "inject_vertex_positions_dna", forbid_dna)
    monkeypatch.setattr(mesh_bridge, "inject_uvs_dna", forbid_dna)
    monkeypatch.setattr(mesh_bridge, "inject_color_attributes_dna", forbid_dna)
    monkeypatch.setattr(mesh_bridge, "inject_face_materials_dna", forbid_dna)

    assert inject_mesh_data(mesh_a, mesh, update_topology=False, update_normals=True, use_dna=True)
    assert mesh.validate(verbose=True, clean_customdata=False) is False

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    np.testing.assert_allclose(co, np.frombuffer(mesh_a.positions_memoryview(), dtype=np.float32))
