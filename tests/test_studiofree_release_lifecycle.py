"""Disposable package and maintenance transaction checks; no owner profile use."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import studiofree_maintenance as maintenance
from tools import studiofree_release as release
from tools import package_distribution


ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def fixture(root, name):
    payload = root / name
    exe = payload / "engine" / "vta_engine.exe"
    write(exe, name.encode())
    fingerprint = hashlib.sha256(name.encode()).hexdigest()
    identity = dict(schema="vta-compiled-release", version=1, build_id=name,
                    product_version=maintenance.PRODUCT, protocol="vta-engine",
                    fingerprint=fingerprint, executable_sha256=release.sha256(exe))
    write(payload / "engine" / "release.json", json.dumps(identity).encode())
    for component, rel in (("control", "python.exe"), ("body", "Scripts/python.exe"),
                           ("analysis", "Scripts/python.exe"),
                           ("analysis_source", "src/sam_3d_pose_estimation/stream_detector.py"),
                           ("models", "sam3.pt")):
        write(payload / component / rel, b"fixture")
    routes = dict(control_python="python.exe", body_python="Scripts/python.exe",
                  analysis_python="Scripts/python.exe", body_root=".",
                  kinesia_root=".", sam3_checkpoint="sam3.pt")
    write(payload / "runtime-routes.json", json.dumps(routes).encode())
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("blender_manifest.toml", f'version = "{maintenance.PRODUCT}"\n')
    write(payload / "blendcap.zip", archive.getvalue())
    files = [dict(path=p.relative_to(payload).as_posix(), bytes=p.stat().st_size,
                  sha256=release.sha256(p), component=p.relative_to(payload).parts[0])
             for p in sorted(payload.rglob("*")) if p.is_file()]
    manifest = dict(schema="studiofree-package", version=1, product_version=maintenance.PRODUCT,
                    build_id=name, fingerprint=fingerprint, protocol=maintenance.PROTOCOL,
                    public_commit="a" * 40, private_commit="b" * 40, files=files)
    write(payload / "package.json", json.dumps(manifest).encode())
    return payload


class FakeBlender:
    def __init__(self, root):
        self.exe = root / "blender.exe"
        write(self.exe, b"fixture")
        self.root = root / "extensions"
        self.fail = False
        self.installed = None

    def check_version(self):
        pass

    def repository(self):
        return self.root

    def install(self, archive):
        if self.fail:
            self.fail = False
            raise RuntimeError("Injected Blender CLI failure")
        with zipfile.ZipFile(archive) as zip_file:
            content = zip_file.read("blender_manifest.toml")
        target = self.root / "blendcap"
        write(target / "blender_manifest.toml", content)
        self.installed = archive.parent.name
        return target

    def remove(self):
        (self.root / "blendcap" / "blender_manifest.toml").unlink(missing_ok=True)
        self.installed = None
        return True


class ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".build" / "test-temp"
        work.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = fixture(self.root, "A")
        self.b = fixture(self.root, "B")
        self.blender = FakeBlender(self.root / "profile")
        self.program = self.root / "program"
        self.data = self.root / "local" / "VTA"
        self.smokes = []
        self.lifecycle = maintenance.Lifecycle(self.program, self.data, self.blender,
                                                smoke=lambda path: self.smokes.append(path.name))

    def test_stage_hash_and_traversal_fail_closed(self):
        self.assertEqual(maintenance.verify_payload(self.a)["build_id"], "A")
        (self.a / "models" / "sam3.pt").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            maintenance.verify_payload(self.a)
        value = json.loads((self.b / "package.json").read_text())
        value["files"][0]["path"] = "../QA.blend"
        (self.b / "package.json").write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            maintenance.verify_payload(self.b)

    def test_internal_component_stage_cannot_be_installed(self):
        manifest = json.loads((self.a / "package.json").read_text())
        manifest["internal_test"] = True
        (self.a / "package.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Internal technical-test"):
            maintenance.verify_payload(self.a)
        self.assertEqual(maintenance.verify_payload(self.a, allow_internal=True)["build_id"], "A")

    def test_release_names_cannot_escape_or_alias_windows_paths(self):
        for name in ("..", "A.", "CON", "com1.txt", "A/B"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid release ID"):
                maintenance.release_id(name)

    def test_missing_extension_removal_is_idempotent(self):
        cli = maintenance.BlenderCLI(self.blender.exe)
        with patch.object(cli, "repository", return_value=self.blender.root), \
                patch.object(cli, "command") as command:
            cli.remove()
            command.assert_not_called()

    def test_uninstall_preserves_data_when_blender_was_removed_first(self):
        entitlement = self.data / "license" / "entitlement.dpapi"
        write(entitlement, b"sealed")
        self.lifecycle.install(self.a)
        self.lifecycle.blender = maintenance.BlenderCLI(
            self.root / "removed-blender.exe", allow_missing=True)
        self.lifecycle.uninstall()
        self.assertFalse(self.lifecycle.release("A").exists())
        self.assertFalse(self.lifecycle.runtime_path.exists())
        self.assertEqual(entitlement.read_bytes(), b"sealed")
        self.assertIn("extension-removal-deferred",
                      (self.data / "logs" / "maintenance.jsonl").read_text())

    def test_large_identical_model_is_reused_by_hash(self):
        probe = self.root / "link-probe"
        probe.write_bytes(b"probe")
        try:
            os.link(probe, self.root / "link-target")
        except OSError:
            self.skipTest("Hardlinks unavailable in this filesystem sandbox")
        with patch.object(maintenance, "REUSE_MIN_BYTES", 1):
            self.lifecycle.install(self.a)
            self.lifecycle.install(self.b)
        first = self.lifecycle.release("A") / "models" / "sam3.pt"
        second = self.lifecycle.release("B") / "models" / "sam3.pt"
        self.assertTrue(os.path.samefile(first, second))
        first.unlink()
        self.assertEqual(second.read_bytes(), b"fixture")

    def test_different_model_hash_is_not_reused(self):
        target = self.b / "models" / "sam3.pt"
        target.write_bytes(b"different")
        manifest_path = self.b / "package.json"
        manifest = json.loads(manifest_path.read_text())
        row = next(item for item in manifest["files"] if item["path"] == "models/sam3.pt")
        row["bytes"] = target.stat().st_size
        row["sha256"] = release.sha256(target)
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(maintenance, "REUSE_MIN_BYTES", 1):
            self.lifecycle.install(self.a)
            self.lifecycle.install(self.b)
        first = self.lifecycle.release("A") / "models" / "sam3.pt"
        second = self.lifecycle.release("B") / "models" / "sam3.pt"
        self.assertFalse(os.path.samefile(first, second))

    def test_damaged_previous_release_does_not_block_verified_update(self):
        self.lifecycle.install(self.a)
        (self.lifecycle.release("A") / "models" / "sam3.pt").write_bytes(b"damaged")
        self.assertEqual(self.lifecycle.install(self.b), "B")
        self.assertEqual(self.lifecycle.current()["active"], "B")
        self.assertEqual((self.lifecycle.release("B") / "models" / "sam3.pt").read_bytes(), b"fixture")

    def test_distribution_contains_exact_verified_payload(self):
        installer = self.root / "installer" / "StudioFreeMoCap.exe"
        write(installer, b"synthetic installer")
        output = self.root / "StudioFreeMoCap.zip"
        incomplete = fixture(self.root, "Core")
        (incomplete / "control" / "python.exe").unlink()
        manifest_path = incomplete / "package.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [row for row in manifest["files"] if row["component"] != "control"]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Required component missing"):
            package_distribution.package(installer, incomplete, output)
        receipt = package_distribution.package(installer, self.a, output, test_fixture=True)
        self.assertFalse(output.is_relative_to(self.a))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("payload/package.json"), (self.a / "package.json").read_bytes())
            self.assertEqual(archive.read(installer.name), installer.read_bytes())
            self.assertEqual(json.loads(archive.read("distribution.json")), receipt)
            self.assertEqual(len(archive.namelist()), receipt["payload_files"] + 3)
        self.assertTrue(receipt["test_fixture"])

    def test_customer_distribution_rejects_missing_compliance_bundle(self):
        installer = self.root / "installer" / "StudioFreeMoCap.exe"
        write(installer, b"synthetic installer")
        with self.assertRaises((FileNotFoundError, ValueError)):
            package_distribution.package(installer, self.a, self.root / "customer.zip")

    def test_marked_customer_stage_rejects_missing_compliance_bundle(self):
        path = self.a / "package.json"
        manifest = json.loads(path.read_text())
        manifest["compliance_required"] = True
        path.write_text(json.dumps(manifest))
        with self.assertRaises((FileNotFoundError, ValueError)):
            release.verify_stage(self.a)

    def test_missing_dependency_and_private_source_fail_closed(self):
        (self.b / "models" / "sam3.pt").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.lifecycle.install(self.b)
        self.assertIsNone(self.lifecycle.current())
        self.assertFalse(self.lifecycle.runtime_path.exists())
        write(self.a / "engine" / "vta_core" / "secret.py", b"private source")
        with self.assertRaisesRegex(ValueError, "Private implementation source"):
            release.copy_engine(self.a / "engine", self.root / "copied-engine")

    def test_machine_local_configuration_cannot_be_staged(self):
        path = self.root / "package-tree" / "body" / "pyvenv.cfg"
        write(path, b"home = C:/developer/python")
        with self.assertRaisesRegex(ValueError, "Machine-local"):
            release.package_manifest(self.root / "package-tree", {"product_version": maintenance.PRODUCT,
                "build_id": "A", "fingerprint": "a" * 64}, "a" * 40, "b" * 40)

    def test_update_rollback_preserve_entitlement_and_project(self):
        license_path = self.data / "license" / "entitlement.dpapi"
        project = self.root / "owner" / "QA.blend"
        unrelated = self.program / "other.txt"
        write(license_path, b"sealed-fixture")
        write(project, b"owner-project")
        write(unrelated, b"other")
        self.assertEqual(self.lifecycle.install(self.a), "A")
        self.assertEqual(self.lifecycle.install(self.b), "B")
        self.assertEqual(self.lifecycle.current()["active"], "B")
        self.assertTrue(self.lifecycle.release("A").exists())
        self.assertEqual(self.lifecycle.rollback(), "A")
        self.assertEqual(self.lifecycle.current()["active"], "A")
        self.assertEqual(self.lifecycle.current()["previous"], "B")
        self.assertEqual(self.smokes, ["A", "B", "A"])
        self.lifecycle.uninstall()
        self.assertFalse(self.lifecycle.release("A").exists())
        self.assertFalse(self.lifecycle.release("B").exists())
        self.assertFalse(self.lifecycle.runtime_path.exists())
        self.assertEqual(license_path.read_bytes(), b"sealed-fixture")
        self.assertEqual(project.read_bytes(), b"owner-project")
        self.assertEqual(unrelated.read_bytes(), b"other")

    def test_failed_update_keeps_active_release(self):
        self.lifecycle.install(self.a)
        for checkpoint in ("stage", "smoke", "extension", "switch"):
            with self.assertRaises(RuntimeError):
                self.lifecycle.install(self.b, fail_after=checkpoint)
            self.assertEqual(self.lifecycle.current()["active"], "A")
            self.assertEqual(json.loads(self.lifecycle.runtime_path.read_text())["roots"]["engine"],
                             str(self.lifecycle.release("A") / "engine"))
            self.assertEqual(self.blender.installed, "A")
        self.blender.fail = True
        with self.assertRaisesRegex(RuntimeError, "Blender CLI"):
            self.lifecycle.install(self.b)
        self.assertEqual(self.lifecycle.current()["active"], "A")
        self.assertEqual(self.lifecycle.install(self.b), "B")

    def test_repair_restores_owned_file_only(self):
        self.lifecycle.install(self.a)
        target = self.lifecycle.release("A") / "models" / "sam3.pt"
        target.write_bytes(b"bad")
        owner = self.lifecycle.release("A") / "owner.blend"
        write(owner, b"preserve")
        license_path = self.data / "license" / "entitlement.dpapi"
        write(license_path, b"sealed-fixture")
        self.assertEqual(self.lifecycle.repair(self.a), ["models/sam3.pt"])
        self.assertEqual(target.read_bytes(), b"fixture")
        self.assertEqual(owner.read_bytes(), b"preserve")
        self.assertEqual(license_path.read_bytes(), b"sealed-fixture")

    def test_repair_rejects_linked_owned_destination(self):
        self.lifecycle.install(self.a)
        target = self.lifecycle.release("A") / "models" / "sam3.pt"
        target.unlink()
        outside = self.root / "outside.txt"
        write(outside, b"owner")
        try:
            target.symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "Linked"):
            self.lifecycle.repair(self.a)
        self.assertEqual(outside.read_bytes(), b"owner")


if __name__ == "__main__":
    unittest.main()
