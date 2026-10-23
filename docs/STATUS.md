# UniXNU-luming — stage status (pre-build; hardware inputs measured 2026-10-22)

Single source of truth for what this repository has proven and what is blocked.
Evidence levels are used strictly (UniXNU-nuwa AGENTS.md:6 discipline):

> **静态检查通过** < **QEMU 执行通过** < **实机通过**

No UniXNU image has run on luming hardware and no image has been built, flashed or
booted. The highest level for the repository itself remains 静态检查通过 (static/local
checks), plus a **read-only hardware measurement**: on 2026-10-22 the owner's stock
device was captured read-only (live device tree, `/proc/iomem`, dmesg, GPT, sysfs; no
partition written, device restored to unmodified stock) and those values are now in
`board.toml` and `scripts/make-luming-adt.py`. A measurement is not a boot: XNU has
never executed on this board.

## Stage table

| Stage | Goal | State | Evidence / blocker |
| --- | --- | --- | --- |
| **S0** Repository & static contracts | board.toml, defconfig, ADT tool, package skeleton, docs, CI workflow exist and validate locally; measured platform/hardware inputs integrated | **done (静态检查通过)** | `scripts/check-luming.py` PASS; ADT generates with measured addresses and `--check` verifies them; `unixnu defconfig` + `unixnu status` run from the UniXNU base repo; see docs/VALIDATION.md |
| **S1** UEFI firmware | uefi-luming reaches the firmware UI on the phone | **blocked (pre-hardware)** | uefi-luming is a separate repository being scaffolded by the uefi-builder teammate; `[firmware]` paths here are UNVERIFIED against it. No build executed (needs EDK2 toolchain, `--uefi`). `[uefi_fd]` base/size still TODO(device). |
| **S2** UniXNU menu | firmware loads UniXNU and reads `unixnu.toml` | **not started** | needs S1 |
| **S3** Payload + ADT | UniXNU reads the payload volume, ADT validates, DarwinBoot accepts the kernel | **partly ready; still blocked** | The ADT is no longer address-blocked: all platform addresses were measured on the device 2026-10-22 (live DTB/iomem/GPT/dmesg) and the tool generates without `--todo-placeholder-addresses`; `--check` verifies the measured windows. Still missing: `[payload.adt] owned_memory_size`, `root_match_volume_uuid`, the platform seed and DSC/trust-cache/Data inputs, plus the Darwin 22 kernel archives (S5). |
| **S4** DarwinBoot | kernel/ADT/trust-cache handoff at EL1 | **not started** | needs S3 |
| **S5** Kernel entry | XNU starts, platform expert matches `/chosen`, `/cpus`, `/arm-io` | **not started; high risk** | Darwin 22 platform/archive support is "not passing" in the community iOS 16 route (UniXNU docs/devices/nuwa.md:14-15); the iOS 16.7.16 kernelcache prelink failed on the closest documented port (refs/UniXNU-guacamole/NOTES.md:197-210). The kernelcache itself is now pinned (`[kernel.prelink] kernelcache_sha256`); slide/roots/early_security_init stay absent. |
| **S6** Storage + root | UFS driver publishes `userdata`, XNU mounts the root | **blocked (driver + root UUID)** | UFS controller windows, `userdata` GPT LBAs and the DRAM base/size are measured (2026-10-22); the NativeUfs Darwin 22 driver is not ported and the SystemOS root volume UUID is still TODO(device). |
| **S7** Userland | iOS 16.7.16 `launchd`, Data mount, display | **not started** | needs S6 plus display/touch drivers that do not exist (panel 1236x2750 and Synaptics TCM touch are identified/measured; no Darwin driver). |

## Blocked items (in dependency order)

1. **Remaining device/prelink measurements** (everything else measured 2026-10-22):
   `[payload.adt] owned_memory_size` (needs the uefi-luming memory map after kernel
   entry), `root_match_volume_uuid` (offline APFS extraction), `[kernel.prelink]`
   slide / early_security_init / roots (Darwin 22 prelink not demonstrated), and the
   `[uefi_fd]` base/size for uefi-luming. Closed on 2026-10-22: ADT platform
   addresses, `[userdata]` GPT LBAs, boot image header v4 geometry, splash reserved
   memory, DRAM size (12 GiB SKU) and the kernelcache pin (see the gate table below).
2. **uefi-luming** — DSC/FDF/config and `[uefi_fd]` values; board `[firmware]` is
   provisional until the firmware builds.
