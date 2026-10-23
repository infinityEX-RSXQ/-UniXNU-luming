# Porting notes — Xiaomi Civi 5 Pro (`luming`, SM8735) → iOS 16.7.16 userland

This document records the **rationale**, the **sourced facts**, the **unmeasured
inputs** and the **execution plan** of `UniXNU-luming`. It is deliberately explicit
about what is not known: the repository is pre-build and must not be mistaken for a
working port.

---

## 1. Target pairing and why it is unusual

| Role | Choice | Evidence |
| --- | --- | --- |
| Phone | Xiaomi Civi 5 Pro, codename `luming`, SoC SM8735 / Snapdragon 8s Gen 4 | Android recovery device tree `refs/android_device_xiaomi_luming-recovery/BoardConfig.mk` (`TARGET_BOARD_PLATFORM := sm8735`, `DEVICE_PATH := device/xiaomi/luming`) |
| Userland | iOS **16.7.16**, build **20H392**, product **iPhone10,3**, device class **d22ap** (iPhone X), platform **t8015** | Apple BuildManifest/Restore.plist read by HTTP range: `refs/ios16-ipsw/BuildManifest-summary.json`, `Restore.plist`; ipsw.me v4 cross-check (`ApChipID 0x8015`, `ApBoardID 0x06`) |
| Target kernel | Darwin **22.6.0**, kernelcache `xnu-8796.142.1.703.8~1` | `refs/UniXNU-guacamole/NOTES.md:182-184` (kernelcache extracted: 18,204,358 B IM4P/LZFSE → 50,937,884 B arm64 Mach-O) |
| Source kernel | public `xnu-8796.141.3` reference | community iOS 16 route, e.g. `refs/UniXNU-nuwa/`; UniXNU mainline targets Darwin 23 / `xnu-10063`, so this port is the iOS 16 branch of the effort |
| Firmware | Mu-Silicium port `uefi-luming` (separate repository) | scaffolded by the uefi-builder teammate. Structural base is upstream `Platforms/Xiaomi/onyxPkg` (Xiaomi POCO F7, **same SM8735 SoC**) + `Silicon/Qualcomm/BonitoPkg/BonitoPkg.dsc.inc`, which is explicitly SM8735 (8 cores / 4 clusters, GIC distributor `0x17100000`, redistributors `0x17180000`, `-march=armv9.2-a`); `muyu`/`nuwa` configs are cross-checks only. No upstream `lumingPkg` exists. |

The mainline UniXNU tree is qualified against Darwin 23 / iPadOS 17. This port
keeps the UniXNU build driver, repository schema and ADT encoding, but targets the
iPhone X iOS 16.7.16 userland because that is the documented community route for
xnu-8796-era kernels. Consequences are tracked in §7.

## 2. Identity model (what the ADT claims, and what it does not)

`scripts/make-luming-adt.py` writes the Apple-flat-device-tree the platform expert
reads. Identity is *software identity for the userland*, never a claim about the
SoC's fuses (see UniXNU `docs/guide/mobilegestalt.md`).

| ADT location | Written value | Source |
| --- | --- | --- |
| root `model` | `iPhone10,3` | `docs/guide/mobilegestalt.md` (iOS row) + decoded Apple D22AP tree |
| root `target-type` | `D22` | same |
| root `target-sub-type` | `D22AP` | decoded Apple D22AP tree (`scripts/luming_identity.py` provenance) |
| root `compatible` | `xiaomi,luming`, `qcom,sm8735` — **the board's own strings** | `docs/guide/mobilegestalt.md`: "Leave the root `compatible` strings as the board's own. An Apple board name can match original platform kexts and select a path the port does not implement." |
| `/product` | Apple's real 60-property D22AP `/product` set (product-name `iPhone X`, product-description `iPhone X`, artwork-device-idiom `phone`, artwork-device-subtype 2436, artwork-scale-factor 3, artwork-display-gamut `P3`, compatible-device-fallback `iPhone9,2`, thin-bezel 1, …) | Apple `DeviceTree.d22ap.im4p` from the 16.7.16 IPSW, decoded and saved under `refs/ios16-ipsw/`; embedded byte-exact in `scripts/luming_identity.py` |
| `/buttons` | `button-names` = `volup voldown hold` (ringer switch **omitted** — the Civi 5 Pro has none), `home-button-type` = 2, `opposed-power-vol-buttons` = 1 | decoded Apple tree; deviation documented below |
| `/chosen` | `chip-id` 0x8015, `board-id` 6, `unique-chip-id` (device seed), `random-seed` 64 B, `root-matching` dict, NVRAM proxy bank, `osenvironment` main, `crypto-hash-method` sha2-384, `display-scale` 3, four activation/security-status properties, `dram-base`/`dram-size`, `memory-map` placeholders | `docs/porting/device-tree.md`, `docs/guide/mobilegestalt.md`, UniXNU-sheng ADT tool (proven encoder), guacamole iOS 16 ADT (security tuple + display scale) |
| `/defaults` | `sleep-disabled` 1, `no-effaceable-storage` 1 | `docs/porting/device-tree.md`, guacamole iOS 16 ADT |
| `/arm-io` | `ranges` bus window, `intc` `arm,gic-v3` + redistributor stride, `uart` node, `ufshc` class-match node | structural requirement from `docs/porting/device-tree.md`; the CPU MPIDRs/timebase, GIC/UART/UFS windows and DRAM base are **measured on the device 2026-10-22** (live DTB + `/proc/iomem`, §3) |

