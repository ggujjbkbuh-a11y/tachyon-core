#!/usr/bin/env python3
"""Apply the fork's NaiveProxy overlay to upstream Tachyon release IPKs."""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PACKAGE_SPECS = {
    "tachyon": {
        "patch": "tachyon-naiveproxy-package.patch.gz",
        "files": {
            "usr/lib/tachyon/subscription/parser.uc": (
                b'starts_with(line, "naive+https://")',
                b"function process_naive(",
            ),
            "usr/lib/tachyon/singbox/generator_outbounds.uc": (
                b'if (scheme == "naive+https")',
                b"function manual_naive_outbound(",
            ),
        },
    },
    "luci-app-tachyon": {
        "patch": "luci-naiveproxy-package.patch.gz",
        "files": {
            "www/luci-static/resources/view/tachyon/main.js": (
                b"function validateNaiveUrl(",
                b'prefixes: ["naive+https://"]',
            ),
            "www/luci-static/resources/view/tachyon/section.js": (
                b'case "naive":',
                b"NaiveProxy",
            ),
        },
    },
    "luci-i18n-tachyon-ru": {"patch": None, "files": {}},
}
DEFAULT_PATCH_DIR = Path(__file__).resolve().parents[1] / "patches"


def normalized_name(name: str) -> str:
    return name.removeprefix("./")