3. **Darwin 22 / xnu-8796 kernel build** — Xcode-only, documented for the macOS
   Actions runner (`.github/workflows/macos-build.yml`); not executed in this phase.
4. **Prelink decision** — either qualify a Darwin 22 prelink against the 20H392
   kernelcache (now pinned) or adopt the monolithic-kernel route used by the
   guacamole port; `[kernel.prelink]` slide/roots/early_security_init stay absent
   until then.
5. **ACPI / UEFI handoff details** — platform matches for the luming DTB and the
   SM8735 memory map (owner: uefi-builder).
6. **Trust cache / Data seed / config seed** — device inputs that must never be
   committed (`--unique-chip-id`, `--trust-cache`, `--data-ramdisk`).

## What was actually run (static, host-side)

See `docs/VALIDATION.md` for exact commands and outputs. Summary:

- `tomllib.load` on every TOML file (4 files) — pass.
- `scripts/make-luming-adt.py` generation **with the measured constants and no
  placeholder flag** — pass. The tree carries the measured `/chosen/dram-base`,
  `/cpus` MPIDRs + timebase, `/arm-io` ranges, GIC distributor/redistributor windows
  and UART window; the tool reports
  `platform addresses: MEASURED on the luming board 2026-10-22 (live DTB/iomem)`.
- ADT `--check` against that tree — pass, including the measured-address
  verification (`addresses measured on the luming board 2026-10-22`).
- ADT negative regressions — pass: a tree generated with a different DRAM base is
  rejected by `--check`; a truncated tree is rejected; a temporary copy of the tool
  with `DRAM_BASE = None` refuses to fabricate a value (`TODO(device)` message) and
  `--todo-placeholder-addresses` still produces a structurally valid, clearly
  non-bootable placeholder tree.
- `scripts/check-luming.py` — pass (exit 0, PREFLIGHT PASSED, 12 warnings). Its TODO
  inventory is machine-generated and drifts as docs change; the current list is
  `docs/validation/todo-list.log` (regen: `--list-todos`). Remaining code/config
  markers are the prelink values, payload device inputs, UEFI firmware inputs and
  kernel-archive port, not platform addresses.
- `pwsh tools/validate.ps1` (Windows harness, PowerShell 5.1) — pass: TOML 4/4, ADT
  measured generation + check + two negative regressions, preflight, `unixnu
  defconfig`, `unixnu status`.
- `python unixnu defconfig luming_defconfig -D D:\civitoip20\UniXNU-luming --out D:\civitoip20\out-luming2` — pass (config copied into `out-luming2/luming/.config`).
- `python unixnu status -D ... --out ...` — pass (device + config printed; module
  resolution shows the device package as `package:device luming`).
- Windows finding fixed: `scripts/unixnu/config.py` reads without an explicit
  encoding, so a non-ASCII quote in `board.toml` raised `UnicodeDecodeError` on this
  GBK host. Driver-read files (`board.toml`, defconfig, `feeds.toml`) are ASCII-only
  now; keep them that way.
- Nothing else. No build, no `fetch`, no `ipsw fetch`, no firmware build, no flash.

### Expected fail-fast on the first macOS runner run

`board.toml` deliberately keeps the pre-boot gates: `[kernel.prelink]`
slide/early_security_init/roots absent, `[payload] panic_screen = false` (patch
script not ported), `[payload.adt] unique_chip_id = "required"`, and the payload
needs the trust cache + Data seed. The workflow
`.github/workflows/macos-build.yml` will pass the preflight and then stop at
`unixnu build kernel` (missing Darwin-22 archives) / `build payload` (device seed)
until those inputs exist. This is the designed gate, not a defect: the pipeline must
not pretend the values exist.

Concretely, with the current measured values:

- `unixnu build userdata` no longer hits the unmeasured-LBA gate: the partition
  capacity is now computed from the measured GPT entry (125,494,779 LBAs x 4096 B =
  514,026,614,784 B) and is larger than the ~7.5 GiB of OS/SystemOS images. The
  step can proceed to the IPSW extraction path on the macOS runner (not executed
  here).
- `unixnu build kernel` first stops at the missing Darwin-22 platform archives
  (see `[kernel] archives` TODO(kernel)); the absent `[kernel.prelink] slide` is the
  later gate. The boot-progress prerequisite check does not fire because
  `CONFIG_XNU_BOOT_PROGRESS=n` (see the gate table below).
