"""Focused tests for public discovery of the separately installed VTA Core."""

from pathlib import Path
import tempfile
import types
import unittest

import blendcap_vta_runtime as runtime


class RuntimeDiscoveryTests(unittest.TestCase):
    def _root(self, directory: str, *, valid: bool = True) -> Path:
        root = Path(directory) / "VTA"
        package = root / "vta_core"
        package.mkdir(parents=True)
        if valid:
            (package / "__init__.py").write_text("# mock\n", encoding="utf-8")
        return root

    @staticmethod
    def _core_for(root: Path):
        core = types.SimpleNamespace(__file__=str(root / "vta_core" / "__init__.py"))
        for name in runtime.REQUIRED_API:
            setattr(core, name, lambda: None)
        return core

    def test_saved_valid_path_wins_over_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = self._root(directory + "\\saved")
            fallback = self._root(directory + "\\fallback")
            result = runtime.discover_vta_core(
                saved,
                roots=(fallback,),
                importer=lambda _name: self._core_for(saved),
                sys_path=[],
            )
        self.assertTrue(result.available)
        self.assertEqual(result.root, saved)

    def test_localappdata_then_programfiles_then_development_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            program = Path(directory) / "program"
            local_root = self._root(str(local / "Programs"))
            program_root = self._root(str(program))
            roots = runtime.default_roots({"LOCALAPPDATA": str(local), "ProgramFiles": str(program)})
            self.assertEqual(roots[:2], (local_root, program_root))
            result = runtime.discover_vta_core(
                roots=roots,
                importer=lambda _name: self._core_for(local_root),
                sys_path=[],
            )
        self.assertTrue(result.available)
        self.assertEqual(result.root, local_root)

    def test_development_fallback_and_not_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            dev = self._root(directory)
            result = runtime.discover_vta_core(
                roots=(dev,), importer=lambda _name: self._core_for(dev), sys_path=[]
            )
            absent = runtime.discover_vta_core(roots=(Path(directory) / "none",), sys_path=[])
        self.assertTrue(result.available)
        self.assertEqual(absent.status, runtime.VtaCoreStatus.NOT_CONFIGURED)

    def test_missing_and_invalid_saved_paths_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = runtime.discover_vta_core(Path(directory) / "missing", sys_path=[])
            invalid_root = self._root(directory, valid=False)
            invalid = runtime.discover_vta_core(invalid_root, sys_path=[])
        self.assertEqual(missing.status, runtime.VtaCoreStatus.CONFIGURED_PATH_MISSING)
        self.assertEqual(invalid.status, runtime.VtaCoreStatus.CONFIGURED_PATH_INVALID)

    def test_duplicate_path_is_not_added_repeatedly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            path = [str(root.resolve())]
            for _ in range(2):
                result = runtime.discover_vta_core(
                    root, importer=lambda _name: self._core_for(root), sys_path=path
                )
                self.assertTrue(result.available)
        self.assertEqual(len(path), 1)

    def test_broken_or_incompatible_core_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            broken = runtime.discover_vta_core(
                root,
                importer=lambda _name: (_ for _ in ()).throw(RuntimeError("broken")),
                sys_path=[],
            )
            incomplete = types.SimpleNamespace(__file__=str(root / "vta_core" / "__init__.py"))
            incompatible = runtime.discover_vta_core(
                root, importer=lambda _name: incomplete, sys_path=[]
            )
        self.assertEqual(broken.status, runtime.VtaCoreStatus.IMPORT_FAILED)
        self.assertEqual(incompatible.status, runtime.VtaCoreStatus.INCOMPATIBLE)

    def test_existing_review_backend_accepts_discovered_core_module(self):
        from blendcap_vta_review import PrivateCoreBackend

        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            core = self._core_for(root)
            result = runtime.discover_vta_core(
                root, importer=lambda _name: core, sys_path=[]
            )
        backend = PrivateCoreBackend(core=result.core)
        self.assertIs(backend._core, core)


if __name__ == "__main__":
    unittest.main()
