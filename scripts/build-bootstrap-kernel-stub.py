#!/usr/bin/env python3
"""Append the bootstrap-only IOPCIMessageFromMach stub to one linked archive.

The original 20H392 kernelcache exports IOPCIMessageFromMach but the community xnu-8796
source kernel does not. NativePrelink refuses to bind the original kext pointer without
it. The stub is an empty return so prelink can proceed; it is not a functional
IOPCIFamily implementation.

Only one archive may carry the stub. libArmDTPlatform.a is linked whenever
CONFIG_XNU_ARM_DT_PLATFORM=1 (required by this board), so append it there.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build" / "native-aes"
out.mkdir(parents=True, exist_ok=True)
archive = ROOT / "build" / "armdt-platform" / "libArmDTPlatform.a"

source = out / "bootstrap_kernel_stubs.s"
source.write_text(
    ".text\n"
    ".p2align 2\n"
    ".globl _IORPCMessageFromMach\n"
    ".globl IORPCMessageFromMach\n"
    ".globl __IORPCMessageFromMach\n"
    "_IORPCMessageFromMach:\n"
    "IORPCMessageFromMach:\n"
    "__IORPCMessageFromMach:\n"
    "\tret\n",
    encoding="utf-8",
)
object_path = out / "bootstrap_kernel_stubs.o"
subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios17.0",
                "-c", str(source), "-o", str(object_path)], check=True)

if not archive.is_file():
    raise SystemExit(f"missing {archive}; the ArmDTPlatform archive must be built first")
subprocess.run(["xcrun", "ar", "rcs", archive, object_path], check=True)
print(f"PASS: appended bootstrap IOPCIMessageFromMach stub to {archive}")
