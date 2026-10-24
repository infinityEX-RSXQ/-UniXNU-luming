#!/usr/bin/env python3
"""Append the bootstrap-only IOPCIMessageFromMach stub to the final NativeAES archive.

build-native-aes-bindings.py rebuilds libNativeAES.a after build-arm-driver.py, so the
stub must be appended after the bindings step. The stub is an empty kernel export that
lets NativePrelink bind the original kernelcache pointer; it is not a functional
IOPCIFamily implementation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build" / "native-aes"
out.mkdir(parents=True, exist_ok=True)
archives = [
    ROOT / "build" / "native-aes" / "libNativeAES.a",
    ROOT / "build" / "armdt-platform" / "libArmDTPlatform.a",
    ROOT / "build" / "armcpu" / "libArmCpu.a",
]

source = out / "bootstrap_kernel_stubs.s"
source.write_text(
    ".text\n"
    ".globl _IOPCIMessageFromMach\n"
    ".globl IOPCIMessageFromMach\n"
    "_IOPCIMessageFromMach:\n"
    "IOPCIMessageFromMach:\n"
    "\tret\n",
    encoding="utf-8",
)
object_path = out / "bootstrap_kernel_stubs.o"
subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios17.0",
                "-c", str(source), "-o", str(object_path)], check=True)

for archive in archives:
    if not archive.is_file():
        print(f"skip missing {archive}")
        continue
    subprocess.run(["xcrun", "ar", "rcs", archive, object_path], check=True)
    print(f"PASS: appended bootstrap IOPCIMessageFromMach stub to {archive}")