### 2.1 Deviations from the reference implementations

1. **`ipad_identity` flag semantics.** In UniXNU-sheng the `--ipad-identity` flag
   makes the tool write the *iPad* product identity. Here the product identity is
   **always** iPhone10,3/D22AP (that is the target userland), and the flag only
   gates the four activation/security-status `/chosen` properties — the same four
   properties sheng writes. `board.toml` sets `ipad_identity = true`, so
   `unixnu build payload` keeps passing `--ipad-identity` and the flag keeps its
   base-driver meaning ("write the Apple identity properties"). This delta is why
   the tool accepts `--iphone-identity` as an alias.
2. **`button-names`.** Apple's iPhone X list is `volup voldown hold ringerab`; the
   Civi 5 Pro has no ringer/silent switch, so the default omits `ringerab`
   (`--apple-buttons` emits Apple's full list for comparison).
3. **`product-id` omitted.** The donor tree's 20-byte per-chip `product-id` is
   deliberately **not** copied; a UniXNU ADT must not clone a specific donor
   handset identifier. Everything else in `/product` is byte-exact.
4. **Root `compatible` is not Apple's.** Guacamole's iOS 16 port put
   `D22AP\0iPhone10,3\0AppleARM` in the root compatible for its own platform
   expert. UniXNU's documented rule is the opposite; this repository follows the
   UniXNU rule and carries identity in `model`/`target-type`/`/product`.

## 3. Sourced facts (verified; safe to rely on)

Two evidence classes are used below: **sourced** (Apple/Android repositories and the
IPSW) and **measured** (the read-only capture of the owner's stock luming device on
2026-10-22: live device tree, `/proc/iomem`, dmesg, GPT and sysfs; the device was
restored to unmodified stock afterwards). The raw capture is not committed to this
repository; it is listed in §8.

| Fact | Value | Where |
| --- | --- | --- |
| IPSW URL | `https://updates.cdn-apple.com/2025FallFCS/fullrestores/122-77006/1158E502-D76D-4226-AA06-ED5E30CD1111/iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw` | ipsw.me v4 + HTTP HEAD |
| IPSW size / sha256 | 6,171,035,648 B / `ece1194f7e233e9109c5790a8d23a4564a10a254e15ec5b09978a343265568fb` | ref-scout, HTTP HEAD; **whole-image hash, not a member pin** |
| IPSW components | KernelCache, DeviceTree, StaticTrustCache, Cryptex1,SystemTrustCache | BuildManifest |
| DeviceTree member | `Firmware/all_flash/DeviceTree.d22ap.im4p`, 33,719 B, im4p sha256 `480e8b08…a6368`, ADT payload 186,084 B | decoded by ref-scout; saved JSON under `refs/ios16-ipsw/` |
| Kernelcache member | `kernelcache.release.iphone10b`, 18,204,358 B (IM4P/LZFSE) → 50,937,884 B Mach-O | BuildManifest + guacamole NOTES |
| CPU topology | 8 cores, ARMv9, 4 clusters: 1× Cortex-X4 ≤3.2 GHz, 3× Cortex-A720 ≤3.01 GHz, 2× Cortex-A720 ≤2.80 GHz, 2× Cortex-A720 ≤2.02 GHz; measured live DT: 8 `cpu@` nodes, MPIDR `0,0x100..0x700`, `compatible = qcom,kryo`, `enable-method = psci` | Qualcomm Snapdragon 8s Gen 4 launch coverage (fonearena, notebookcheck); MPIDRs/topology **measured on the device 2026-10-22** (live DTB `/cpus`) |
| Android stock boot image | header_version 4, pagesize 4096, kernel base 0x0, ramdisk_offset 0x01000000, tags_offset 0x100; boot/vendor_boot 96 MiB each; super 9,126,805,504 B. **Measured**: stock `boot.img` is Android header v4 (`ANDROID!`, header_size 1584, 100,663,296 B) | `refs/android_device_xiaomi_luming-recovery/BoardConfig.mk:47,50,53-55` + parsed stock `boot.img` (`backup/luming-20261022-211723/stock-rom/`) |
| Kernelcache pin | decompressed arm64 Mach-O 50,937,856 B sha256 `2ec297bac95bcba01046fd0148e97e7e5885c05a6c18827f39f534ba375c1221` (IPSW member `kernelcache.release.iphone10b` 18,204,358 B sha256 `2d6a13d040aaf87cec09bb6906d43e9bd8cea951e43d1aa1cd62707dfdc5aaec`) | kernel analyst IPSW range-read + local LZFSE decode; `backup/analysis/kernel/`; pin in `board.toml [kernel.prelink]` |

### 3.1 Measured hardware facts (luming, 2026-10-22; read-only)

All values below are from `backup/analysis/device-live/` (live DTB
`luming-live/live.dtb`, 794,887 B; parsed summary `LIVE-FACTS.json`/`LIVE-FACTS.md`;
`iomem.txt`, `dmesg.txt`, `sys/`, and the GPT dump under `luming-live/gpt/`). They
are entered in `board.toml` / `scripts/make-luming-adt.py` and cross-checked by
`scripts/check-luming.py`. They are hardware facts of the stock Android system, not a
boot of XNU.

| Fact | Measured value |
| --- | --- |
| Model / root compatible | `Qualcomm Technologies, Inc. Luming based on SM8735`; root compatible `qcom,tuna-mtp qcom,tuna qcom,tunap-mtp qcom,tunap qcom,mtp` |
| CPU | 8 cores, MPIDR `0,0x100,0x200,0x300,0x400,0x500,0x600,0x700`; `compatible qcom,kryo`; `enable-method psci`; timebase 19,200,000 Hz (`/soc/timer@17420000`) |
| GIC | distributor `0x17100000` + `0x10000`; redistributor `0x17180000` + `0x200000`; ITS `0x17140000` + `0x20000` (live DT + `/proc/iomem` GICD/GICR) |
| UART | `0x00A9C000` + `0x4000`, `interrupts <0 579 4>` (interrupt 579 = 0x243), `stdout-path serial0:115200n8` (live DT `qcom,qup_uart@a9c000` + iomem `a9c000-a9ffff`) |
| UFS | `ufshc@1d84000` regs `<0x1d84000 0x3000, 0x1d88000 0x18000, 0x1da5000 0x2000, 0x1da4000 0x10>`, interrupt 265; iomem `ufs_mem`/`ice`/`mcq` |
| DRAM | base `0x80000000` (lowest iomem System RAM is `0x81300000`); MemTotal 11,503,328 kB; live DT `/memory` regions total 12,098,654,208 B ⇒ **12 GiB SKU** (`0x300000000`) |
| `userdata` GPT | first_lba 3,124,736, last_lba 128,619,514, lba_bytes 4096 (125,494,779 LBAs = 514,026,614,784 B = 478.72 GiB); unit-specific (this 512 GB unit) |
| Splash reserved memory | `0xfc800000` + `0x2b00000` (`0xfc800000..0xff2fffff`), label `cont_splash_region`; dmesg `[drm] cont_splash enabled` |
| Panel / display | DRM `card0-DSI-1` modes `1236x2750x120cmd`, `x90cmd`, `x60cmd`; cmdline `msm_drm.dsi_display0=qcom,mdss_dsi_o9_42_02_0a_dsc_cmd:`; panel cell id `AGY553R049AH3` |
| Touch | `synaptics_tcm@0` under `/soc/qcom,qupv3_1_geni_se@ac0000/spi@a90000`, compatible `synaptics,tcm-spi`; plus `/soc/xiaomi_touch`; input device `Xiaomi_Touch_Input_0` |
| Boot image geometry | stock `boot.img`: Android header v4, header_size 1584, pagesize 4096, 100,663,296 B; offsets from the stock build (`base 0x0`, `kernel_offset 0x8000`, `ramdisk_offset 0x01000000`, `tags_offset 0x100`) |

## 4. Device inputs: measured 2026-10-22 and remaining TODO(device)

`python scripts/check-luming.py --list-todos` prints the live marker list with
file:line. The measurement campaign closed the platform-address, GPT, boot-image,
splash and kernelcache parts of the old inventory; the grouped status is below.

### 4.1 Platform addresses (CLOSED 2026-10-22)

Every value is a measured module constant in `scripts/make-luming-adt.py`; no value
needs `--todo-placeholder-addresses` for generation any more. Evidence: the live
device tree `backup/analysis/device-live/luming-live/live.dtb` (794,887 B),
`/proc/iomem` and dmesg, parsed in `backup/analysis/device-live/LIVE-FACTS.json`/`.md`.

| Value | Measured | Used for |
| --- | --- | --- |
| DRAM base | `0x80000000` (lowest iomem System RAM `0x81300000`; SM8735 DDR base) | `/chosen/dram-base` |
| GIC distributor | `0x17100000` + `0x10000` | `/arm-io/intc` |
| GIC redistributor | `0x17180000` + `0x200000` (8 cores × `0x40000` stride) | `/arm-io/intc` |
| UART | `0x00A9C000` + `0x4000` (interrupt 579 = 0x243; `serial0:115200n8`) | `/arm-io/uart` |
| timebase frequency | 19,200,000 Hz | `/cpus/*/timebase-frequency` |
| CPU MPIDRs | `0,0x100,0x200,0x300,0x400,0x500,0x600,0x700` (8 cores, `qcom,kryo`, `enable-method psci`) | `/cpus/*/reg` |
| UFS host | `0x1D84000` + `0x3000` (plus measured ICE/MCQ windows, IRQ 265) | NativeUfs board constants |
| DRAM size | 12 GiB SKU = `0x300000000` (MemTotal 11,503,328 kB; live memory regions 12,098,654,208 B) | `/chosen/dram-size`, `[payload] dram_size` |
| arm-io window | base `0x0`, size `0x20000000` - a presentation window (child offsets equal the physical addresses), not a measurement; contains every measured MMIO child | `/arm-io/ranges` |

`--check` verifies the dram-base, timebase, CPU compatible, arm-io window and the
GIC/UART windows in the emitted tree. `--todo-placeholder-addresses` is no longer
needed: it remains only as the structural-encoding escape hatch for a future revision
that must carry a value as `None`; such a tree prints `NOT BOOTABLE` and must never
boot. The CLI address flags still exist for one-off trees and mark the tree as an
unverified override unless `--soc-verified` is given.

> Confidence: these are hardware facts read from the stock Android system, **not a
> boot of XNU**. They are "measured on the device 2026-10-22", not "hardware-verified
> by this port"; the first kernel boot is what validates that ABL/XNU agree with them.

### 4.2 IPSW / partition inputs

| Value | State | Evidence |
| --- | --- | --- |
| `[userdata] first_lba` / `last_lba` | **measured** 3,124,736 / 128,619,514 | live GPT of `/dev/block/sda` (`backup/analysis/device-live/luming-live/gpt/`, `LIVE-FACTS.json`); 125,494,779 LBAs × 4096 B = 514,026,614,784 B (478.72 GiB) |
| `lba_bytes` = 4096 | **measured** | GPT logical block size (also `fastboot getvar logical-block-size: 0x1000`) |
| `[ipsw.sha256]` member pins | TODO(ipsw) | pin each extracted IPSW member's sha256 after a `unixnu ipsw fetch` run; the whole-IPSW hash is not a member pin (the kernelcache itself is pinned separately in `[kernel.prelink]`) |

The measured `userdata` LBAs are specific to this 512 GB unit; re-measure on a
different storage SKU.

### 4.3 Payload / ADT identity inputs

| Value | State | How to obtain |
| --- | --- | --- |
| `owned_memory_size` (1 GiB candidate) | TODO(device) | DarwinBoot contiguous window, 256 MiB-8 GiB in 2 MiB steps; needs the uefi-luming memory map after kernel entry (nuwa's D-002 first move is 512 MiB-1 GiB) |
| `root_match_volume_uuid` | TODO(device) | APFS UUID of the SystemOS volume of the 20H392 image, extracted offline on macOS (`diskutil info`/`apfsutil`) or from the assembled userdata image |
| trust cache hashes / bundles | TODO(device) | original StaticTrustCache + Cryptex1,SystemTrustCache of the IPSW; owner bundle build is a later step (`[payload] trust_cache_sha256` stays commented) |
| Data seed (`--data-ramdisk`) | TODO(device) | built/extracted separately; never stored in git. `[payload.adt] unique_chip_id = "required"` so the payload step cannot run without the platform seed |
| platform seed (`--unique-chip-id`) | TODO(device) | generated once per board, kept as a CI secret (`UNIXNU_UNIQUE_CHIP_ID`), never committed |
| `dram_size` SKU | **measured** 12 GiB (`0x300000000`) | §4.1; a 16 GiB unit would be `0x400000000` |
| splash region (boot image DTB node) | **measured** `0xfc800000` + `0x2b00000` | live DT `/reserved-memory/splash_region` + dmesg; entered as `[[bootimg.dtb_node]]`; ABL acceptance is still confirm-on-boot |
| boot image header parameters | **measured** header v4, pagesize 4096, base 0x0, kernel_offset 0x8000, ramdisk_offset 0x01000000, tags_offset 0x100 | parsed stock `boot.img` (`backup/luming-20261022-211723/stock-rom/boot.img`, 100,663,296 B) + recovery `BoardConfig.mk:47,50,53-55`; entered as `[bootimg.image]`; a v4 build needs the driver's v4 verify patch (`patches/unixnu-bootimg-header-v4.patch`) |

### 4.4 Kernel / prelink inputs

| Value | State | What unblocks it |
| --- | --- | --- |
| `kernelcache_sha256` | **PINNED** `2ec297ba...5c1221` (decompressed arm64 Mach-O, 50,937,856 B; IPSW member 18,204,358 B `2d6a13d0...`) | `board.toml [kernel.prelink]`; evidence `backup/analysis/kernel/`; the driver hashes the decompressed Mach-O (`scripts/unixnu/ipsw.py:kernelcache()`) |
| `slide`, `early_security_init`, `roots` | absent (TODO(device)/TODO(kernel)) | the iOS 16.7.16 kernelcache prelink against a native xnu-8796 kernel **failed** on the closest documented port (missing `_vfs_getvfs_with_vfsops`, missing prelink ABI exports; `refs/UniXNU-guacamole/NOTES.md:197-210`) - either qualify a Darwin 22 prelink or adopt the monolithic-kernel route; copying sheng's iPadOS 17.7.11 numbers is forbidden (`refs/UniXNU-nuwa/docs/DECISIONS.md:147-150`) |
| `[uefi_fd]` base/size | TODO(device) | uefi-luming's memory map/BootShim compile; onyx (same SoC) uses `0xA7100000/0x300000` as a lead, not a luming measurement |
| archive compatibility | TODO(kernel) | every UniXNU archive builder is Darwin 23-qualified; audit each builder against Darwin 22 |

## 5. Stage plan and gates

Stages follow UniXNU `docs/porting/bring-up.md`; **S0 is the only stage this
repository claims** (static checks plus the read-only hardware measurement of
2026-10-22). Gate criteria before any `fastboot boot`:

| Stage | Result to observe | Gate |
| --- | --- | --- |
| S0 | static checks pass | `scripts/check-luming.py` PASS; `unixnu defconfig` + `status` run |
| S1 UEFI | uefi-luming builds and shows output on the phone | uefi-luming repo builds `SILICIUM_UEFI.fd`; boot image header verified against stock image |
| S2 UniXNU | `UNIXNU:` log lines, menu, payload volume | firmware FV contains DarwinBoot; `unixnu.toml` read |
| S3 payload | `xnu` entry listed, ADT sidecar next to kernel, payload report hashes match | measured ADT addresses + valid KDF (both ready); payload still needs the platform seed/trust cache/Data seed and the Darwin 22 kernel |
| S4 DarwinBoot | `DARWINBOOT:` lines, kernel window and entry addresses | kernel passes DarwinBoot validation (Mach-O, ADT, trust cache) |
| S5 kernel entry | XNU version prints; platform expert matches the ADT | Darwin 22 kernel build + archive set works; `/chosen`, `/cpus`, `/arm-io` accepted |
| S6 storage/root | UFS publishes `userdata`, root UUID matches, `launchd` starts | UFS windows + `userdata` LBAs measured 2026-10-22; root UUID TODO(device); NativeUfs Darwin 22 port TODO(kernel) |
| S7 userland | Data mounts, display output, Setup flows | display/touch hardware identified (1236x2750 panel, Synaptics TCM); Darwin drivers do not exist |

## 6. Windows-only / no MacBook workflow

The user does **not** own a Mac. Nothing below requires one.

### 6.1 Windows host (pre-build and everything non-Xcode)

Requirements: Python 3.11+ (bundled runtime or python.org; `tomllib` is stdlib),
PowerShell 7 (the harness also runs on Windows PowerShell 5.1). For
firmware/package/bootimg later: MSYS2 (or WSL2) providing `make`, `clang`/LLVM,
`mtools`, `dtc`/`fdtput`, plus an EDK2-capable environment.

> **Windows encoding constraint (found during validation).**
> `scripts/unixnu/config.py` reads `board.toml`/defconfig with
> `Path.read_text()` **without an encoding**, so on a Windows host whose ANSI code
> page is not UTF-8 (e.g. GBK) any non-ASCII byte in those files raises
> `UnicodeDecodeError`. The files the driver reads (`board.toml`,
> `configs/luming_defconfig`, `feeds.toml`) are therefore kept **ASCII-only**;
> prose with non-ASCII characters belongs in README/PORTING/docs, which the driver
> does not parse. If you add a comment to board.toml, keep it ASCII.
>
> **Expected fail-fast on the first runner run.** The pre-build set has the measured
> platform, GPT, boot-image and kernelcache inputs, but no prelink values and no
> device inputs (platform seed, trust cache, Data seed), and `[payload] panic_screen`
> is off because its patch script is not ported. The macOS workflow will pass the
> preflight and then stop on `unixnu build kernel` (Darwin 22 archives / absent
> prelink slide) or `build payload` (device inputs) with the corresponding "missing"
> error. That is intended: the pipeline encodes the remaining gates instead of
> pretending they are satisfied.

```powershell
# 1. static validation of this device repository
python UniXNU-luming\scripts\check-luming.py --list-todos
pwsh -File UniXNU-luming\tools\validate.ps1

# 2. ADT with the measured platform constants (no placeholder flag needed)
python UniXNU-luming\scripts\make-luming-adt.py --output $env:TEMP\luming.adt `
  --dram-size 0x300000000 --owned-memory-size 0x40000000 --chip-id 8015 `
  --unique-chip-id 0011223344556677 --cpu-regs 0,0x100,0x200,0x300,0x400,0x500,0x600,0x700 `
  --packed-data-ramdisk --trust-cache
python UniXNU-luming\scripts\make-luming-adt.py --check --output $env:TEMP\luming.adt `
  --dram-size 0x300000000 --cpu-regs 0,0x100,0x200,0x300,0x400,0x500,0x600,0x700 `
  --packed-data-ramdisk --trust-cache
# structural encoding test only (synthetic addresses; prints NOT BOOTABLE):
# add --todo-placeholder-addresses to the generation command; never boot that tree

# 3. select the board in the UniXNU driver (works on Windows)
cd UniXNU-main\UniXNU-main
python unixnu defconfig luming_defconfig -D ..\..\UniXNU-luming --out ..\..\out-luming
python unixnu status -D ..\..\UniXNU-luming --out ..\..\out-luming

# 4. later, with the MSYS2 toolchain and a uefi-luming checkout:
#    python unixnu build package  (EDK2 env)
#    python unixnu build firmware --uefi ..\..\uefi-luming
#    python unixnu bootimg --uefi ..\..\uefi-luming --dtb <luming.dtb>

# 5. firmware USB gadget: none for control; boot mode selection is done by the user
#    on the phone (power+volume), fastboot is a manual step in a later phase.
```

### 6.2 GitHub Actions macOS runner (Xcode-only steps)

`.github/workflows/macos-build.yml` in this repository: Actions → *macOS build
(luming)* → Run workflow with `defconfig=luming_defconfig`, `steps=kernel userdata
payload` (or fewer), and repository inputs pointing at the UniXNU driver and this
device repository. It needs the repository secret `UNIXNU_UNIQUE_CHIP_ID`. The
workflow uploads `out/luming/kernel/**`, `out/luming/payload/**`, logs, and
optionally the userdata image as artifacts. It never runs `fastboot` and no
device is attached.

The same command sequence works on a borrowed Mac with Xcode; the Actions runner is
just the no-Mac path.

### 6.3 Flashing (manual, later, not executed)

```sh
fastboot boot out/luming/boot/boot.img
fastboot flash userdata out/luming/userdata/userdata-root.img
fastboot reboot bootloader
fastboot boot out/luming/boot/boot.img
```

Only after the §5 gates are cleared and with the user present. This repository and
its CI never flash.

## 7. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Darwin 22 platform archives not passing (docs/devices/nuwa.md:14-15) | S5 blocked | enable documented flags only; audit archives one by one; track in docs/STATUS.md |
| iOS 16.7.16 kernelcache cannot prelink this native kernel (guacamole NOTES:197-210) | S3/S5 blocked | leave prelink slide/roots/early_security_init absent; evaluate the monolithic-kernel route |
| ADT platform addresses measured from the stock DT but not yet confirmed by an XNU boot | platform expert could reject `/arm-io` if ABL/UEFI/XNU expectations differ from the Android DT | values are measured and cross-checked against `/proc/iomem`; `--check` verifies them; the first kernel boot log is the acceptance test |
| stock ABL requires header v4 / splash region / specific offsets | boot image rejected or panel stays off | v4 header + splash region measured 2026-10-22 and entered in board.toml; the base driver's `unixnu bootimg` verify is v0-oriented, so apply the v4 verify patch (`patches/unixnu-bootimg-header-v4.patch`) before bootimg; real `fastboot boot` acceptance is still unverified |
| uefi-luming not built | S1 blocked | board `[firmware]` values are provisional; align with uefi-builder; `[uefi_fd]` base/size TODO(device) |
| per-unit identity copied accidentally | donor handset identity leak | `product-id` omitted; `unique-chip-id` required and never stored; CI secret |
| no macOS locally | cannot build kernel/userdata/payload on the Windows host | GitHub Actions macOS runner (documented, workflow provided) |

## 8. Evidence index

| Topic | Path |
| --- | --- |
| UniXNU device-repository schema | `UniXNU-main/UniXNU-main/docs/porting/device-repository.md`, `docs/reference/configuration.md` |
| ADT encoding / required nodes | `UniXNU-main/UniXNU-main/docs/porting/device-tree.md` |
| Identity rules | `UniXNU-main/UniXNU-main/docs/guide/mobilegestalt.md` |
| Proven ADT encoder | `refs/UniXNU-sheng/scripts/make-sheng-adt.py` |
| iOS 16 ADT/route (hardware) | `refs/UniXNU-guacamole/scripts/mkadt.py`, `NOTES.md`, `board.toml` |
| iOS 16 route decisions (nuwa) | `refs/UniXNU-nuwa/docs/DECISIONS.md`, `docs/devices/nuwa.md` in the base repo |
| iPhone X DeviceTree decode | `refs/ios16-ipsw/d22ap-product.json`, `scripts/luming_identity.py` |
| Verified BuildManifest facts | `refs/ios16-ipsw/BuildManifest-summary.json`, `Restore.plist` |
| luming Android facts | `refs/android_device_xiaomi_luming-recovery/` |
| Live device hardware capture 2026-10-22 | `backup/analysis/device-live/luming-live/` (`live.dtb`, `iomem.txt`, `dmesg.txt`, `gpt/`, `sys/`), parsed `backup/analysis/device-live/LIVE-FACTS.json` / `LIVE-FACTS.md` |
| Stock boot image header evidence | `backup/luming-20261022-211723/stock-rom/boot.img` (100,663,296 B, header v4) |
| Target kernelcache pin evidence | `backup/analysis/kernel/` (`kernelcache.decoded`, 50,937,856 B) |
| Local validation record | `docs/VALIDATION.md`, `docs/STATUS.md`, `docs/validation/` |
