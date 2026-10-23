# Local validation record — UniXNU-luming (pre-build; measured inputs integrated)

Everything below was executed on the Windows host of this workspace with the
bundled Python 3.12.14:

```
C:\Users\HONOR\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
```

Base repository used for the driver-level checks:

```
D:\civitoip20\UniXNU-main\UniXNU-main
```

Highest evidence level reached: **静态检查通过** (static/local checks) plus the **read-only
hardware measurement of 2026-10-22**. Nothing was built, fetched, flashed or booted on
the phone; no XNU image has ever run on this board. The device capture was read-only
(no partition written, stock `init_boot` restored), and this validation only reads the
captured evidence under `backup/analysis/`. The macOS/Xcode steps are documented in
`PORTING.md` §6.2 and `.github/workflows/macos-build.yml` but were **not executed**.

Raw logs for every command are in `docs/validation/` (`adt-measured.log`,
`check-luming.log`, `todo-list.log`, `unixnu-defconfig-status.log`, `validate-ps1.log`,
`ascii-check.log`).

## 1. Measured hardware inputs integrated (2026-10-22, read-only)

Evidence captured from the owner's temporarily-rooted stock device and parsed outside
this repository (`backup/analysis/device-live/`):

| Evidence | Content used |
| --- | --- |
| `luming-live/live.dtb` (794,887 B) + `LIVE-FACTS.json`/`.md` | CPU count/MPIDRs/compatible, timebase 19.2 MHz, GIC distributor/redistributor/ITS windows, UART MMIO/IRQ/stdout, UFS regs/IRQ, DRAM base/memory regions, splash reserved memory, display/touch nodes |
| `luming-live/iomem.txt` | cross-check of GICD/GICR, UART `a9c000-a9ffff`, UFS `ufs_mem`/`ice`, System RAM base `0x81300000` |
| `luming-live/dmesg.txt` | splash region (`0xfc800000..0xff2fffff`, `cont_splash_region`), DRM `cont_splash`, Synaptics TCM/`xiaomi_touch`, mdss/DSI probe |
| `luming-live/gpt/` + `LIVE-FACTS.json` | `userdata` first_lba 3,124,736 / last_lba 128,619,514, lba_bytes 4096 (478.72 GiB) on the 512 GB unit |
| `luming-live/meminfo.txt`, `disks.txt`, `sys/` (drm/input/spi/i2c) | MemTotal 11,503,328 kB ⇒ 12 GiB SKU; disk/panel/touch/button device identification |
| `backup/luming-20261022-211723/stock-rom/boot.img` | header v4, header_size 1584, pagesize 4096, 100,663,296 B; offsets from stock `BoardConfig.mk:47,50,53-55` |
| `backup/analysis/kernel/kernelcache.decoded` | 50,937,856 B arm64 Mach-O, sha256 `2ec297ba…5c1221` ⇒ `[kernel.prelink] kernelcache_sha256` |

