from __future__ import annotations

import numpy as np

from simpledesign.core.geometry.mesh import box_mesh


def test_box_mesh_bounds_and_triangles() -> None:
    mesh = box_mesh((600, 300, 18))
    assert np.allclose(mesh.points.min(axis=0), (0, 0, 0))
    assert np.allclose(mesh.points.max(axis=0), (600, 300, 18))
    assert len(mesh.faces) % 4 == 0
    assert len(mesh.faces) // 4 >= 12  # at least 2 triangles per face


def test_box_mesh_is_cached() -> None:
    assert box_mesh((10, 20, 30)) is box_mesh((10.0000001, 20, 30))
