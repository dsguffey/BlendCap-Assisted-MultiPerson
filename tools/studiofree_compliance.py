"""Build and verify payload-specific notices and corresponding-source material.

This assembles evidence; it never grants redistribution approval. All inputs
are explicit local files, so the resulting installer download stays offline.
"""
from __future__ import annotations

import hashlib
import io
import json
import lzma
from pathlib import Path
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "docs/research/studiofree_distribution_notices_draft"
LOCK = ROOT / "tools/distribution_source_lock.json"
BUILD_FILES = (
    "build_release.ps1", "installer/studiofree_mocap.iss",
    "installer/verify_sidecar.ps1",
    "tools/build_portable_ai.py", "tools/build_portable_ai_assets.py",
    "tools/build_portable_control.py", "tools/package_distribution.py",
    "tools/portable_ai_lock.json", "tools/portable_assets_lock.json",
    "tools/probe_offline_components.py",
    "tools/studiofree_compliance.py", "tools/studiofree_maintenance.py",
    "tools/studiofree_release.py", "tools/distribution_source_lock.json",
)
EXCLUDED_NATIVE = (
    "analysis/Lib/site-packages/torch/lib/nvperf_host.dll",
    "analysis/Lib/site-packages/torch/lib/nvrtc64_130_0.alt.dll",
    "body/Lib/site-packages/torch/lib/nvrtc64_120_0.alt.dll",
    "analysis/Lib/site-packages/torch/lib/cusolverMg64_12.dll",
    "body/Lib/site-packages/torch/lib/cusolverMg64_11.dll",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe(name: str) -> Path:
    parts = name.split("/")
    if not name or name.startswith("/") or ":" in name or "\\" in name or any(
            part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe compliance path: {name!r}")
    return Path(*parts)


def checked(path: Path, boundary: Path, expected: str | None = None) -> Path:
    if (not path.is_file() or path.is_symlink() or
            not path.resolve().is_relative_to(boundary.resolve())):
        raise ValueError(f"Missing or linked compliance input: {path}")
    if expected and digest(path) != expected:
        raise ValueError(f"Compliance source hash mismatch: {path}")
    return path


def _commit(repo: Path) -> str:
    result = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _source_archive(archive: Path) -> None:
    with tarfile.open(archive, "r:xz") as content:
        files = [row for row in content if row.isfile()]
    if not files:
        raise ValueError(f"Empty corresponding-source archive: {archive}")


def _fixed_openh264_headers(checkout: Path, expected_commit: str) -> bytes:
    if _commit(checkout) != expected_commit:
        raise ValueError("OpenH264 source commit mismatch")
    result = subprocess.run(("git", "ls-files", "-z", "LICENSE", "codec/api/wels"),
                            cwd=checkout, capture_output=True, check=True)
    names = sorted(x.decode("utf-8") for x in result.stdout.split(b"\0") if x)
    if "LICENSE" not in names or len(names) < 5:
        raise ValueError("OpenH264 API headers or license missing")
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as archive:
        for name in names:
            path = checked(checkout / safe(name), checkout)
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return lzma.compress(tar_bytes.getvalue(), preset=6)


def _zip_add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _license_candidates(payload: Path) -> set[str]:
    def relevant(name: str) -> bool:
        return name.lower().split("/")[-1].startswith(
            ("license", "notice", "copying", "copyright"))

    found = {"payload/" + path.relative_to(payload).as_posix()
             for path in payload.rglob("*") if path.is_file() and
             not path.is_relative_to(payload / "legal") and relevant(path.name)}
    with zipfile.ZipFile(payload / "blendcap.zip") as extension:
        found.update("blendcap-zip/" + name for name in extension.namelist()
                     if not name.endswith("/") and relevant(name))
    return found


def build(payload: Path, extension_source: Path, ffmpeg_sources: dict[str, Path],
          opencv_archives: dict[str, Path], openh264_source: Path, output: Path,
          *, internal_probe: bool = False) -> dict:
    """Add a complete legal tree to a new output path inside an unsealed stage."""
    payload, extension_source = payload.resolve(strict=True), extension_source.resolve(strict=True)
    output = output.resolve()
    if output.exists() or (not internal_probe and not output.is_relative_to(payload)):
        raise ValueError("Compliance output must be a new payload subdirectory")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    draft = json.loads((DRAFT / "manifest.json").read_text(encoding="utf-8"))
    if lock.get("schema") != "studiofree-corresponding-source-lock" or len(draft["files"]) != 182:
        raise ValueError("Compliance lock or notice baseline incomplete")
    for key, name in (("portable_ai_lock_sha256", "portable_ai_lock.json"),
                      ("portable_assets_lock_sha256", "portable_assets_lock.json")):
        if draft.get(key) != digest(ROOT / "tools" / name):
            raise ValueError(f"Dependency lock changed; review notices: {name}")
    if not internal_probe:
        for name in EXCLUDED_NATIVE:
            if (payload / safe(name)).exists():
                raise ValueError(f"Uncleared NVIDIA binary remains: {name}")
    expected_notice_paths = {row["path"] for row in draft["files"]}
    if not _license_candidates(payload) <= expected_notice_paths:
        raise ValueError("Payload license/notice inventory differs from reviewed baseline")
    if set(ffmpeg_sources) != {"analysis", "body"} or set(opencv_archives) != {"analysis", "body"}:
        raise ValueError("Both OpenCV FFmpeg checkouts and full OpenCV archives are required")
    fixed_headers = _fixed_openh264_headers(openh264_source.resolve(strict=True),
                                            lock["openh264_2_5_commit"])
    if hashlib.sha256(fixed_headers).hexdigest() != lock["openh264_2_5_headers_sha256"]:
        raise ValueError("Corrected OpenH264 header archive differs from lock")
    records = []

    def put(name: str, data: bytes, origin: str) -> None:
        target = output / safe(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                            origin=origin))

    # Recheck every reviewed text against the actual staged package, then keep
    # complete copies in a customer-facing notices directory.
    with zipfile.ZipFile(payload / "blendcap.zip") as extension:
        for row in draft["files"]:
            name = row["path"]
            if name.startswith("payload/"):
                data = checked(payload / safe(name[8:]), payload, row["sha256"]).read_bytes()
            elif name.startswith("blendcap-zip/"):
                data = extension.read(name[13:])
            elif name.startswith("additional/"):
                data = checked(DRAFT / safe(name), DRAFT, row["sha256"]).read_bytes()
            else:
                raise ValueError(f"Unexpected notice origin: {name}")
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError(f"Notice drift: {name}")
            put("Third-Party-Notices/" + name, data, row["origin"])
    put("Third-Party-Notices/README.txt", (
        "StudioFree MoCap third-party notices for this exact package.\n"
        "All original license, copyright and NOTICE files are copied below\n"
        "using payload/ or blendcap-zip/ paths. The adjacent legal/manifest.json\n"
        "lists each file, source and SHA-256. Key terms include:\n"
        "  Public Blender extension: blendcap-zip/LICENSE\n"
        "  SAM3: payload/analysis_source/vendor/sam3-main/LICENSE\n"
        "  SAM 3D Body: blendcap-zip/licenses/SAM_LICENSE.txt\n"
        "  DINOv3: blendcap-zip/licenses/DINOV3_LICENSE.txt\n"
        "  MHR: additional/MHR-v1.0.1-LICENSE.txt\n"
        "  CUDA 12.8 / 13.0: additional/CUDA-12.8-EULA.html and\n"
        "                    additional/CUDA-13.0-EULA.html\n"
        "  cuDNN 9.7.1 / 9.19.0: additional/cuDNN-9.7.1-EULA.html and\n"
        "                        additional/cuDNN-9.19.0-EULA.html\n"
        "  OpenCV/FFmpeg: payload/{body,analysis}/Lib/site-packages/cv2/\n"
        "                 LICENSE-3RD-PARTY.txt; matching source is adjacent.\n"
        "Other wheel and CPython texts are indexed individually in manifest.json.\n"
    ).encode(), "StudioFree notice index")

    # Exact public extension source and its build/install material. The private
    # engine checkout is never consulted or copied here.
    source_zip = io.BytesIO()
    with zipfile.ZipFile(source_zip, "w") as archive:
        for path in sorted(extension_source.rglob("*")):
            if path.is_file():
                checked(path, extension_source)
                _zip_add(archive, "extension/" + path.relative_to(extension_source).as_posix(),
                         path.read_bytes())
        for name in BUILD_FILES:
            _zip_add(archive, name, checked(ROOT / safe(name), ROOT).read_bytes())
        for path in sorted(DRAFT.rglob("*")):
            if path.is_file():
                checked(path, DRAFT)
                _zip_add(archive, path.relative_to(ROOT).as_posix(), path.read_bytes())
    put("Corresponding-Source/public-integration.zip", source_zip.getvalue(),
        "exact staged public extension source plus public build/install scripts")

    for role in ("analysis", "body"):
        row = lock["opencv_ffmpeg"][role]
        checkout = ffmpeg_sources[role].resolve(strict=True)
        if _commit(checkout) != row["source_commit"]:
            raise ValueError(f"OpenCV FFmpeg source commit mismatch: {role}")
        checked(payload / safe(row["dll_path"]), payload, row["dll_sha256"])
        prefix = f"Corresponding-Source/opencv-ffmpeg-{role}/"
        opencv_archive = opencv_archives[role].resolve(strict=True)
        checked(opencv_archive, opencv_archive.parent, row["opencv_archive_sha256"])
        put(prefix + f"opencv-{row['opencv_commit']}.tar.gz", opencv_archive.read_bytes(),
            f"https://codeload.github.com/opencv/opencv/tar.gz/{row['opencv_commit']}")
        for name, expected_hash in sorted(row["archives"].items()):
            archive_path = checked(checkout / safe(name), checkout, expected_hash)
            if role == "body" and "openh264-api-headers" in name:
                # OpenCV's n7.1 branch archived an empty, 32-byte file because
                # its script still names the old codec/api/svc location.
                data = fixed_headers
                origin = f"OpenH264 {lock['openh264_2_5_commit']} corrected API archive"
            else:
                _source_archive(archive_path)
                data = archive_path.read_bytes()
                origin = f"opencv_3rdparty {row['source_commit']}:{name}"
            put(prefix + name.removeprefix("sources/"), data, origin)
        for name in ("license.txt", "readme.txt"):
            put(prefix + name, checked(checkout / name, checkout).read_bytes(),
                f"opencv_3rdparty {row['source_commit']}:{name}")
        put(prefix + "STUDIOFREE-SOURCE-README.txt", (
            f"This is OpenCV's corresponding FFmpeg build material for {role}.\n"
            f"opencv_3rdparty source commit: {row['source_commit']}\n"
            f"Shipped DLL: {row['dll_path']}\nSHA-256: {row['dll_sha256']}\n"
            f"Full OpenCV source commit: {row['opencv_commit']}\n"
            "The opencv_ffmpeg archive contains scripts/configuration; the build/ archives\n"
            "contain the actual FFmpeg, libvpx, AOM and OpenH264 API source used.\n"
            "The OpenH264 library is loaded separately at runtime, not bundled here.\n"
            "For body, the upstream OpenH264 API archive was empty because its script\n"
            "used the obsolete codec/api/svc path. The supplied corrected archive\n"
            "contains codec/api/wels and LICENSE from the pinned v2.5.0 commit.\n"
            "The DLL remains a separately replaceable OpenCV FFmpeg plugin.\n"
        ).encode(), "StudioFree explanatory build/source note")

    put("Corresponding-Source/README.txt", (
        "StudioFree corresponding source for the public GPL integration and bundled\n"
        "OpenCV FFmpeg plugins is included in this same offline download. The public\n"
        "source ZIP contains the extension's exact staged source plus public build\n"
        "and install material. Proprietary private-engine source is not included.\n"
        "See each OpenCV FFmpeg directory for the matching binary hash and build\n"
        "sources. Replacing the FFmpeg DLL at its app-local cv2 location preserves\n"
        "the LGPL dynamic-link replacement route. The complete notices are adjacent.\n"
    ).encode(), "StudioFree corresponding-source delivery note")
    manifest = dict(schema="studiofree-compliance-bundle", version=1,
                    public_commit=_commit(ROOT),
                    source_lock_sha256=digest(LOCK),
                    notice_baseline_sha256=digest(DRAFT / "manifest.json"),
                    files=sorted(records, key=lambda row: row["path"]))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify(payload, output, internal_probe=internal_probe)
    return manifest


def verify(payload: Path, output: Path | None = None, *, internal_probe: bool = False) -> dict:
    payload = payload.resolve(strict=True)
    output = output or payload / "legal"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "studiofree-compliance-bundle" or manifest.get("version") != 1:
        raise ValueError("Compliance bundle missing or invalid")
    records = manifest.get("files", [])
    if manifest.get("source_lock_sha256") != digest(LOCK) or manifest.get(
            "notice_baseline_sha256") != digest(DRAFT / "manifest.json"):
        raise ValueError("Compliance source/notice baseline differs")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    baseline = json.loads((DRAFT / "manifest.json").read_text(encoding="utf-8"))
    for key, name in (("portable_ai_lock_sha256", "portable_ai_lock.json"),
                      ("portable_assets_lock_sha256", "portable_assets_lock.json")):
        if baseline.get(key) != digest(ROOT / "tools" / name):
            raise ValueError(f"Dependency lock changed; review notices: {name}")
    expected_names = {"Third-Party-Notices/" + row["path"] for row in baseline["files"]}
    expected_names.add("Third-Party-Notices/README.txt")
    expected_names.update({"Corresponding-Source/public-integration.zip",
                           "Corresponding-Source/README.txt"})
    for role, item in lock["opencv_ffmpeg"].items():
        prefix = f"Corresponding-Source/opencv-ffmpeg-{role}/"
        expected_names.update(prefix + name.removeprefix("sources/")
                              for name in item["archives"])
        expected_names.update(prefix + name for name in
                              ("license.txt", "readme.txt", "STUDIOFREE-SOURCE-README.txt"))
        expected_names.add(prefix + f"opencv-{item['opencv_commit']}.tar.gz")
    if len(records) != len(expected_names):
        raise ValueError("Compliance bundle file count mismatch")
    expected = set()
    for row in records:
        name = row["path"]
        if name in expected:
            raise ValueError("Duplicate compliance file")
        expected.add(name)
        path = checked(output / safe(name), output, row["sha256"])
        if path.stat().st_size != row["bytes"]:
            raise ValueError(f"Compliance file size mismatch: {name}")
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    if expected != expected_names or actual != expected | {"manifest.json"}:
        raise ValueError("Undeclared compliance file")
    if not internal_probe and any((payload / safe(name)).exists() for name in EXCLUDED_NATIVE):
        raise ValueError("Excluded NVIDIA binary present")
    with zipfile.ZipFile(payload / "blendcap.zip") as extension:
        for row in baseline["files"]:
            name = row["path"]
            checked(output / "Third-Party-Notices" / safe(name), output, row["sha256"])
            if name.startswith("payload/"):
                checked(payload / safe(name[8:]), payload, row["sha256"])
            elif name.startswith("blendcap-zip/"):
                if hashlib.sha256(extension.read(name[13:])).hexdigest() != row["sha256"]:
                    raise ValueError(f"Extension notice drift: {name}")
        with zipfile.ZipFile(output / "Corresponding-Source/public-integration.zip") as public_source:
            for name in extension.namelist():
                if name.endswith("/"):
                    continue
                if public_source.read("extension/" + name) != extension.read(name):
                    raise ValueError(f"Public corresponding source differs from extension: {name}")
            if any(name.startswith(("vta_core/", "engine/vta_core/"))
                   for name in public_source.namelist()):
                raise ValueError("Private engine source in public corresponding-source ZIP")
    for role, row in lock["opencv_ffmpeg"].items():
        checked(payload / safe(row["dll_path"]), payload, row["dll_sha256"])
        prefix = output / f"Corresponding-Source/opencv-ffmpeg-{role}"
        checked(prefix / f"opencv-{row['opencv_commit']}.tar.gz", output,
                row["opencv_archive_sha256"])
        for name, expected_hash in row["archives"].items():
            if role == "body" and "openh264-api-headers" in name:
                expected_hash = lock["openh264_2_5_headers_sha256"]
            checked(prefix / safe(name.removeprefix("sources/")), output, expected_hash)
    if not _license_candidates(payload) <= {row["path"] for row in baseline["files"]}:
        raise ValueError("Payload notice inventory drift")
    return manifest