Integrated into the repository (all within this task's write scope):

- `board.toml`: `[userdata]` GPT LBAs; `[kernel.prelink] kernelcache_sha256`; `[payload]
  dram_size` (12 GiB SKU evidence); `[payload.adt] cpu_regs`/`psci_method` measured
  comments; `[bootimg.image]` header-v4 geometry; measured `[[bootimg.dtb_node]]` splash
  region. Remaining `TODO(device)`/`TODO(kernel)`/`TODO(toolchain)`: prelink
  slide/roots/early_security_init, owned window, root UUID, trust cache/Data seed,
  platform seed, UEFI FD base/size, `[ipsw.sha256]` member pins.
- `scripts/make-luming-adt.py`: measured platform constants (DRAM base, GIC windows,
  UART, timebase, UFS constants, `qcom,kryo` CPU compatible, extended board compatible);
  generation no longer needs `--todo-placeholder-addresses`; `--check` verifies the
  measured addresses.
- `scripts/check-luming.py`: measured expectations (`userdata`, `dram_size`,
  `kernelcache_sha256`, boot-image/splash block), ADT smoke test now uses measured
  generation + address-verification/truncation/unmeasured-value regressions.

## 2. ADT generator with the measured constants

```powershell
python UniXNU-luming\scripts\make-luming-adt.py `
  --output D:\civitoip20\prebuild\out\luming-measured.adt `
  --dram-size 0x300000000 --packed-data-ramdisk --trust-cache
# -> exit 0; 15,440 bytes, 8 CPUs, dram 0x300000000
#    stderr: platform addresses: MEASURED on the luming board 2026-10-22 (live DTB/iomem)
#    (the "no --unique-chip-id" warning is expected: the payload step supplies the seed)

python UniXNU-luming\scripts\make-luming-adt.py --check `
  --output D:\civitoip20\prebuild\out\luming-measured.adt `
  --dram-size 0x300000000 --packed-data-ramdisk --trust-cache
# -> luming ADT check: PASS (15440 bytes, addresses measured on the luming board 2026-10-22)
# -> exit 0
```

What `--check` now verifies besides the structural contract: `/chosen/dram-base`,
`/chosen/dram-size` (when `--dram-size` is given), `/cpus/cpu0/timebase-frequency` and
`compatible`, `/arm-io/ranges`, the GIC distributor/redistributor windows in
`/arm-io/intc/reg`, and the UART window in `/arm-io/uart/reg`, all against the measured
constants (or the caller's explicit CLI overrides).

Negative regressions (also part of `scripts/check-luming.py`):

- a tree generated with `--dram-base 0x90000000` is **rejected** by `--check` without
  the matching override (`/chosen/dram-base is 0x90000000, expected measured 0x80000000`);
- a truncated tree is rejected;
- a temporary copy of the tool with `DRAM_BASE = None` **refuses** to generate
  (`TODO(device)` message, exit 2) instead of fabricating a value;
- `--todo-placeholder-addresses` on that copy still produces a structurally valid tree
  and prints `NOT BOOTABLE`; on the real measured tool the flag reports that it has no
  effect.

## 3. Repository preflight (`scripts/check-luming.py`)

```powershell
python UniXNU-luming\scripts\check-luming.py
```

Result: **exit 0, PREFLIGHT PASSED: 12 warning(s)** (it was 15 before the measured
inputs were integrated). Key `ok` lines:

- `python 3.12.14`, required files, repository layout, `board.toml [board]`
- `[ipsw]` identity matches the verified d22ap iOS 16.7.16 build
- `[userdata] measured GPT entry 3124736..128619514 (478.72 GiB)`
- `[kernel.prelink] kernelcache_sha256 pinned (decompressed arm64 Mach-O)`
- `[payload] dram_size=0x300000000 measured (12 GiB SKU)`
- `[bootimg] measured header-v4 geometry and splash-reserved-memory node`
- D22AP identity data (60 `/product`, 3 `/buttons` properties)
- scripts parse, `configs/luming_defconfig` (20 flags, 14 enabled), TOML files parse
- **ADT tool: measured generation, `--check` + address verification, truncation refusal,
  unmeasured-value refusal, placeholder encoding test**

Remaining 12 warnings are the honest pre-build inventory: `[ipsw.sha256]` member pins,
`[kernel.prelink]` slide/roots/early_security_init, `[payload] panic_screen` (patch
script not ported), `[payload.adt]` owned window + root UUID, the pending
trust-cache bundle/Data seed device inputs, three defconfig flags not in the base
docs table (base-repository documentation gap), no extra feeds/drivers yet, and the
TODO-inventory pointer.

The machine-generated TODO inventory (regen `python scripts/check-luming.py
--list-todos > docs/validation/todo-list.log`) currently reports **80 markers, 51 of
them in code/config and 38 of those `TODO(device)`**; the rest are documentation
references. The actionable gate list is the code/config set in
`docs/validation/todo-list.log`, not the documentation counts.

Closed by this integration: the `device-gpt-measurement` and `kernelcache-extraction`
pre-build gates. Still open: `kernelcache-prelink-analysis` (slide/roots),
`darwin22-kernel-archives`, the UEFI firmware inputs and the payload device inputs; a
new local `bootimg-v4-verify` toolchain gate is recorded in `docs/STATUS.md`.

## 4. Windows validation harness (`tools/validate.ps1`)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File UniXNU-luming\tools\validate.ps1 `
  -Python <bundled-python> `
  -BaseRepo D:\civitoip20\UniXNU-main\UniXNU-main `
  -OutRoot D:\civitoip20\out-luming2
```

Result: **VALIDATION PASSED (static checks only; nothing built, flashed or booted)**.

| Step | Result |
| --- | --- |
| TOML parse (tomllib) | 4 TOML file(s) parsed, 0 failure(s) |
| ADT generation with the measured constants (no placeholder flag) | tree generated, prints `MEASURED` |
| ADT `--check` (structure + measured addresses) | PASS |
| ADT `--check` rejects a non-measured address | refused |
| ADT `--check` refuses a truncated tree | refused |
| check-luming.py preflight | PREFLIGHT PASSED: 12 warning(s) |
| `unixnu defconfig luming_defconfig` | `== configuration luming_defconfig -> D:\civitoip20\out-luming2\luming\.config` |
| `unixnu status` | device/output/config lines + full module resolution (`package device device`) |

Note: `pwsh` is not installed on this host, so the harness was invoked through
`powershell -File`; the script itself only uses PowerShell-5.1-compatible syntax.

## 5. UniXNU driver selection (base repo, unmodified)

```powershell
cd D:\civitoip20\UniXNU-main\UniXNU-main
python unixnu defconfig luming_defconfig -D D:\civitoip20\UniXNU-luming --out D:\civitoip20\out-luming2
# exit 0: == configuration luming_defconfig -> D:\civitoip20\out-luming2\luming\.config
python unixnu status -D D:\civitoip20\UniXNU-luming --out D:\civitoip20\out-luming2
# exit 0:
#   device   D:\civitoip20\UniXNU-luming (luming)
#   output   D:\civitoip20\out-luming2\luming
#   config   D:\civitoip20\out-luming2\luming\.config
#   ... driver/kit/package/patch/refer module resolution (package device device)
```

The macOS warning printed before the output is expected on Windows and is not an
error (`unixnu: developed and tested on macOS only; ...`).

**Windows finding (from the earlier phase, still enforced):** `scripts/unixnu/config.py`
calls `Path.read_text()` without an encoding, so on a Windows host with a GBK ANSI code
page the driver raised `UnicodeDecodeError` on a `board.toml` that originally contained
a non-ASCII (Chinese) quote. The device-repository files the driver reads
(`board.toml`, `configs/luming_defconfig`, `feeds.toml`) are now **ASCII-only**
(`docs/validation/ascii-check.log` = PASS); keep them that way. Non-ASCII prose belongs
in README.md/PORTING.md/docs, which the driver does not parse.

## 6. Not executed (deliberately out of scope for the pre-build phase)

| Command | Why not |
| --- | --- |
| `unixnu fetch`, `ipsw fetch` | network/git fetches with no build target yet; base refs already pinned in `out-luming` state |
| `unixnu build kernel` | Xcode-only; plus Darwin 22 platform archives are missing and the prelink slide/roots are not qualified (documented fail-fast gate) |
| `unixnu build userdata` | Xcode/network-only, not run on this host. The old unmeasured-LBA gate is **closed**: the capacity now comes from the measured GPT entry (125,494,779 × 4096 B = 514,026,614,784 B) |
| `unixnu build payload` | requires `--unique-chip-id` (device seed), trust cache/Data seed (device inputs) and the Darwin 22 kernel |
| `unixnu build firmware` | `uefi-luming` is a separate repository, not built in this phase |
| `unixnu bootimg` | needs the firmware FD and a device tree from the bootloader; the measured v4 header also needs the driver's v4 verify patch (`refs/UniXNU-dada/docs/patches/unixnu-bootimg-header-v4.patch`) |
| `fastboot *` | out of scope, manual, later, user-approved only |

The first GitHub Actions macOS run is expected to **fail fast at these remaining gates**
(preflight passes; `build kernel` stops on Darwin-22 archives/prelink, `build payload`
on the device inputs). The unmeasured platform-address, userdata-LBA, splash and
kernelcache-pin gates are closed by the 2026-10-22 measurement. That is the intended
pre-build behaviour, not a surprise. See `PORTING.md` §5/§6.
