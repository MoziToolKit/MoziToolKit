"""Repeat mesh injection must rewrite buffers in place instead of reallocating geometry.

Assembling a world for the same object twice (re-tint, live sync flush, re-import) used to pay
`clear_geometry` + three `add()` calls plus an edge rebuild on every injection (~35 ms for a
336k-quad world). The reuse path is only taken when vertex/loop/polygon counts still match, so
these tests pin both branches.
"""

from __future__ import annotations

import numpy as np
import pytest

import bpy


def _world_mesh(span: int, blocks):
    import libmtk_py

    storage = libmtk_py.VoxelStorage()
    storage.set_bounds(0, 0, 0, span, span, span)
    for x, y, z, state in blocks:
        storage.set_block(x, y, z, state)
    world = libmtk_py.VoxelWorld.from_storage(storage=storage)
    return world.rebuild_all()


def _read_positions(mesh) -> np.ndarray:
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return co


def _read_material_indices(mesh) -> np.ndarray:
    indices = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", indices)
    return indices


@pytest.fixture()
def mesh_datablock():
    mesh = bpy.data.meshes.new("test_injection_reuse")
    yield mesh
    bpy.data.meshes.remove(mesh)


def test_repeat_injection_reuses_topology(mesh_datablock, monkeypatch):
    from MoziToolKit.bridge.mesh import inject_mesh_data
    from MoziToolKit.bridge.mesh import dna as dna_module

    mesh_data = _world_mesh(4, [(x, 0, z, "minecraft:stone") for x in range(4) for z in range(4)])
    assert mesh_data.vertex_count > 0

    calls = {"count": 0}
    original = dna_module.clean_zombie_attributes

    def counting_clean(mesh):
        calls["count"] += 1
        return original(mesh)

    monkeypatch.setattr(dna_module, "clean_zombie_attributes", counting_clean)

    assert inject_mesh_data(mesh_data, mesh_datablock, update_topology=True, update_normals=True)
    first_positions = _read_positions(mesh_datablock)
    first_materials = _read_material_indices(mesh_datablock)
    first_counts = (len(mesh_datablock.vertices), len(mesh_datablock.loops), len(mesh_datablock.polygons))
    allocations = calls["count"]
    assert allocations > 0, "the first injection must allocate geometry"

    # Same mesh data again: counts match, so the buffers are rewritten in place.
    assert inject_mesh_data(mesh_data, mesh_datablock, update_topology=True, update_normals=True)
    assert calls["count"] == allocations, "repeat injection must not clear/reallocate geometry"
    assert first_counts == (
        len(mesh_datablock.vertices),
        len(mesh_datablock.loops),
        len(mesh_datablock.polygons),
    )
    np.testing.assert_array_equal(_read_positions(mesh_datablock), first_positions)
    np.testing.assert_array_equal(_read_material_indices(mesh_datablock), first_materials)


def test_topology_change_still_reallocates(mesh_datablock, monkeypatch):
    from MoziToolKit.bridge.mesh import inject_mesh_data
    from MoziToolKit.bridge.mesh import dna as dna_module

    small = _world_mesh(3, [(x, 0, z, "minecraft:stone") for x in range(3) for z in range(3)])
    large = _world_mesh(5, [(x, 0, z, "minecraft:stone") for x in range(5) for z in range(5)])
    assert small.vertex_count != large.vertex_count

    calls = {"count": 0}
    original = dna_module.clean_zombie_attributes

    def counting_clean(mesh):
        calls["count"] += 1
        return original(mesh)

    monkeypatch.setattr(dna_module, "clean_zombie_attributes", counting_clean)

    assert inject_mesh_data(small, mesh_datablock, update_topology=True, update_normals=True)
    assert inject_mesh_data(large, mesh_datablock, update_topology=True, update_normals=True)

    # A count change must take the reallocation path, not the in-place rewrite.
    assert calls["count"] >= 2
    assert len(mesh_datablock.vertices) == large.vertex_count
    assert len(mesh_datablock.polygons) == large.face_count