def read_ipk(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files = {
            normalized_name(member.name): archive.extractfile(member).read()
            for member in archive
            if member.isfile()
        }
    required = {"debian-binary", "control.tar.gz", "data.tar.gz"}
    if files.keys() != required:
        raise ValueError(f"{path} has unexpected IPK members: {sorted(files)}")
    if files["debian-binary"] != b"2.0\n":
        raise ValueError(f"{path} has an unsupported IPK format")
    return files


def control_fields(blob: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        member = next(
            (
                item
                for item in archive
                if normalized_name(item.name) == "control" and item.isfile()
            ),
            None,
        )
        if member is None:
            raise ValueError("IPK is missing its control file")
        content = archive.extractfile(member).read().decode()

    fields = {}
    for line in content.splitlines():
        if line and not line[0].isspace() and ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


def payload_files(blob: bytes) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        result = {}
        for member in archive:
            name = normalized_name(member.name)
            if name in result:
                raise ValueError(f"IPK contains duplicate payload path: {name}")
            content = archive.extractfile(member).read() if member.isfile() else b""
            result[name] = (copy.copy(member), content)
    return result


def patch_targets(patch: bytes) -> list[str]:
    targets = []
    for line in patch.decode().splitlines():
        if line.startswith("+++ b/"):
            target = line[6:]
            path = Path(target)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe path in NaiveProxy patch: {target}")
            targets.append(target)
    if not targets:
        raise ValueError("NaiveProxy patch contains no target files")
    return targets


def apply_patch(
    payload: dict[str, tuple[tarfile.TarInfo, bytes]],
    patch_path: Path,
    staging: Path,
) -> dict[str, bytes]:
    patch = gzip.decompress(patch_path.read_bytes())
    targets = patch_targets(patch)
    for target in targets:
        if target not in payload or not payload[target][0].isfile():
            raise ValueError(f"upstream IPK is missing patch target: {target}")
        destination = staging / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload[target][1])

    result = subprocess.run(
        ["patch", "--batch", "--forward", "--fuzz=0", "-p1"],
        cwd=staging,
        input=patch,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + result.stderr).decode(errors="replace").strip()
        raise ValueError(f"NaiveProxy patch did not apply cleanly:\n{details}")
    return {target: (staging / target).read_bytes() for target in targets}


def rewrite_tar_gz(blob: bytes, replacements: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    seen = set()
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as gz:
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.GNU_FORMAT) as target:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as source:
                for member in source:
                    info = copy.copy(member)
                    name = normalized_name(info.name)
                    if name in replacements:
                        body = replacements[name]
                        info.size = len(body)
                        seen.add(name)
                    elif member.isfile():
                        body = source.extractfile(member).read()
                    else:
                        body = None
                    target.addfile(info, io.BytesIO(body) if body is not None else None)
        gz.write(tar_bytes.getvalue())
    missing = replacements.keys() - seen
    if missing:
        raise ValueError(f"could not update package payload files: {sorted(missing)}")
    return output.getvalue()


def write_ipk(path: Path, parts: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as gz:
            tar_bytes = io.BytesIO()
            with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name in ("debian-binary", "data.tar.gz", "control.tar.gz"):
                    content = parts[name]
                    member = tarfile.TarInfo(f"./{name}")
                    member.size = len(content)
                    member.mode = 0o644
                    member.uid = member.gid = 0
                    member.uname = member.gname = "root"
                    member.mtime = 0
                    archive.addfile(member, io.BytesIO(content))
            gz.write(tar_bytes.getvalue())


def read_payload_content(blob: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        member = next(
            (item for item in archive if normalized_name(item.name) == name and item.isfile()),
            None,
        )
        if member is None:
            raise ValueError(f"IPK payload is missing required file: {name}")
        return archive.extractfile(member).read()


def package_version(
    path: Path,
    name: str,
    version: str,
    package_release: int = 0,
) -> dict[str, bytes]:
    parts = read_ipk(path)
    fields = control_fields(parts["control.tar.gz"])
    if fields.get("Package") != name:
        raise ValueError(f"{path} contains package {fields.get('Package')}, expected {name}")
    expected_version = package_version_string(version, package_release)
    if fields.get("Version") != expected_version:
        raise ValueError(
            f"{path} contains version {fields.get('Version')}, expected {expected_version}"
        )
    if fields.get("Architecture") != "all":
        raise ValueError(f"{path} has unexpected architecture {fields.get('Architecture')}")
    return parts


def package_version_string(version: str, package_release: int) -> str:
    return f"{version}-{package_release}" if package_release else version


def package_filename(package_name: str, version: str, package_release: int) -> str:
    package_version = package_version_string(version, package_release)
    return f"{package_name}_{package_version}.ipk"


def set_package_release(parts: dict[str, bytes], version: str, package_release: int) -> None:
    if package_release == 0:
        return
    control_content = read_payload_content(parts["control.tar.gz"], "control")
    current = re.findall(rb"(?m)^Version: ([^\r\n]+)$", control_content)
    if current != [version.encode()]:
        raise ValueError(f"upstream package control must contain exactly Version: {version}")
    replacement = re.sub(
        rb"(?m)^Version: [^\r\n]+$",
        f"Version: {package_version_string(version, package_release)}".encode(),
        control_content,
    )
    parts["control.tar.gz"] = rewrite_tar_gz(
        parts["control.tar.gz"],
        {"control": replacement},
    )


def overlay_is_present(parts: dict[str, bytes], package_name: str) -> bool:
    spec = PACKAGE_SPECS[package_name]
    return all(
        all(marker in read_payload_content(parts["data.tar.gz"], path) for marker in markers)
        for path, markers in spec["files"].items()
    )


def prepare_package(
    input_path: Path,
    output_path: Path,
    package_name: str,
    version: str,
    package_release: int,
    patch_dir: Path,
) -> None:
    parts = package_version(input_path, package_name, version)
    spec = PACKAGE_SPECS[package_name]
    if spec["patch"] and not overlay_is_present(parts, package_name):
        payload = payload_files(parts["data.tar.gz"])
        with tempfile.TemporaryDirectory(prefix="tachyon-naiveproxy-") as directory:
            replacements = apply_patch(payload, patch_dir / spec["patch"], Path(directory))
        parts["data.tar.gz"] = rewrite_tar_gz(parts["data.tar.gz"], replacements)
    set_package_release(parts, version, package_release)
    write_ipk(output_path, parts)
    verify_package(output_path, package_name, version, package_release)


def verify_package(
    path: Path,
    package_name: str,
    version: str,
    package_release: int = 0,
) -> None:
    parts = package_version(path, package_name, version, package_release)
    spec = PACKAGE_SPECS[package_name]
    for payload_path, markers in spec["files"].items():
        content = read_payload_content(parts["data.tar.gz"], payload_path)
        for marker in markers:
            if marker not in content:
                raise ValueError(f"{path} is missing required NaiveProxy marker {marker!r}")


def build(
    upstream_dir: Path,
    output_dir: Path,
    version: str,
    package_release: int,
    patch_dir: Path,
) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid upstream version: {version}")
    if package_release < 0:
        raise ValueError(f"invalid package release: {package_release}")
    for package_name in PACKAGE_SPECS:
        upstream_filename = f"{package_name}_{version}.ipk"
        filename = package_filename(package_name, version, package_release)
        prepare_package(
            upstream_dir / upstream_filename,
            output_dir / filename,
            package_name,
            version,
            package_release,
            patch_dir,
        )
        print(f"built and verified {filename}")


def verify(directory: Path, version: str, package_release: int) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid upstream version: {version}")
    if package_release < 0:
        raise ValueError(f"invalid package release: {package_release}")
    for package_name in PACKAGE_SPECS:
        path = directory / package_filename(package_name, version, package_release)
        verify_package(path, package_name, version, package_release)
    print(
        f"verify OK: Tachyon and LuCI "
        f"{package_version_string(version, package_release)}, including NaiveProxy support"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build", help="overlay fork changes onto upstream IPKs")
    build_command.add_argument("--upstream-dir", type=Path, required=True)
    build_command.add_argument("--output-dir", type=Path, required=True)
    build_command.add_argument("--version", required=True)
    build_command.add_argument("--package-release", type=int, default=0)
    build_command.add_argument("--patch-dir", type=Path, default=DEFAULT_PATCH_DIR)
    verify_command = commands.add_parser("verify", help="verify package metadata and NaiveProxy")
    verify_command.add_argument("--directory", type=Path, required=True)
    verify_command.add_argument("--version", required=True)
    verify_command.add_argument("--package-release", type=int, default=0)
    args = parser.parse_args()
    if args.command == "build":
        build(
            args.upstream_dir,
            args.output_dir,
            args.version,
            args.package_release,
            args.patch_dir,
        )
    else:
        verify(args.directory, args.version, args.package_release)


if __name__ == "__main__":
    try:
        main()
    except (OSError, tarfile.TarError, ValueError) as error:
        raise SystemExit(f"repackage_tachyon: {error}") from error
