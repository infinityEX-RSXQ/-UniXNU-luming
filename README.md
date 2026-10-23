# UniXNU-luming

Device repository for running an **iOS 16.7.16 userland (iPhone10,3 / d22ap
identity, Darwin 22.6.0 / xnu-8796-era kernel)** on the **Xiaomi Civi 5 Pro
(codename `luming`, Qualcomm SM8735 / Snapdragon 8s Gen 4)**, using
[UniXNU](https://github.com/UniXNU/UniXNU) as the build driver and a separate
Mu-Silicium port (`uefi-luming`) as the firmware.

> **PRE-BUILD PHASE — READ THIS FIRST.**
> This repository is a validated device-repository skeleton, not a working port.
> **Nothing here has been built, flashed or booted on the phone.** The platform
> inputs were measured on the device on **2026-10-22** with a read-only capture
> (live device tree, `/proc/iomem`, dmesg, GPT, sysfs); the device was restored to
> unmodified stock afterwards. The values that only a boot of this port can provide
> (kernel prelink numbers, the DarwinBoot owned window, the root-volume UUID, the
> trust cache/Data seed, the platform seed, the UEFI FD base/size) are still marked
> `TODO(device)` with their procedure in [PORTING.md](PORTING.md). No macOS was used
> to produce this repository, and the user needs **no MacBook**: Windows does
> everything that does not need Xcode, and a GitHub Actions macOS runner does the
> Xcode-only steps (see [Windows-only, no Mac](#windows-only-no-macbook-needed)).
> Do not flash an image built from this repository before the remaining `TODO(device)`
> values are measured.

## Status at a glance

| Layer | State | Detail |
| --- | --- | --- |
| Device repository (this) | ✅ static checks pass (静态检查通过) | `scripts/check-luming.py` PASS; TOMLs parse; `unixnu defconfig`/`unixnu status` run |
| IPSW identity | ✅ verified | iPhone10,3 / 16.7.16 / 20H392 / d22ap / ApChipID 0x8015 / ApBoardID 0x06 (Apple BuildManifest + ipsw.me v4) |
| Hardware inputs | ✅ measured 2026-10-22 (read-only) | Live DTB + `/proc/iomem` + dmesg + GPT: platform addresses, CPU MPIDRs + 19.2 MHz timebase, GIC/UART/UFS windows, DRAM base + 12 GiB SKU, `userdata` GPT LBAs, splash region, boot image header v4, kernelcache pin |
| ADT generator | ✅ measured pass | Generates with the measured addresses (no placeholder flag); `--check` verifies them against the measured constants |
| Kernel, prelink | ⛔ not started | Darwin 22 archive risk; kernelcache pinned, but slide/roots/early_security_init are not demonstrated |
| UEFI firmware (uefi-luming) | ⛔ separate repo, not built | board `[firmware]` values provisional |
| Device boot | ⛔ never run | read-only measurement only; no flash, no boot — out of scope for this phase |

Detailed stage table: [docs/STATUS.md](docs/STATUS.md). Local validation evidence:
[docs/VALIDATION.md](docs/VALIDATION.md). Porting rationale and the full
TODO(device) list: [PORTING.md](PORTING.md).

## Layout

```
UniXNU-luming/
  board.toml                  board description (UniXNU device-repository schema)
  configs/luming_defconfig    kernel configuration (Darwin 22 flags + risk notes)
  scripts/make-luming-adt.py  Apple-flat-device-tree generator (--check support)
  scripts/luming_identity.py  D22AP identity bytes, generated from Apple's DeviceTree
  scripts/check-luming.py     preflight validator (static checks, TODO inventory)
  tools/gen-identity.py       regenerates luming_identity.py from the decoded tree
  tools/validate.ps1          Windows-local validation harness
  package/                    device kit skeleton (honest: no drivers yet)
  .github/workflows/macos-build.yml  GitHub Actions macOS runner (workflow_dispatch)
  docs/STATUS.md              stage table S0-S7, blockers
  docs/VALIDATION.md          exact validation commands and results
  feeds.toml, .gitmodules     extra sources / submodules (none yet)
```

## Windows-only, no MacBook needed

The user's requirement is explicitly *no Mac*. The split is:

| Where | What |
| --- | --- |
| **Windows host** | repository authoring, TOML/defconfig validation, ADT generation and `--check`, preflight (`scripts/check-luming.py`, `tools/validate.ps1`), firmware/package/boot image steps once their toolchain exists (MSYS2 or WSL2 for GNU make/clang/mtools/dtc/fdtput), and finally `fastboot` (manual, later, after approval) |
| **GitHub Actions macOS runner** | Xcode-only steps: `unixnu build kernel`, `userdata`, `payload` — via `.github/workflows/macos-build.yml` (`workflow_dispatch`, artifacts uploaded, no flash) |

No step in this table requires owning a Mac. See PORTING.md §6 for the exact
command sequences.

## Build (documented; not executed in this phase)

Windows pre-build validation:

```powershell
# bundled Python 3.11+ on Windows; no third-party packages needed
python UniXNU-luming\scripts\check-luming.py --list-todos
# ADT with the measured platform constants (no placeholder flag needed):
python UniXNU-luming\scripts\make-luming-adt.py --output $env:TEMP\luming.adt `
  --dram-size 0x300000000 --packed-data-ramdisk --trust-cache
python UniXNU-luming\scripts\make-luming-adt.py --check --output $env:TEMP\luming.adt `
  --dram-size 0x300000000 --packed-data-ramdisk --trust-cache
pwsh -File UniXNU-luming\tools\validate.ps1
```

Select the board from a UniXNU checkout (this part runs on Windows too):

```powershell
cd UniXNU-main\UniXNU-main
python unixnu defconfig luming_defconfig -D ..\..\UniXNU-luming --out ..\..\out-luming
python unixnu status -D ..\..\UniXNU-luming --out ..\..\out-luming
```

Xcode-only steps (GitHub Actions → *macOS build (luming)* → Run workflow):

```
steps: kernel userdata payload
defconfig: luming_defconfig
device_repository: <owner>/UniXNU-luming
```

Or on any Mac with Xcode, from the UniXNU checkout:

```sh
./unixnu defconfig luming_defconfig -D ../UniXNU-luming
./unixnu fetch
./unixnu ipsw fetch
./unixnu build kernel
./unixnu build userdata
./unixnu build payload --unique-chip-id <hex> --trust-cache <bundle> --data-ramdisk <seed>
./unixnu bootimg --uefi ../uefi-luming --dtb <luming.dtb>
```

The platform seed (`--unique-chip-id`), trust cache bundle and Data seed are
device inputs and are never stored in this repository.

## Flashing

**Out of scope for this phase.** When hardware work is approved, the manual
sequence is expected to be (do not run it yet):

```sh
fastboot boot out/luming/boot/boot.img
# only after the pre-build gates in docs/STATUS.md are cleared:
fastboot flash userdata out/luming/userdata/userdata-root.img
fastboot reboot bootloader
fastboot boot out/luming/boot/boot.img
```

## Known caveats

- The iOS 16.7.16 IPSW (20H392) ships Darwin 22.6.0 (`xnu-8796.142.1.703.8~1`);
  the public source reference is `xnu-8796.141.3`. The prelink of that kernelcache
  against the native kernel **failed** on the closest documented iOS 16 port
  (`refs/UniXNU-guacamole/NOTES.md:197-210`), which shipped a monolithic kernel
  instead. `[kernel.prelink]` values are therefore absent, not approximated.
- Darwin 22 platform archives are still in progress in the community route
  (`docs/devices/nuwa.md:14-15`); `luming_defconfig` enables only documented flags
  and leaves the owner-path flags off.
- The ADT's platform values are **measured on the device on 2026-10-22** (live device
  tree + `/proc/iomem` + dmesg): DRAM base `0x80000000`, GIC distributor
  `0x17100000/0x10000`, redistributor `0x17180000/0x200000`, UART `0xA9C000/0x4000`,
  UFS `0x1D84000/0x3000`, timebase 19,200,000 Hz and MPIDRs `0,0x100..0x700`. The
  tool generates a complete tree without `--todo-placeholder-addresses` and `--check`
  verifies every measured window; it still refuses to fabricate a value if a constant
  is unset, and the placeholder flag remains only for structural encoding tests
  (never bootable).
- Those values come from the **stock Android device tree/GPT/dmesg, not from a boot of
  XNU**. The kernel has never executed on this board: the platform values are hardware
  facts but are not yet hardware-verified by this port (docs/STATUS.md).
- The 20H392 kernelcache is pinned in `[kernel.prelink] kernelcache_sha256` (sha256 of
  the decompressed arm64 Mach-O, 50,937,856 B) so the IPSW extraction is checked; the
  prelink slide/roots/early_security_init remain absent because the Darwin 22 prelink
  is not demonstrated.
- `[bootimg.image]` records the measured stock boot image header v4 geometry
  (pagesize 4096, base 0x0, kernel_offset 0x8000, ramdisk_offset 0x01000000,
  tags_offset 0x100), plus the measured `cont_splash_region` reserved-memory node.
  The base driver's `unixnu bootimg` unpack verification was v0-v2-oriented; the local
  patch `patches/unixnu-bootimg-header-v4.patch` adds the v4 verify path and is covered by
  a host-side synthetic test (pack/verify/tampered-signature rejection, PASS). It does not
  make any image bootable and was not executed on a real boot image in this phase.
- The first GitHub Actions macOS run is expected to **fail fast** at the remaining
  gates: `build kernel` stops on the missing Darwin-22 platform archives and the
  absent prelink slide, `build payload` stops on the platform seed / trust cache /
  Data seed. The unmeasured-userdata-LBA gate is closed by the 2026-10-22 GPT
  measurement, and so are the ADT platform-address and kernelcache-pin gates. That is
  the intended pre-build behaviour.
- Files the `unixnu` driver reads (`board.toml`, `configs/luming_defconfig`,
  `feeds.toml`) are ASCII-only: `scripts/unixnu/config.py` reads them without an
  explicit encoding, which breaks on Windows GBK hosts if non-ASCII bytes appear
  (finding recorded in docs/VALIDATION.md §4).

## License

Follows UniXNU's licensing (BSD-2-Clause-Patent for UniXNU code). The D22AP
identity bytes in `scripts/luming_identity.py` are factual hardware-description
data decoded from the user's own target IPSW for interoperability. Proprietary
inputs (kernelcache, trust cache, Data seed, platform seed) are **not** part of
this repository.
