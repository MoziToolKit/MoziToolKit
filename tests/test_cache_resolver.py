"""Tests for the host-agnostic Rust `.mtkcache` resolver."""

import tempfile
import unittest
from pathlib import Path

from MoziToolKit.bridge.engine import get_libmtk


class TestCacheResolver(unittest.TestCase):
    def test_resolves_matching_fingerprint_and_newest_fallback(self):
        mtk = get_libmtk()
        testing = getattr(getattr(mtk, "unstable", None), "testing", None) if mtk else None
        if (
            mtk is None
            or not hasattr(mtk, "resolve_active_cache_package")
            or testing is None
            or not hasattr(testing, "create_test_cache_package")
        ):
            self.skipTest("Native cache resolver / test package helper unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "fp1.mtkcache"
            second = root / "fp2.mtkcache"
            testing.create_test_cache_package(str(first), "fp1", 0)
            testing.create_test_cache_package(str(second), "fp2", 0)

            self.assertEqual(
                mtk.resolve_active_cache_package(str(root), "fp1"), str(first)
            )
            self.assertEqual(
                mtk.resolve_active_cache_package(str(root), "fp2"), str(second)
            )
            self.assertIsNone(mtk.resolve_active_cache_package(str(root), "missing"))
            self.assertIn(
                mtk.resolve_active_cache_package(str(root), None),
                {str(first), str(second)},
            )

    def test_missing_directory_returns_none(self):
        mtk = get_libmtk()
        testing = getattr(getattr(mtk, "unstable", None), "testing", None) if mtk else None
        if (
            mtk is None
            or not hasattr(mtk, "resolve_active_cache_package")
            or testing is None
            or not hasattr(testing, "create_test_cache_package")
        ):
            self.skipTest("Native cache resolver / test package helper unavailable")
        self.assertIsNone(
            mtk.resolve_active_cache_package("/path/that/does/not/exist", "fp")
        )

    def test_fast_fingerprint_parity_with_stack(self):
        """Verifies get_configured_stack_fingerprint produces exact 1:1 match with ResourcePackStack."""
        mtk = get_libmtk()
        if mtk is None or not hasattr(mtk, "ResourcePackStack"):
            self.skipTest("libmtk ResourcePackStack unavailable")

        from types import SimpleNamespace
        from unittest.mock import patch
        from MoziToolKit.bridge.assets.pack import (
            get_configured_stack_fingerprint,
            get_configured_pack_stack,
        )
        from MoziToolKit.bridge.assets.cache import get_active_cache_package_path
        from MoziToolKit.utils.assets.guard import check_asset_cache_status

        with tempfile.TemporaryDirectory() as tmpdir:
            dir_pack = Path(tmpdir) / "test_addon"
            dir_pack.mkdir()

            mock_prefs = SimpleNamespace(
                resource_pack_path=str(dir_pack),
                resource_packs=[],
                cache_dir=tmpdir,
            )

            # Stack computation
            stack = get_configured_pack_stack(mock_prefs)
            fp_stack = stack.compute_stack_fingerprint()

            # Fast lightweight fingerprint
            fp_fast = get_configured_stack_fingerprint(mock_prefs)
            self.assertEqual(fp_fast, fp_stack)

            # Verify get_active_cache_package_path does NOT call get_configured_pack_stack
            with patch("MoziToolKit.bridge.assets.cache.get_configured_pack_stack") as mock_get_stack:
                _ = get_active_cache_package_path(mock_prefs)
                mock_get_stack.assert_not_called()

            # Verify check_asset_cache_status does NOT call _get_configured_pack_stack
            with patch("MoziToolKit.utils.assets.guard._get_configured_pack_stack") as mock_guard_stack:
                _ = check_asset_cache_status(mock_prefs)
                mock_guard_stack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
