"""
Unit and regression tests for the i18n auditing tool (tools/audit_i18n.py).
Verifies that AST term extraction, dictionary parsing, and occurrence checking
function deterministically across frontend and backend codebases.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from MoziToolKit.tools.audit_i18n import (
    DictEntry,
    FrontendVisitor,
    extract_backend_terms,
    extract_frontend_terms,
    is_translatable_candidate,
    load_dictionary,
    run_audit,
    to_json,
)


class TestI18nAudit(unittest.TestCase):
    """Test suite for tools/audit_i18n.py."""

    def test_translatable_candidate_filter(self):
        """Verifies filtering of non-human-readable technical tokens."""
        self.assertFalse(is_translatable_candidate(""))
        self.assertFalse(is_translatable_candidate("X"))
        self.assertFalse(is_translatable_candidate("123"))
        self.assertFalse(is_translatable_candidate("*.json"))
        self.assertFalse(is_translatable_candidate("DEFAULT"))
        self.assertFalse(is_translatable_candidate("mozi.rebuild_mesh"))
        self.assertFalse(is_translatable_candidate("__init__"))

        self.assertTrue(is_translatable_candidate("Rebuild Voxel Mesh"))
        self.assertTrue(is_translatable_candidate("Ambient Occlusion"))
        self.assertTrue(is_translatable_candidate("Loading chunk"))

    def test_frontend_visitor_ast(self):
        """Verifies AST extraction from simulated Blender Python code."""
        code = '''
import bpy
from ..i18n import tr

NOISE_ITEMS = [
    ("PERLIN", "3D Perlin Noise", "Continuous gradient noise"),
]

class MOZI_OT_test(bpy.types.Operator):
    """Fallback docstring tooltip"""
    bl_idname = "mozi.test"
    bl_label = "Test Operator"
    
    my_prop: bpy.props.BoolProperty(
        name="Enable Test",
        description="Toggle test feature",
    )
    
    def execute(self, context):
        self.report({'ERROR'}, "Failed to execute test")
        return {'FINISHED'}

def draw_ui(layout):
    layout.label(text="My Label")
    layout.operator("mozi.test", text=tr("Custom Operator Text"))
'''
        tree = ast.parse(code)
        visitor = FrontendVisitor(Path("test.py"), "test.py")
        visitor.visit(tree)

        terms = {term: cat for term, cat, _ in visitor.extracted}

        self.assertIn("3D Perlin Noise", terms)
        self.assertEqual(terms["3D Perlin Noise"], "enum_item_name")
        self.assertIn("Continuous gradient noise", terms)
        self.assertIn("Test Operator", terms)
        self.assertEqual(terms["Test Operator"], "bl_label")
        self.assertIn("Enable Test", terms)
        self.assertEqual(terms["Enable Test"], "prop_name")
        self.assertIn("Toggle test feature", terms)
        self.assertEqual(terms["Toggle test feature"], "prop_description")
        self.assertIn("Failed to execute test", terms)
        self.assertEqual(terms["Failed to execute test"], "report_message")
        self.assertIn("My Label", terms)
        self.assertEqual(terms["My Label"], "ui_label_text")
        self.assertIn("Custom Operator Text", terms)
        self.assertEqual(terms["Custom Operator Text"], "explicit_tr")

    def test_load_dictionary(self):
        """Verifies safe AST dictionary loading from dictionary.py."""
        dict_file = Path(__file__).resolve().parents[1] / "i18n/dictionary.py"
        entries = load_dictionary(dict_file)
        self.assertGreater(len(entries), 800)

        msgids = {e.msgid for e in entries}
        self.assertIn("Rebuild Voxel Mesh", msgids)
        self.assertIn("Badlands", msgids)
        self.assertIn("Loading chunk", msgids)

    def test_backend_extraction(self):
        """Verifies Rust backend term extraction (biomes, prefixes, states)."""
        lib_path = Path(__file__).resolve().parents[2] / "libmozitoolkit"
        if not lib_path.exists():
            self.skipTest("libmozitoolkit directory not found")

        backend_terms = extract_backend_terms(lib_path)
        self.assertIn("Badlands", backend_terms)
        self.assertIn("Loading chunk", backend_terms)
        self.assertIn("RECEIVING_BASELINE", backend_terms)

    def test_full_audit_execution(self):
        """Verifies end-to-end execution of run_audit with healthy coverage."""
        mozi_path = Path(__file__).resolve().parents[1]
        lib_path = mozi_path.parent / "libmozitoolkit"

        report = run_audit(mozi_path, lib_path)
        self.assertGreater(report.total_code_terms, 500)
        self.assertGreater(report.total_dict_entries, 800)
        self.assertGreaterEqual(report.coverage_rate, 90.0)

        json_str = to_json(report)
        data = json.loads(json_str)
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["total_code_terms"], report.total_code_terms)


if __name__ == "__main__":
    unittest.main()
