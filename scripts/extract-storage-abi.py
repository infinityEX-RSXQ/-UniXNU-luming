#!/usr/bin/env python3
"""Extract the public IOBlockStorageDevice method list on the macOS runner.

Runs after the kernel build, while refer/IOStorageFamily and the XNU compile record
exist. It does NOT need the live-guest metadata that the full storage audit uses; it
only compiles the public reference object and records its vtable method list for the
offline binding pass.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
source = ROOT / "refer" / "IOStorageFamily"
record_path = ROOT / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json"
if not source.is_dir() or not (source / "IOBlockStorageDevice.cpp").is_file():
    raise SystemExit(f"missing IOStorageFamily source: {source}")
if not record_path.is_file():
    raise SystemExit(f"missing XNU compile record: {record_path}")

out = ROOT / "out/luming/storage-abi"
out.mkdir(parents=True, exist_ok=True)
headers = out / "include/IOKit/storage"
headers.mkdir(parents=True, exist_ok=True)
for path in source.glob("*.h"):
    (headers / path.name).write_bytes(path.read_bytes())

sdk = Path(subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip())
(out / "include/TargetConditionals.h").write_bytes((sdk / "usr/include/TargetConditionals.h").read_bytes())

record = json.loads(record_path.read_text().strip().rstrip(","))
obj_path = out / "IOBlockStorageDevice-reference.o"
arguments = record["arguments"]
command, index = [], 0
while index < len(arguments):
    value = arguments[index]
    if value == record["file"]:
        command.append(str(source / "IOBlockStorageDevice.cpp"))
    elif value == "-o":
        command += ["-o", str(obj_path)]
        index += 1
    elif value in ("-MF", "-MJ"):
        index += 1
    elif value == "-Xclang" and arguments[index + 1].startswith("-W"):
        index += 1
    elif not value.startswith("-W") and value not in ("-MD", "-MP"):
        command.append(value)
    index += 1
command += ["-I", str(out / "include"), "-I", str(ROOT / ".cache/xnu-work/osfmk"),
            "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-deprecated-declarations",
            "-Wno-c99-designator", "-Wno-reorder-init-list", "-Wno-unnecessary-virtual-specifier",
            "-Wno-deprecated-enum-enum-conversion"]
(out / "compile-command.json").write_text(json.dumps(command, indent=2) + "\n")
result = subprocess.run(command, cwd=record["directory"], capture_output=True, text=True)
(out / "compile.log").write_text(result.stdout + result.stderr)
if result.returncode:
    raise SystemExit((result.stdout + result.stderr)[-3500:])

obj = obj_path.read_bytes()
magic, cpu, subtype, kind, count, size = struct.unpack_from("<6I", obj, 0)
if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
    raise SystemExit("unexpected reference object")
offset = 32
sycmd = None
for _ in range(count):
    cmd, length = struct.unpack_from("<II", obj, offset)
    if cmd == 2:
        sycmd = offset
    offset += length
if sycmd is None:
    raise SystemExit("no symbol table in reference object")
symoff, symcount, stroff, strsize = struct.unpack_from("<4I", obj, sycmd + 8)
strings = obj[stroff:stroff + strsize]
symbols = []
for index in range(symcount):
    strx, nkind, _, _, value = struct.unpack_from("<IBBHQ", obj, symoff + index * 16)
    end = strings.find(b"\0", strx)
    name = strings[strx:end].decode() if end >= 0 else ""
    symbols.append(dict(name=name, value=value, kind=nkind))
vtable = next(s["value"] for s in symbols if s["name"] == "__ZTV20IOBlockStorageDevice")
end = min(s["value"] for s in symbols if not s["kind"] & 0xE0 and s["kind"] & 0xE == 0xE and s["value"] > vtable)
relocations = {}
# Reuse the public audit relocation reader.
sys.path.insert(0, str(ROOT / "kits/NativePrelink"))
import native_prelink as np  # noqa: E402
image = np.macho(obj)
for segment in image["segments"]:
    for section in segment["sections"]:
        if section["va"] <= vtable < section["va"] + section["size"]:
            ro, rc = struct.unpack_from("<II", obj, section["command_offset"] + 56)
            for index in range(rc):
                address, word = struct.unpack_from("<II", obj, ro + index * 8)
                if word >> 27 & 1 and word >> 28 == 0 and word >> 25 & 3 == 3:
                    relocations[section["va"] + address] = symbols[word & 0xFFFFFF]["name"]
methods = []
for at in range(vtable + 16, end, 8):
    if at not in relocations:
        break
    methods.append(relocations[at])
report = dict(source_revision=subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
              methods=methods, slots=len(methods), reference_object_sha256=hashlib.sha256(obj).hexdigest(),
              source_sha256=hashlib.sha256((source / "IOBlockStorageDevice.cpp").read_bytes()).hexdigest())
(out / "methods.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"PASS: {len(methods)} IOBlockStorageDevice method slots -> {out/'methods.json'}")