- `unixnu build payload` refuses because `[payload.adt] unique_chip_id = "required"`
  and the device seed is not in any repository (a trust cache/Data seed is also
  needed for the real payload).
- `unixnu bootimg` will need the base driver's v4 verify path before the measured
  header-v4 geometry can be emitted end-to-end; see the local gate below.

### Known preflight gates (cross-team, 2026-10-22)

windows-ci's runner-input preflight (`prebuild/windows/preflight_inputs.py`,
schema 2 results in `prebuild/windows/results/luming-preflight.json`, rows carry
`expected: true` + a gate id). After the device measurement session two of its four
gates are closed on the device-repository side; windows-ci's classifier has **not**
been re-run by this task, so its next run is expected to report **2 expected gates**
(plus warnings) instead of 4. If it still reports the closed ones, its expectation
list (owner: windows-ci) is stale:

| windows-ci gate id | Severity | Finding | Status |
| --- | --- | --- | --- |
| `device-gpt-measurement` | gate | `[userdata] first_lba/last_lba` are 0/0 | **CLOSED 2026-10-22**: measured GPT entry `userdata` = 3,124,736..128,619,514, `lba_bytes` 4096 (478.72 GiB) |
| `kernelcache-extraction` | gate | `[kernel.prelink] kernelcache_sha256` not pinned | **CLOSED 2026-10-22**: pinned to the decompressed arm64 Mach-O sha256 `2ec297ba...5c1221` (50,937,856 B; IPSW member 18,204,358 B) |
| `kernelcache-prelink-analysis` | gate | `[kernel.prelink] slide` missing | OPEN: TODO(device)/TODO(kernel); Darwin 22 prelink not demonstrated; no value exists to write |
| `darwin22-kernel-archives` | gate | `[kernel] archives` has no builder for the enabled platform/display/UFS archives (5 flags: ARM_DT_PLATFORM, ARMCPU, NATIVE_UFS, NATIVE_RAM_BLOCK, NATIVE_LEGACY_DISPLAY) | OPEN: TODO(kernel): `build-arm-driver.py` / `build-native-legacy-display.py` are UniXNU-sheng-only; port before linking |

One earlier expected gate shown by windows-ci, `kernel-config-prerequisites`
(`CONFIG_XNU_BOOT_PROGRESS=y` without the audited owner-root/Virtio chain, which
makes `scripts/build-xnu-4k-baseline.sh:64-69` exit before compiling), is
**closed by design**: `CONFIG_XNU_BOOT_PROGRESS=n` in `luming_defconfig` with a
TODO(kernel) note, and `check-luming.py` mirrors the prerequisite check so the flag
cannot be re-enabled without its audited chain and `build-boot-progress.py`.

A new local gate appears with the measured boot image (no windows-ci id yet):

| Local gate | Finding | Status |
| --- | --- | --- |
| `bootimg-v4-verify` | `[bootimg.image] header_version = 4` is measured from the stock boot.img, but the base `scripts/unixnu/bootimg.py` unpack verification was v0-v2-oriented | **CLOSED (host-side, 2026-10-22)**: `patches/unixnu-bootimg-header-v4.patch` applied to the local base driver; synthetic v4 image packs/verifies and a tampered v4 signature is rejected (`backup/analysis/test-bootimg-v4.py`). Real boot image/ABL acceptance is still unverified |

Everything else preflight checks (archives resolution for the four listed builders,
ADT tool, prelink tool/kernel paths, firmware preflight, defconfig, userdata
windows, measured boot-image block) resolves. The remaining findings are tracked
here so the final report counts them as expected pre-build gates, not repository
defects; they disappear only after the remaining measurements (PORTING.md §4) and
the Darwin 22 archive port.

## Next action when hardware work is authorised

1. Finish the remaining measurements (PORTING.md §4): DarwinBoot owned window from
   the uefi-luming memory map, SystemOS APFS root UUID offline on macOS, the
   uefi-luming `[uefi_fd]` base/size, and the Darwin 22 prelink decision.
2. Run the macOS CI workflow for `kernel userdata payload` (documented, not executed).
3. Build uefi-luming and the boot image on Windows; the stock boot header is now
   measured (v4, pagesize 4096), so verify the built image header against it (and
   apply the v4 verify patch noted above).
4. Only then consider a `fastboot boot` of the built image — a separate, user-approved
   step that this repository does not perform.
