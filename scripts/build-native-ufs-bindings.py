#!/usr/bin/env python3
"""Append the iOS16 IOBlockStorageDevice address bindings to libNativeUfs.a.

The aliases are absolute symbols resolved from the 20H392 original kernelcache
and the Darwin22 native kernel (slide -0x1000000). They are only meaningful when
the matching Darwin22 luming kernel and iOS16 prelink closure are used.
"""
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
device = Path(__file__).resolve().parents[1]
source = device / "scripts" / "native-ufs-bindings.S"
archive = root / "build" / "native-ufs" / "libNativeUfs.a"
if not source.is_file():
    raise SystemExit(f"missing bindings source: {source}")
if not archive.is_file():
    raise SystemExit(f"NativeUfs builder must run first: {archive}")
obj = root / "build" / "native-ufs" / "bindings.o"
clang = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--find", "clang"], text=True).strip()
ar = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--find", "ar"], text=True).strip()
subprocess.run([clang, "-target", "arm64-apple-ios16.0", "-c", str(source), "-o", str(obj)], check=True)
subprocess.run([ar, "rcs", str(archive), str(obj)], check=True)
print(f"appended {obj.name} to {archive}")
