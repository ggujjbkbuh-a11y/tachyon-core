#!/usr/bin/env python3
"""Repackage an upstream static aarch64 tachyon-core binary as an OpenWrt IPK."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import re
import struct
import tarfile
from pathlib import Path

ARCH = "aarch64_cortex-a53"
PACKAGE_NAME = "tachyon-core"
BINARY_PATH = "./usr/bin/tachyon-core"
SING_BOX_LINK = "./usr/bin/sing-box"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def read_ipk(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files = {
            member.name: archive.extractfile(member).read()
            for member in archive
            if member.isfile()
        }
    required = {"debian-binary", "control.tar.gz", "data.tar.gz"}
    if files.keys() != required:
        raise ValueError(f"{path} has unexpected IPK members: {sorted(files)}")
    if files["debian-binary"] != b"2.0\n":
        raise ValueError(f"{path} has an unsupported IPK format")
    return files


def rewrite_tar_gz(blob: bytes, replace_file) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as gz:
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.USTAR_FORMAT) as target:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as source:
                for member in source:
                    info = copy.copy(member)
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = 0
                    body = source.extractfile(member).read() if member.isfile() else None
                    if body is not None:
                        body = replace_file(info.name, body)
                        info.size = len(body)
                    target.addfile(info, io.BytesIO(body) if body is not None else None)
        gz.write(tar_bytes.getvalue())
    return output.getvalue()


def control_fields(control: bytes) -> dict[str, str]:
    fields = {}
    for line in control.decode().splitlines():
        if line and not line[0].isspace() and ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


def make_ipk(
    base_path: Path,
    binary_path: Path,
    version: str,
    package_release: int,
    output_path: Path,
) -> None:
    if not VERSION_RE.fullmatch(version) or package_release < 1:
        raise ValueError(f"invalid package version: {version}-{package_release}")
    parts = read_ipk(base_path)
    binary = binary_path.read_bytes()
    if f"tachyon-core {version}".encode() not in binary:
        raise ValueError("binary does not contain its expected tachyon-core version marker")
    if b"naive+https://" not in binary or b"/parse" not in binary:
        raise ValueError("binary is missing the fork's NaiveProxy Clash API parser")

    old_control = b""
    with tarfile.open(fileobj=io.BytesIO(parts["control.tar.gz"]), mode="r:gz") as archive:
        for member in archive:
            if member.name in ("./control", "control"):
                old_control = archive.extractfile(member).read()
                break
    fields = control_fields(old_control)
    if fields.get("Package") != PACKAGE_NAME or fields.get("Architecture") != ARCH:
        raise ValueError("base IPK is not the expected aarch64_cortex-a53 tachyon-core package")

    old_payload = parts["data.tar.gz"]
    old_binary_size = 0
    with tarfile.open(fileobj=io.BytesIO(old_payload), mode="r:gz") as archive:
        binary_member = archive.getmember(BINARY_PATH)
        old_binary_size = binary_member.size
    # Keep the base package's non-file allocation overhead while accounting for
    # the replacement binary's actual installed size.
    installed_size = int(fields["Installed-Size"]) + len(binary) - old_binary_size

    parts["data.tar.gz"] = rewrite_tar_gz(
        old_payload,
        lambda name, content: binary if name == BINARY_PATH else content,
    )

    def update_control(name: str, content: bytes) -> bytes:
        if name not in ("./control", "control"):
            return content
        text = content.decode()
        text, count = re.subn(
            r"(?m)^Version: .+$",
            f"Version: {version}-{package_release}",
            text,
        )
        if count != 1:
            raise ValueError("base control file must contain exactly one Version field")
        text, count = re.subn(
            r"(?m)^Installed-Size: .+$",
            f"Installed-Size: {installed_size}",
            text,
        )
        if count != 1:
            raise ValueError("base control file must contain exactly one Installed-Size field")
        return text.encode()

    parts["control.tar.gz"] = rewrite_tar_gz(parts["control.tar.gz"], update_control)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_ipk(output_path, parts)
    verify_ipk(output_path, version, package_release, binary_path)


def write_ipk(path: Path, parts: dict[str, bytes]) -> None:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as gz:
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name in ("debian-binary", "data.tar.gz", "control.tar.gz"):
                content = parts[name]
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o644
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                member.mtime = 0
                archive.addfile(member, io.BytesIO(content))
        gz.write(tar_bytes.getvalue())
    path.write_bytes(output.getvalue())


def verify_ipk(
    path: Path,
    version: str,
    package_release: int,
    expected_binary_path: Path | None = None,
) -> None:
    parts = read_ipk(path)
    with tarfile.open(fileobj=io.BytesIO(parts["control.tar.gz"]), mode="r:gz") as control_archive:
        control_member = next(
            member for member in control_archive if member.name in ("./control", "control")
        )
        fields = control_fields(control_archive.extractfile(control_member).read())
    if fields.get("Package") != PACKAGE_NAME:
        raise ValueError("IPK package name is incorrect")
    if fields.get("Version") != f"{version}-{package_release}":
        raise ValueError(
            f"IPK version is {fields.get('Version')}, expected {version}-{package_release}"
        )
    if fields.get("Architecture") != ARCH:
        raise ValueError(f"IPK architecture is {fields.get('Architecture')}, expected {ARCH}")
    with tarfile.open(fileobj=io.BytesIO(parts["data.tar.gz"]), mode="r:gz") as data_archive:
        binary_member = data_archive.getmember(BINARY_PATH)
        binary = data_archive.extractfile(binary_member).read()
        if not any(member.name == SING_BOX_LINK and member.issym() for member in data_archive):
            raise ValueError("IPK is missing the sing-box compatibility symlink")
    if binary[:4] != b"\x7fELF" or binary[4] != 2 or struct.unpack("<H", binary[18:20])[0] != 183:
        raise ValueError("IPK payload is not a 64-bit little-endian AArch64 ELF binary")
    if f"tachyon-core {version}".encode() not in binary:
        raise ValueError("IPK binary version marker does not match package version")
    if b"naive+https://" not in binary or b"/parse" not in binary:
        raise ValueError("IPK binary is missing the fork's NaiveProxy Clash API parser")
    if expected_binary_path and hashlib.sha256(binary).digest() != hashlib.sha256(
        expected_binary_path.read_bytes()
    ).digest():
        raise ValueError("IPK binary payload differs from the verified upstream binary")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a new package from a verified binary")
    build.add_argument("--base-package", type=Path, required=True)
    build.add_argument("--binary", type=Path, required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--package-release", type=int, default=1)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify package metadata and binary")
    verify.add_argument("package", type=Path)
    verify.add_argument("--version", required=True)
    verify.add_argument("--package-release", type=int, default=1)
    verify.add_argument("--binary", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        make_ipk(
            args.base_package,
            args.binary,
            args.version,
            args.package_release,
            args.output,
        )
    else:
        verify_ipk(args.package, args.version, args.package_release, args.binary)
    print(f"{args.command} OK: {args.version}-{args.package_release}")


if __name__ == "__main__":
    main()
