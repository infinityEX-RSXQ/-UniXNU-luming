#!/usr/bin/env python3
"""Compile an iOS16 IOBlockStorageDevice reference object against the Darwin22 tree.

Runs inside the Darwin22 source-kernel GitHub Actions job, after the kernel build.
Uses the build's real XNU compile record and the public IOStorageFamily source.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

WORK = Path(os.environ["RUNNER_TEMP"]) / "nuwa-8796-source"
TAG = "IOStorageFamily-299"
URL = f"https://github.com/apple-oss-distributions/IOStorageFamily/archive/refs/tags/{TAG}.zip"
OUT = WORK / "reports" / "storage-abi-8796"
OUT.mkdir(parents=True, exist_ok=True)
SOURCE_ZIP = OUT / f"{TAG}.zip"
SOURCE_DIR = OUT / "source"

if not SOURCE_ZIP.is_file():
    urllib.request.urlretrieve(URL, SOURCE_ZIP)
if not SOURCE_DIR.is_dir():
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        archive.extractall(SOURCE_DIR)
source_root = next(p for p in SOURCE_DIR.iterdir() if p.is_dir())
cpp = source_root / "IOBlockStorageDevice.cpp"

headers = OUT / "include" / "IOKit" / "storage"
headers.mkdir(parents=True, exist_ok=True)
for header in source_root.glob("*.h"):
    shutil.copyfile(header, headers / header.name)
sdk = Path(subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip())
shutil.copyfile(sdk / "usr/include/TargetConditionals.h", OUT / "include" / "TargetConditionals.h")

record = json.loads((WORK / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json").read_text().strip().rstrip(","))
obj = OUT / "IOBlockStorageDevice-reference.o"
command, index = [], 0
arguments = record["arguments"]
while index < len(arguments):
    value = arguments[index]
    if value == record["file"]:
        command.append(str(cpp))
    elif value == "-o":
        command += ["-o", str(obj)]
        index += 1
    elif value in ("-MF", "-MJ"):
        index += 1
    elif value == "-Xclang" and arguments[index + 1].startswith("-W"):
        index += 1
    elif not value.startswith("-W") and value not in ("-MD", "-MP"):
        command.append(value)
    index += 1
command += ["-I", str(OUT / "include"), "-I", str(WORK / ".cache/xnu-work/osfmk"),
            "-Wall", "-Wextra", "-Werror", "-Wno-unknown-warning-option", "-Wno-unused-parameter",
            "-Wno-deprecated-declarations", "-Wno-c99-designator", "-Wno-reorder-init-list",
            "-Wno-unnecessary-virtual-specifier", "-Wno-deprecated-enum-enum-conversion"]
(OUT / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
result = subprocess.run(command, cwd=record["directory"], capture_output=True, text=True)
(OUT / "compile.log").write_text(result.stdout + result.stderr)
if result.returncode:
    raise SystemExit((result.stdout + result.stderr)[-4000:])

obj_bytes = obj.read_bytes()
sycmd = None
offset = 32
magic, cpu, subtype, kind, count, size = struct.unpack_from("<6I", obj_bytes, 0)
for _ in range(count):
    cmd, length = struct.unpack_from("<II", obj_bytes, offset)
    if cmd == 2:
        sycmd = offset
    offset += length
symoff, symcount, stroff, strsize = struct.unpack_from("<4I", obj_bytes, sycmd + 8)
strings = obj_bytes[stroff:stroff + strsize]
symbols = []
for i in range(symcount):
    strx, nkind, _, _, value = struct.unpack_from("<IBBHQ", obj_bytes, symoff + i * 16)
    end = strings.find(b"\0", strx)
    name = strings[strx:end].decode() if end >= 0 else ""
    symbols.append(dict(name=name, value=value, kind=nkind))
vtable = next(s["value"] for s in symbols if s["name"] == "__ZTV20IOBlockStorageDevice")
end = min(s["value"] for s in symbols if not s["kind"] & 0xE0 and s["kind"] & 0xE == 0xE and s["value"] > vtable)
# Parse the Mach-O section that holds the vtable directly: the Darwin22 work
# tree has no kits/NativePrelink checkout, so do not import native_prelink.
sections = []
offset = 32
for _ in range(count):
    cmd, length = struct.unpack_from("<II", obj_bytes, offset)
    if cmd == 0x19:
        nsects = struct.unpack_from("<I", obj_bytes, offset + 64)[0]
        section_at = offset + 72
        for _ in range(nsects):
            addr, size = struct.unpack_from("<QQ", obj_bytes, section_at + 32)
            reloff, nreloc = struct.unpack_from("<II", obj_bytes, section_at + 56)
            sections.append((addr, size, reloff, nreloc))
            section_at += 80
    offset += length
relocations = {}
for addr, size, reloff, nreloc in sections:
    if addr <= vtable < addr + size:
        for i in range(nreloc):
            address, word = struct.unpack_from("<II", obj_bytes, reloff + i * 8)
            if word >> 27 & 1 and word >> 28 == 0 and word >> 25 & 3 == 3:
                relocations[addr + address] = symbols[word & 0xFFFFFF]["name"]
        break
methods = []
for at in range(vtable + 16, end, 8):
    if at not in relocations:
        break
    methods.append(relocations[at])
report = dict(tag=TAG, methods=methods, slots=len(methods),
              reference_object_sha256=hashlib.sha256(obj_bytes).hexdigest(),
              source_sha256=hashlib.sha256(cpp.read_bytes()).hexdigest(),
              xnu_version="8796.141.3", darwin="22.6.0")
(OUT / "methods.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"PASS: {len(methods)} iOS16 IOBlockStorageDevice slots -> {OUT/'methods.json'}")
