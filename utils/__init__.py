"""
MoziToolKit Utilities Root Package.
Organized into core glue and Blender orchestration domains:
- system: Python dependency management and right-click menu registry
- config: Addon preference manager and persistence backends
- node_groups: Pure Blender shader node template generators (LabPBR, atlas UV, animations)
"""

from . import system
from . import config
from . import progress
from . import async_task

import bpy

from . import node_groups
from . import pack_detect
from . import launcher_scan

__all__ = [
    "system",
    "config",
    "progress",
    "async_task",
    "node_groups",
    "pack_detect",
    "launcher_scan",
]


