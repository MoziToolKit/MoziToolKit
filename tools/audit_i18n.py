#!/usr/bin/env python3
"""
MoziToolKit & libmozitoolkit i18n Auditing & Synchronization Tool.

Performs static AST & code analysis across the Blender addon frontend (Python)
and native engine backend (Rust) to detect:
1. Missing dictionary entries: user-facing strings in code that lack translations in `i18n/dictionary.py`.
2. Obsolete dictionary entries: translations in `i18n/dictionary.py` no longer used anywhere in code.
3. Test-only entries: dictionary entries only referenced in unit tests.
4. Blank / incomplete translations in `i18n/dictionary.py`.

Usage:
    python3 tools/audit_i18n.py                 # Full audit report
    python3 tools/audit_i18n.py --missing       # List missing terms with file/line
    python3 tools/audit_i18n.py --obsolete      # List obsolete dictionary entries
    python3 tools/audit_i18n.py --generate-snippet  # Generate python dict code for missing terms
    python3 tools/audit_i18n.py --prune-diff    # Show lines in dictionary.py that can be pruned
    python3 tools/audit_i18n.py --check         # CI check: exit 1 if missing terms exist
    python3 tools/audit_i18n.py --json          # Machine-readable JSON output
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple


# --- Directory Setup ---
SCRIPT_DIR = Path(__file__).resolve().parent
MOZI_DIR = SCRIPT_DIR.parent
ROOT_DIR = MOZI_DIR.parent
LIB_DIR = ROOT_DIR / "libmozitoolkit"

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "target",
    "wheels",
    "dist",
    "build",
    "node_modules",
    "site-packages",
}

# Ignore obvious non-translatable tokens, icons, and technical internal IDs
IGNORED_EXACT_TOKENS = {
    "",
    "X",
    "Y",
    "Z",
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "R",
    "G",
    "B",
    "None",
    "NONE",
    "DEFAULT",
    "CANCELLED",
    "FINISHED",
    "RUNNING_MODAL",
    "PASS_THROUGH",
    "INTERFACE",
    "REGISTER",
    "UNDO",
    "BLOCKING",
    "INTERNAL",
    "HIDDEN",
    "INFO",
    "WARNING",
    "ERROR",
    "QUESTION",
    "WORLD",
    "SCENE_DATA",
    "OBJECT",
    "MESH",
    "OUTLINER_OB_MESH",
    "COLORSET_01_VEC",
    "ADD",
    "REMOVE",
    "TRASH",
    "FILE_REFRESH",
    "ZOOM_IN",
    "EXPORT",
    "IMPORT",
    "GRID",
    "COMPACT",
    "RGBA",
    "RGB",
    "srgb",
    "linear",
    "*.json",
    "*.mca",
    ".mtkscene",
    ".mtkcache",
    "level.dat",
    "mozi",
    "mtk",
    "zh_HANS",
    "zh_CN",
    "en_US",
}


def is_translatable_candidate(s: str) -> bool:
    """Filters out non-human-readable technical identifiers, icons, and formatting artifacts."""
    if not isinstance(s, str):
        return False
    s_strip = s.strip()
    if len(s_strip) <= 1:
        return False
    if s_strip in IGNORED_EXACT_TOKENS:
        return False
    # Skip Blender internal operator/property identifiers (e.g. mozi.rebuild_mesh)
    if s_strip.startswith("mozi.") or s_strip.startswith("mtk."):
        return False
    # Skip python magic methods
    if s_strip.startswith("__") and s_strip.endswith("__"):
        return False
    # Skip file patterns like *.ext
    if s_strip.startswith("*."):
        return False
    # Skip purely numeric or punctuation strings
    if re.fullmatch(r"^[0-9\s.,;:_/\-+*&|#~!?%^<>=()\[\]{}]+$", s_strip):
        return False
    return True


# --- Frontend Term Extraction (Python AST) ---

class FrontendVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path, relpath: str):
        self.filepath = filepath
        self.relpath = relpath
        self.extracted: List[Tuple[str, str, int]] = []  # (term, category, lineno)
        self.module_constants: Dict[str, Any] = {}

    def visit_ClassDef(self, node: ast.ClassDef):
        # 1. Detect Operator classes (only Operators use class docstring as UI tooltip in Blender)
        is_operator = False
        has_explicit_bl_desc = False

        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "bl_idname":
                            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                                val = stmt.value.value
                                if val.startswith("mozi.") or val.startswith("mtk."):
                                    is_operator = True
                        elif target.id == "bl_description":
                            has_explicit_bl_desc = True

        for base in node.bases:
            if isinstance(base, ast.Attribute) and base.attr == "Operator":
                is_operator = True
            elif isinstance(base, ast.Name) and base.id == "Operator":
                is_operator = True

        # If it is an Operator and does not have an explicit bl_description, Blender uses the class docstring
        if is_operator and not has_explicit_bl_desc:
            doc = ast.get_docstring(node)
            if doc:
                clean_doc = doc.strip()
                if is_translatable_candidate(clean_doc):
                    self.extracted.append((clean_doc, "operator_docstring", node.lineno))

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        # 2. Class / module assignments: bl_label, bl_description
        for target in node.targets:
            if isinstance(target, ast.Name):
                name = target.id
                if name in ("bl_label", "bl_description"):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        val = node.value.value.strip()
                        if is_translatable_candidate(val):
                            self.extracted.append((val, name, node.lineno))

                # Track top-level lists / tuples of enum items: e.g. FOO_ITEMS = [("ID", "Name", "Desc"), ...]
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    elts = node.value.elts
                    if elts and all(isinstance(e, (ast.Tuple, ast.List)) and len(e.elts) >= 2 for e in elts):
                        for item in elts:
                            if len(item.elts) >= 2 and isinstance(item.elts[1], ast.Constant) and isinstance(item.elts[1].value, str):
                                item_name = item.elts[1].value.strip()
                                if is_translatable_candidate(item_name):
                                    self.extracted.append((item_name, "enum_item_name", item.elts[1].lineno))
                            if len(item.elts) >= 3 and isinstance(item.elts[2], ast.Constant) and isinstance(item.elts[2].value, str):
                                item_desc = item.elts[2].value.strip()
                                if is_translatable_candidate(item_desc):
                                    self.extracted.append((item_desc, "enum_item_desc", item.elts[2].lineno))

        # Check for dict entries containing "label": "..." (e.g. CANONICAL_DEFAULT_PRESETS)
        if isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and k.value == "label" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    label_val = v.value.strip()
                    if is_translatable_candidate(label_val):
                        self.extracted.append((label_val, "menu_preset_label", v.lineno))

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        # 3. Explicit translation helpers: tr("..."), _tr("..."), pgettext("...")
        if func_name in ("tr", "_tr", "pgettext") and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                msg = first_arg.value.strip()
                if is_translatable_candidate(msg):
                    self.extracted.append((msg, "explicit_tr", node.lineno))

        # 4. Property definitions: bpy.props.*Property(name="...", description="...", items=[...])
        if "Property" in func_name:
            for kw in node.keywords:
                if kw.arg in ("name", "description") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    val = kw.value.value.strip()
                    if is_translatable_candidate(val):
                        self.extracted.append((val, f"prop_{kw.arg}", node.lineno))
                elif kw.arg == "items" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    for item in kw.value.elts:
                        if isinstance(item, (ast.Tuple, ast.List)):
                            if len(item.elts) >= 2 and isinstance(item.elts[1], ast.Constant) and isinstance(item.elts[1].value, str):
                                iname = item.elts[1].value.strip()
                                if is_translatable_candidate(iname):
                                    self.extracted.append((iname, "enum_item_name", item.elts[1].lineno))
                            if len(item.elts) >= 3 and isinstance(item.elts[2], ast.Constant) and isinstance(item.elts[2].value, str):
                                idesc = item.elts[2].value.strip()
                                if is_translatable_candidate(idesc):
                                    self.extracted.append((idesc, "enum_item_desc", item.elts[2].lineno))

        # 5. UI Layout calls: layout.label(text="..."), layout.operator(..., text="..."), layout.menu(..., text="...")
        if func_name in ("label", "operator", "menu", "prop"):
            for kw in node.keywords:
                if kw.arg == "text" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    val = kw.value.value.strip()
                    if is_translatable_candidate(val):
                        self.extracted.append((val, f"ui_{func_name}_text", node.lineno))

        # 6. Operator reports: self.report({'ERROR'}, "...")
        if func_name == "report" and len(node.args) >= 2:
            second_arg = node.args[1]
            if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                msg = second_arg.value.strip()
                if is_translatable_candidate(msg):
                    self.extracted.append((msg, "report_message", node.lineno))

        self.generic_visit(node)


def extract_frontend_terms(mozi_path: Path) -> Dict[str, List[Tuple[str, str]]]:
    """Extracts all translatable strings from the MoziToolKit Python codebase."""
    results: Dict[str, List[Tuple[str, str]]] = {}  # term -> list of (category, file_line)

    py_files = [
        p for p in mozi_path.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
        and "tests" not in p.parts
        and "tools" not in p.parts
        and p.name != "dictionary.py"
    ]

    for py_file in py_files:
        rel = py_file.relative_to(mozi_path).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            visitor = FrontendVisitor(py_file, rel)
            visitor.visit(tree)
            for term, cat, lineno in visitor.extracted:
                if term not in results:
                    results[term] = []
                results[term].append((cat, f"{rel}:{lineno}"))
        except Exception:
            continue

    # Known canonical dimension translation tokens (dynamically formatted in i18n/formatters.py)
    dynamic_dimensions = [
        ("overworld", "dimension_token", "i18n/formatters.py:60"),
        ("the_nether", "dimension_token", "i18n/formatters.py:60"),
        ("the_end", "dimension_token", "i18n/formatters.py:60"),
    ]
    for dim, cat, loc in dynamic_dimensions:
        if dim not in results:
            results[dim] = []
        results[dim].append((cat, loc))

    return results


# --- Backend Term Extraction (Rust Codebase) ---

def extract_backend_terms(lib_path: Path) -> Dict[str, List[Tuple[str, str]]]:
    """Extracts user-facing progress prefixes, milestone reports, biomes, and sync lifecycle states from libmozitoolkit."""
    results: Dict[str, List[Tuple[str, str]]] = {}

    if not lib_path.exists():
        return results

    # 1. Minecraft 66 Canonical Biomes (SSOT)
    canonical_biomes_file = lib_path / "crates/mtk-material/src/biome/canonical_data.rs"
    if canonical_biomes_file.exists():
        text = canonical_biomes_file.read_text(encoding="utf-8")
        biome_matches = re.findall(r"name:\s*\"([^\"]+)\"", text)
        rel = canonical_biomes_file.relative_to(lib_path).as_posix()
        for b_name in biome_matches:
            b_name = b_name.strip()
            if b_name not in results:
                results[b_name] = []
            results[b_name].append(("backend_biome", rel))

    # 2. Progress Throttler prefixes and ProgressReport messages in crates/
    rs_files = [
        p for p in lib_path.rglob("*.rs")
        if not any(part in SKIP_DIRS for part in p.parts)
        and "tests" not in p.parts
        and "benches" not in p.parts
        and "examples" not in p.parts
    ]

    for rs_file in rs_files:
        rel = rs_file.relative_to(lib_path).as_posix()
        content = rs_file.read_text(encoding="utf-8", errors="ignore")

        # Strip test modules `#[cfg(test)]` to avoid extracting unit test mock strings
        cfg_test_idx = content.find("#[cfg(test)]")
        if cfg_test_idx != -1:
            content = content[:cfg_test_idx]

        # Extract .with_prefix("...")
        for m in re.finditer(r"\.with_prefix\(\"([^\"]+)\"\)", content):
            prefix = m.group(1).strip()
            if is_translatable_candidate(prefix):
                lineno = content[:m.start()].count("\n") + 1
                if prefix not in results:
                    results[prefix] = []
                results[prefix].append(("backend_progress_prefix", f"{rel}:{lineno}"))

        # Extract ProgressThrottler::new("stage", ...)
        for m in re.finditer(r"ProgressThrottler::new\(\"([^\"]+)\"", content):
            stage = m.group(1).strip()
            if is_translatable_candidate(stage):
                lineno = content[:m.start()].count("\n") + 1
                if stage not in results:
                    results[stage] = []
                results[stage].append(("backend_stage_id", f"{rel}:{lineno}"))

        # Extract ProgressReport::new(..., "...")
        for m in re.finditer(r"ProgressReport::new\([^,]+,[^,]+,[^,]+,\s*\"([^\"]+)\"", content):
            rep = m.group(1).strip()
            if is_translatable_candidate(rep):
                lineno = content[:m.start()].count("\n") + 1
                if rep not in results:
                    results[rep] = []
                results[rep].append(("backend_progress_report", f"{rel}:{lineno}"))

    # 3. Live Sync Lifecycle States from mtk-sync
    sync_state_file = lib_path / "crates/mtk-sync/src/client/session/state.rs"
    if not sync_state_file.exists():
        sync_state_file = lib_path / "crates/mtk-sync/src/session/state.rs"
    if sync_state_file.exists():
        content = sync_state_file.read_text(encoding="utf-8")
        m = re.search(r"pub enum SyncLifecycle\s*\{([^}]+)\}", content)
        if m:
            rel = sync_state_file.relative_to(lib_path).as_posix()
            for line in m.group(1).split(","):
                variant = line.strip()
                if variant and not variant.startswith("//"):
                    # Convert PascalCase to SCREAMING_SNAKE_CASE (e.g. ReceivingBaseline -> RECEIVING_BASELINE)
                    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", variant)
                    screaming = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).upper()
                    if is_translatable_candidate(screaming):
                        if screaming not in results:
                            results[screaming] = []
                        results[screaming].append(("backend_sync_lifecycle", rel))

    return results


# --- Dictionary Parsing (AST) ---

class DictEntry:
    def __init__(self, context: str, msgid: str, translation: str, lineno: int):
        self.context = context
        self.msgid = msgid
        self.translation = translation
        self.lineno = lineno

    def __repr__(self):
        return f"DictEntry({self.context!r}, {self.msgid!r} -> {self.translation!r} @ L{self.lineno})"


def load_dictionary(dict_file: Path) -> List[DictEntry]:
    """Safely parses MoziToolKit/i18n/dictionary.py via AST without importing bpy."""
    if not dict_file.exists():
        return []

    tree = ast.parse(dict_file.read_text(encoding="utf-8"), filename=dict_file.name)
    entries: List[DictEntry] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, (ast.Tuple, ast.List)) and len(k.elts) == 2:
                    ctx = k.elts[0].value if isinstance(k.elts[0], ast.Constant) else "*"
                    msgid = k.elts[1].value if isinstance(k.elts[1], ast.Constant) else ""
                    trans = v.value if isinstance(v, ast.Constant) else ""
                    if msgid:
                        entries.append(DictEntry(str(ctx), str(msgid), str(trans), k.lineno))

    return entries


# --- Full Occurrence & Audit Engine ---

class AuditReport:
    def __init__(
        self,
        active_entries: List[Tuple[DictEntry, List[Tuple[str, str]]]],
        missing_terms: List[Tuple[str, List[Tuple[str, str]]]],
        obsolete_orphaned: List[DictEntry],
        obsolete_test_only: List[Tuple[DictEntry, List[str]]],
        blank_entries: List[DictEntry],
        total_code_terms: int,
        total_dict_entries: int,
    ):
        self.active_entries = active_entries
        self.missing_terms = missing_terms
        self.obsolete_orphaned = obsolete_orphaned
        self.obsolete_test_only = obsolete_test_only
        self.blank_entries = blank_entries
        self.total_code_terms = total_code_terms
        self.total_dict_entries = total_dict_entries

    @property
    def coverage_rate(self) -> float:
        if self.total_code_terms == 0:
            return 100.0
        covered = self.total_code_terms - len(self.missing_terms)
        return (covered / self.total_code_terms) * 100.0


def run_audit(mozi_path: Path, lib_path: Path) -> AuditReport:
    """Executes the full i18n audit across frontend, backend, and dictionary."""
    dict_file = mozi_path / "i18n/dictionary.py"
    dict_entries = load_dictionary(dict_file)

    frontend_terms = extract_frontend_terms(mozi_path)
    backend_terms = extract_backend_terms(lib_path)

    # Merge code terms
    all_code_terms: Dict[str, List[Tuple[str, str]]] = {}
    for t, locs in frontend_terms.items():
        all_code_terms.setdefault(t, []).extend(locs)
    for t, locs in backend_terms.items():
        all_code_terms.setdefault(t, []).extend(locs)

    # Map dictionary msgids
    dict_by_msgid: Dict[str, List[DictEntry]] = {}
    for entry in dict_entries:
        dict_by_msgid.setdefault(entry.msgid, []).append(entry)

    # 1. Identify missing terms in code
    missing_terms: List[Tuple[str, List[Tuple[str, str]]]] = []
    for term, locs in sorted(all_code_terms.items()):
        if term not in dict_by_msgid:
            missing_terms.append((term, locs))

    # 2. Collect all production and test source files for occurrence verification
    prod_files = []
    test_files = []

    scan_dirs = [mozi_path]
    if lib_path.exists():
        scan_dirs.append(lib_path)

    for scan_dir in scan_dirs:
        for p in scan_dir.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".rs"):
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                if p == dict_file:
                    continue
                if "tests" in p.parts:
                    test_files.append((p, p.read_text(encoding="utf-8", errors="ignore")))
                else:
                    prod_files.append((p, p.read_text(encoding="utf-8", errors="ignore")))

    # 3. Classify dictionary entries
    active_entries: List[Tuple[DictEntry, List[Tuple[str, str]]]] = []
    obsolete_orphaned: List[DictEntry] = []
    obsolete_test_only: List[Tuple[DictEntry, List[str]]] = []
    blank_entries: List[DictEntry] = []

    for entry in dict_entries:
        if not entry.translation.strip():
            blank_entries.append(entry)

        # Check if matched by AST extraction
        if entry.msgid in all_code_terms:
            active_entries.append((entry, all_code_terms[entry.msgid]))
            continue

        # Substring / occurrence check in production code
        found_in_prod = False
        for p, text in prod_files:
            if entry.msgid in text:
                found_in_prod = True
                active_entries.append((entry, [("text_occurrence", p.name)]))
                break

        if found_in_prod:
            continue

        # Occurrence check in test files
        test_matches = []
        for p, text in test_files:
            if entry.msgid in text:
                test_matches.append(p.name)

        if test_matches:
            obsolete_test_only.append((entry, test_matches))
        else:
            obsolete_orphaned.append(entry)

    return AuditReport(
        active_entries=active_entries,
        missing_terms=missing_terms,
        obsolete_orphaned=obsolete_orphaned,
        obsolete_test_only=obsolete_test_only,
        blank_entries=blank_entries,
        total_code_terms=len(all_code_terms),
        total_dict_entries=len(dict_entries),
    )


# --- Output & Formatting Functions ---

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_CYAN = "\033[36m"
ANSI_GRAY = "\033[90m"


def print_report(report: AuditReport, use_color: bool = True):
    def c(code: str, text: str) -> str:
        return f"{code}{text}{ANSI_RESET}" if use_color else text

    print("=" * 74)
    print(c(ANSI_BOLD + ANSI_CYAN, "  MoziToolKit & libmozitoolkit i18n Translation Audit Dashboard"))
    print("=" * 74)
    print(f"  {c(ANSI_GREEN, '[✓]')} Active & Validated Entries  : {c(ANSI_BOLD, str(len(report.active_entries)))}")
    print(f"  {c(ANSI_RED, '[!]')} Missing Code Terms          : {c(ANSI_BOLD + ANSI_RED, str(len(report.missing_terms)))}")
    print(f"  {c(ANSI_YELLOW, '[?]')} Obsolete (Orphaned in Dict) : {c(ANSI_BOLD + ANSI_YELLOW, str(len(report.obsolete_orphaned)))}")
    print(f"  {c(ANSI_BLUE, '[~]')} Test-Only References        : {c(ANSI_BLUE, str(len(report.obsolete_test_only)))}")
    if report.blank_entries:
        print(f"  {c(ANSI_RED, '[✗]')} Blank / Untranslated Values : {c(ANSI_BOLD + ANSI_RED, str(len(report.blank_entries)))}")
    print("-" * 74)
    print(f"  Total Code Terms Analyzed    : {report.total_code_terms}")
    print(f"  Total Dictionary Entries     : {report.total_dict_entries}")
    cov_str = f"{report.coverage_rate:.1f}%"
    cov_color = ANSI_GREEN if report.coverage_rate >= 95.0 else ANSI_YELLOW
    print(f"  Code Translation Coverage    : {c(ANSI_BOLD + cov_color, cov_str)}")
    print("=" * 74)

    # 1. Missing Terms Section
    if report.missing_terms:
        print(f"\n{c(ANSI_BOLD + ANSI_RED, '▶ MISSING CODE TERMS (Need translation in dictionary.py):')} ({len(report.missing_terms)})")
        for term, locs in report.missing_terms:
            first_cat, first_loc = locs[0]
            extra = f" (+{len(locs) - 1} more)" if len(locs) > 1 else ""
            print(f"  {c(ANSI_YELLOW, '•')} {c(ANSI_BOLD, repr(term)):<55} {c(ANSI_GRAY, f'[{first_cat} @ {first_loc}{extra}]')}")

    # 2. Blank Values Section
    if report.blank_entries:
        print(f"\n{c(ANSI_BOLD + ANSI_RED, '▶ BLANK / UNTRANSLATED DICTIONARY ENTRIES:')} ({len(report.blank_entries)})")
        for entry in report.blank_entries:
            print(f"  {c(ANSI_RED, '•')} L{entry.lineno:<5} ({entry.context!r}, {entry.msgid!r})")

    # 3. Obsolete Orphaned Section
    if report.obsolete_orphaned:
        print(f"\n{c(ANSI_BOLD + ANSI_YELLOW, '▶ OBSOLETE ORPHANED ENTRIES (Not referenced anywhere; safe to prune):')} ({len(report.obsolete_orphaned)})")
        for entry in report.obsolete_orphaned[:35]:
            print(f"  {c(ANSI_GRAY, '•')} L{entry.lineno:<5} ({entry.context!r}, {entry.msgid!r}) -> {entry.translation!r}")
        if len(report.obsolete_orphaned) > 35:
            print(f"  ... and {len(report.obsolete_orphaned) - 35} more (run --obsolete to view all)")

    # 4. Test-Only Section
    if report.obsolete_test_only:
        print(f"\n{c(ANSI_BOLD + ANSI_BLUE, '▶ TEST-ONLY ENTRIES (Only referenced in tests; possible legacy leftovers):')} ({len(report.obsolete_test_only)})")
        for entry, test_names in report.obsolete_test_only[:15]:
            test_summary = ", ".join(sorted(set(test_names))[:2])
            print(f"  {c(ANSI_BLUE, '•')} L{entry.lineno:<5} ({entry.context!r}, {entry.msgid!r}) -> {c(ANSI_GRAY, test_summary)}")
        if len(report.obsolete_test_only) > 15:
            print(f"  ... and {len(report.obsolete_test_only) - 15} more")

    print(f"\n{c(ANSI_BOLD + ANSI_CYAN, '💡 Helpful Commands:')}")
    print("  • Copy missing template to clipboard:  python3 tools/audit_i18n.py --generate-snippet")
    print("  • Inspect diff with obsolete pruned:   python3 tools/audit_i18n.py --prune-diff")
    print("  • Run strict CI gate check:            python3 tools/audit_i18n.py --check")
    print()


def print_missing(report: AuditReport):
    if not report.missing_terms:
        print("All code terms are translated in dictionary.py!")
        return
    for term, locs in report.missing_terms:
        loc_str = ", ".join(f"{cat}@{loc}" for cat, loc in locs)
        print(f"{term}\t[{loc_str}]")


def print_obsolete(report: AuditReport):
    print("--- Obsolete Orphaned Entries (Nowhere in codebase) ---")
    for entry in report.obsolete_orphaned:
        print(f"L{entry.lineno}\t({entry.context!r}, {entry.msgid!r}): {entry.translation!r}")
    print("\n--- Obsolete Test-Only Entries (Referenced only in tests) ---")
    for entry, tests in report.obsolete_test_only:
        print(f"L{entry.lineno}\t({entry.context!r}, {entry.msgid!r}): {entry.translation!r}  [in: {', '.join(set(tests))}]")


def generate_snippet(report: AuditReport):
    if not report.missing_terms:
        print("# No missing terms! All code strings are present in dictionary.py.")
        return

    print("# ==========================================================================")
    print("# Auto-Generated Missing Translation Entries (Copy & Paste to dictionary.py)")
    print("# ==========================================================================\n")

    # Group missing terms by primary category
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for term, locs in report.missing_terms:
        cat, loc = locs[0]
        group_name = "UI & Operators"
        if "prop" in cat:
            group_name = "Properties & Descriptions"
        elif "report" in cat:
            group_name = "Feedback & Report Messages"
        elif "backend" in cat:
            group_name = "Backend Milestones & Sync"
        elif "enum" in cat:
            group_name = "Enum Items & Dropdowns"

        grouped.setdefault(group_name, []).append((term, loc))

    for group_name, items in sorted(grouped.items()):
        print(f"        # --- {group_name} ---")
        for term, loc in sorted(items, key=lambda x: x[0]):
            escaped_term = term.replace("'", "\\'")
            print(f"        ('*', '{escaped_term}'): '',  # {loc}")
        print()


def prune_diff(report: AuditReport, dict_path: Path):
    """Outputs line-by-line diff or lines in dictionary.py that can be pruned."""
    orphaned_lines = set(entry.lineno for entry in report.obsolete_orphaned)
    if not orphaned_lines:
        print("No orphaned lines to prune in dictionary.py.")
        return

    lines = dict_path.read_text(encoding="utf-8").splitlines()
    print(f"Lines to prune in {dict_path.name} ({len(orphaned_lines)} entries):")
    for idx, line in enumerate(lines, start=1):
        if idx in orphaned_lines:
            print(f"- L{idx:4d}: {line}")


def clean_dictionary(report: AuditReport, dict_path: Path, output_path: Optional[Path] = None):
    """Writes a cleaned dictionary.py with orphaned obsolete entries removed."""
    orphaned_lines = set(entry.lineno for entry in report.obsolete_orphaned)
    lines = dict_path.read_text(encoding="utf-8").splitlines()

    new_lines = []
    for idx, line in enumerate(lines, start=1):
        if idx not in orphaned_lines:
            new_lines.append(line)

    dest = output_path or dict_path
    dest.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Successfully pruned {len(orphaned_lines)} obsolete entries into {dest}")


def to_json(report: AuditReport) -> str:
    data = {
        "summary": {
            "total_code_terms": report.total_code_terms,
            "total_dict_entries": report.total_dict_entries,
            "active_count": len(report.active_entries),
            "missing_count": len(report.missing_terms),
            "obsolete_orphan_count": len(report.obsolete_orphaned),
            "obsolete_test_only_count": len(report.obsolete_test_only),
            "blank_count": len(report.blank_entries),
            "coverage_rate": round(report.coverage_rate, 2),
        },
        "missing_terms": [
            {"term": term, "locations": [{"category": cat, "file_line": loc} for cat, loc in locs]}
            for term, locs in report.missing_terms
        ],
        "obsolete_orphaned": [
            {"lineno": e.lineno, "context": e.context, "msgid": e.msgid, "translation": e.translation}
            for e in report.obsolete_orphaned
        ],
        "obsolete_test_only": [
            {
                "lineno": e.lineno,
                "context": e.context,
                "msgid": e.msgid,
                "translation": e.translation,
                "referenced_in_tests": sorted(list(set(tests))),
            }
            for e, tests in report.obsolete_test_only
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


# --- CLI Parser ---

def main():
    parser = argparse.ArgumentParser(
        description="MoziToolKit & libmozitoolkit i18n Auditing & Term Extraction Tool"
    )
    parser.add_argument("--mozi-dir", type=Path, default=MOZI_DIR, help="Path to MoziToolKit root")
    parser.add_argument("--lib-dir", type=Path, default=LIB_DIR, help="Path to libmozitoolkit root")
    parser.add_argument("--missing", action="store_true", help="Print only missing code terms")
    parser.add_argument("--obsolete", action="store_true", help="Print only obsolete dictionary entries")
    parser.add_argument("--generate-snippet", action="store_true", help="Generate python dict snippet for missing terms")
    parser.add_argument("--prune-diff", action="store_true", help="Show lines in dictionary.py that can be pruned")
    parser.add_argument("--clean-dict", type=Path, nargs="?", const=True, help="Write cleaned dictionary (optional output path)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--check", action="store_true", help="CI mode: exit with code 1 if missing terms exist")
    parser.add_argument("--fail-on-obsolete", action="store_true", help="In --check mode, also fail if obsolete terms exist")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI terminal colors")

    args = parser.parse_args()

    report = run_audit(args.mozi_dir, args.lib_dir)

    if args.json:
        print(to_json(report))
        sys.exit(0)

    if args.missing:
        print_missing(report)
        sys.exit(0)

    if args.obsolete:
        print_obsolete(report)
        sys.exit(0)

    if args.generate_snippet:
        generate_snippet(report)
        sys.exit(0)

    if args.prune_diff:
        print_diff = args.mozi_dir / "i18n/dictionary.py"
        prune_diff(report, print_diff)
        sys.exit(0)

    if args.clean_dict:
        dict_file = args.mozi_dir / "i18n/dictionary.py"
        target_file = None if args.clean_dict is True else args.clean_dict
        clean_dictionary(report, dict_file, target_file)
        sys.exit(0)

    # Default to dashboard report
    print_report(report, use_color=not args.no_color)

    if args.check:
        failed = False
        if report.missing_terms:
            print(f"ERROR: {len(report.missing_terms)} terms in code lack translation in dictionary.py!", file=sys.stderr)
            failed = True
        if args.fail_on_obsolete and report.obsolete_orphaned:
            print(f"ERROR: {len(report.obsolete_orphaned)} obsolete terms exist in dictionary.py!", file=sys.stderr)
            failed = True
        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()
