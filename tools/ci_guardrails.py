#!/usr/bin/env python3
"""
MoziToolKit Architectural Guardrails (CI & Local Gate).

Enforces the Golden Rules in `MoziToolKit/GEMINI.md` and `libmozitoolkit/GEMINI.md`:
1. Layer Isolation (Rule 2): `ui/`, `operators/`, and `utils/` must NEVER directly import
   `libmtk_py` or `libmtk_blender_dna`.
2. Pure Data Bridge & No Runtime Probing (Rule 2 & Rust Rule 1): `bridge/` must not probe native
   capabilities with `hasattr(cloud_data/mesh_data/reader, ...)` or retain per-face string
   provenance fallback (`inject_face_source_texture_keys`).
3. Blender 4.2+ / 5.x / 6.0 Compatibility (Rule 6): No `.use_nodes`, no `ShaderNodeMixRGB`.
4. No MagicMock in Tests (Rule 9): `tests/` must never import or use `MagicMock`.
5. Zero Dead Native Symbols: Every stable symbol exported on `libmtk_py` and `libmtk_blender_dna`
   must have at least one consumer in `MoziToolKit` production code (`bridge/`, `utils/`, `operators/`, `ui/`, `dev/`).
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path
import re
import sys

MOZI_ROOT = Path(__file__).resolve().parents[1]
ROOT_PARENT = MOZI_ROOT.parent


def check_layer_isolation() -> list[str]:
    errors: list[str] = []
    forbidden_modules = {"libmtk_py", "libmtk_blender_dna"}
    for sub in ("ui", "operators", "utils"):
        base = MOZI_ROOT / sub
        if not base.exists():
            continue
        for py_file in sorted(base.rglob("*.py")):
            rel = py_file.relative_to(MOZI_ROOT).as_posix()
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in forbidden_modules:
                            errors.append(
                                f"{rel}:{node.lineno}: forbidden direct import `{alias.name}` outside bridge/ (Rule 2)"
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in forbidden_modules:
                        errors.append(
                            f"{rel}:{node.lineno}: forbidden direct import from `{node.module}` outside bridge/ (Rule 2)"
                        )
    return errors


def check_blender_6_compat() -> list[str]:
    errors: list[str] = []
    for sub in ("bridge", "ui", "operators", "utils", "dev", "tests"):
        base = MOZI_ROOT / sub
        if not base.exists():
            continue
        for py_file in sorted(base.rglob("*.py")):
            rel = py_file.relative_to(MOZI_ROOT).as_posix()
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "use_nodes":
                    errors.append(f"{rel}:{node.lineno}: forbidden `.use_nodes` usage (Rule 6)")
                elif isinstance(node, ast.Constant) and node.value == "ShaderNodeMixRGB":
                    errors.append(f"{rel}:{node.lineno}: forbidden `ShaderNodeMixRGB` usage (Rule 6)")
                elif isinstance(node, ast.Name) and node.id == "ShaderNodeMixRGB":
                    errors.append(f"{rel}:{node.lineno}: forbidden `ShaderNodeMixRGB` usage (Rule 6)")
    return errors


def check_no_magic_mock_in_tests() -> list[str]:
    errors: list[str] = []
    tests_dir = MOZI_ROOT / "tests"
    for py_file in sorted(tests_dir.rglob("*.py")):
        if py_file.name == "conftest.py":
            continue
        rel = py_file.relative_to(MOZI_ROOT).as_posix()
        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bMagicMock\b", code):
                errors.append(
                    f"{rel}:{lineno}: forbidden `MagicMock` in tests (Rule 9: use real Blender objects or SimpleNamespace)"
                )
    return errors


def check_bridge_purity_and_no_probing() -> list[str]:
    errors: list[str] = []
    bridge_dir = MOZI_ROOT / "bridge"
    # Native objects must never be probed with hasattr(cloud_data/mesh_data/reader, ...)
    probe_re = re.compile(r"\bhasattr\s*\(\s*(?:cloud_data|mesh_data|reader|cache)\b")
    getattr_probe_re = re.compile(
        r"\bgetattr\s*\(\s*(?:cloud_data|mesh_data|reader|cache)\s*,\s*['\"][a-zA-Z0-9_]+['\"]\s*,"
    )
    retired_funcs = (
        "def inject_face_source_texture_keys(",
        "def compute_biome_tint_attributes(",
    )
    for py_file in sorted(bridge_dir.rglob("*.py")):
        rel = py_file.relative_to(MOZI_ROOT).as_posix()
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if probe_re.search(code) or getattr_probe_re.search(code):
                errors.append(
                    f"{rel}:{lineno}: forbidden runtime capability probing on native object in bridge/ (Rule 1 & 2)"
                )
            for fn_sig in retired_funcs:
                if fn_sig in code:
                    errors.append(
                        f"{rel}:{lineno}: retired non-paletted function `{fn_sig}` must be removed (Rule 1 & 11)"
                    )
    return errors


def check_native_api_consumers() -> list[str]:
    errors: list[str] = []
    sys.path.insert(0, str(ROOT_PARENT))
    dev_lib = MOZI_ROOT / "dev" / "lib"
    if dev_lib.exists():
        sys.path.insert(0, str(dev_lib))

    try:
        import libmtk_py  # type: ignore
        import libmtk_blender_dna  # type: ignore
    except ImportError as exc:
        errors.append(f"Native modules not importable for consumer audit: {exc}")
        return errors

    prod_files: list[str] = []
    for sub in ("bridge", "utils", "operators", "ui", "dev"):
        base = MOZI_ROOT / sub
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            prod_files.append(py_file.read_text(encoding="utf-8"))
    prod_blob = "\n".join(prod_files)

    # Every top-level symbol in libmtk_py (except `unstable` and `version`) must appear in production code
    for name in sorted(n for n in dir(libmtk_py) if not n.startswith("_")):
        if name in ("unstable", "version"):
            continue
        if not re.search(rf"\b{re.escape(name)}\b", prod_blob):
            errors.append(
                f"libmtk_py.{name}: 0 consumers in MoziToolKit production code (Rule 1 & 9)"
            )

    for name in sorted(n for n in dir(libmtk_blender_dna) if not n.startswith("_")):
        if not re.search(rf"\b{re.escape(name)}\b", prod_blob):
            errors.append(
                f"libmtk_blender_dna.{name}: 0 consumers in MoziToolKit production code (Rule 1 & 9)"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument(
        "--skip-native",
        action="store_true",
        help="Skip native symbol consumer audit if native module is not yet built",
    )
    args = parser.parse_args()

    errors: list[str] = []
    errors.extend(check_layer_isolation())
    errors.extend(check_blender_6_compat())
    errors.extend(check_no_magic_mock_in_tests())
    errors.extend(check_bridge_purity_and_no_probing())
    if not args.skip_native:
        errors.extend(check_native_api_consumers())

    if args.json:
        print(
            json.dumps(
                {"status": "pass" if not errors else "failed", "errors": errors},
                indent=2,
            )
        )
    else:
        if errors:
            print(f"[MoziToolKit ci_guardrails] FAILED with {len(errors)} violation(s):")
            for err in errors:
                print(f"  - {err}")
        else:
            print("[MoziToolKit ci_guardrails] PASSED (0 violations)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
