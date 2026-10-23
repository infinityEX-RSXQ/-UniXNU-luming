#!/usr/bin/env python3
"""Preflight validator for the UniXNU-luming device repository (pre-build stage).

It answers one question: can this repository be handed to the UniXNU build driver
without silent mistakes, and are the values that only a real device can provide
clearly marked?

Checks (all read-only, writes only to a temp directory):
  1. Python version (the UniXNU driver itself needs 3.11+).
  2. Required repository files exist.
  3. Every TOML file parses (board.toml, feeds.toml, package/*.toml, ...).
  4. board.toml follows docs/reference/configuration.md: required keys, types,
     IPSW identity consistency, userdata windows, payload/ADT wiring, firmware
     paths, bootimg ranges.
  5. configs/luming_defconfig parses with the same rules as scripts/unixnu/
     config.py (y / n / "text" / 123 / "# CONFIG_X is not set") and the flags
     it sets are documented in docs/guide/build-kernel.md.
  6. scripts/make-luming-adt.py generates a tree with the measured platform
     constants (no placeholder flags) and the produced tree passes `--check`,
     including the measured-address verification; a tree whose addresses differ
     from the constants is rejected and a truncated tree fails `--check`.  A
     temporary copy of the tool with one constant unset must refuse to fabricate a
     value, and the synthetic-placeholder mode must still validate the encoding
     (negative regressions).
  7. TODO(device) inventory: count, per-file list, and the "device inputs"
     summary; --list-todos prints the file:line list, --strict makes any
     TODO(device) a failure.

Exit status: 0 = pass (warnings allowed), 1 = failure, 2 = usage error.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'scripts'
ADT_TOOL = SCRIPTS / 'make-luming-adt.py'
CONFIG = REPO / 'configs' / 'luming_defconfig'

REQUIRED_FILES = (
    'board.toml',
    'configs/luming_defconfig',
    'feeds.toml',
    'scripts/make-luming-adt.py',
    'scripts/luming_identity.py',
    'scripts/check-luming.py',
    'tools/gen-identity.py',
    'package/unixnu.toml',
    'package/README.md',
    'package/LumingKit/Kit.toml',
    'docs/STATUS.md',
    '.github/workflows/macos-build.yml',
    'README.md',
    'PORTING.md',
    '.gitmodules',
)

TODO_MARKERS = ('TODO(device)', 'TODO(ipsw)', 'TODO(kernel)', 'TODO(uefi)', 'TODO(host)')
TODO_RE = re.compile(r'TODO\((?:device|ipsw|kernel|uefi|host)\)')
SCAN_SUFFIXES = ('.toml', '.py', '.md', '.yml', '.yaml', '.ps1', '.sh', '.cfg', '.txt')
SCAN_SKIP = {'.git', '.cache', '__pycache__', 'build', 'out'}

DEFCONFIG_DOCUMENTED = {
    'XNU_SOURCE_CORECRYPTO14': 'build-kernel.md source corecrypto provider',
    'XNU_SOURCE_PTHREAD': 'build-kernel.md source pthread component',
    'XNU_ARM_DT_PLATFORM': 'Apple-format device tree platform expert',
    'XNU_ARMVIRT_PLATFORM': 'QEMU virt platform expert',
    'XNU_ARMCPU': 'architectural CPU and interrupt provider',
    'XNU_NATIVE_UFS': 'UFS storage driver',
    'XNU_NATIVE_AES': 'AES accelerator driver',
    'XNU_NATIVE_VIRTIO_BLOCK': 'VirtIO block driver',
    'XNU_NATIVE_RAM_BLOCK': 'RAM block driver',
    'XNU_NATIVE_VIRTIO_CONSOLE': 'VirtIO console transport',
    'XNU_NATIVE_MOBILE_DISPLAY': 'mobile display provider',
    'XNU_NATIVE_LEGACY_DISPLAY': 'legacy display provider',
    'XNU_NATIVE_ADRENO': 'Adreno GPU driver',
    'XNU_NATIVE_OFFLINE_GPU': 'offline GPU broker',
    'XNU_VERIFIED_ROOT_PROBE': 'verified root provider',
    'XNU_NATIVE_CS_HASH_CACHE': '4 KiB code-signature hashes',
    'XNU_NATIVE_ARCH_TIMER': 'architectural timer entry',
    'XNU_NATIVE_PAN': 'ARMv8.1 PAN support',
    'XNU_DT_PANIC_REGION': 'panic region from the device tree',
    'XNU_TRUST_CACHE_RUNTIME': 'trust cache runtime',
    'XNU_BOOT_PROGRESS': 'boot progress layer',
    'XNU_SOURCE_MOCKFS': 'mockfs test file',
}

YES_NO_STRINGS = ('prelinked',)


def fail(message: str) -> None:
    print(f'FAIL  {message}')


def warn(message: str) -> None:
    print(f'WARN  {message}')


def ok(message: str) -> None:
    print(f'ok    {message}')


def load_toml(path: Path, errors: list[str]) -> dict:
    try:
        with path.open('rb') as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        errors.append(f'{path.relative_to(REPO)}: missing')
        return {}
    except tomllib.TOMLDecodeError as error:
        errors.append(f'{path.relative_to(REPO)}: TOML error: {error}')
        return {}


def check_python(errors: list[str]) -> None:
    if sys.version_info < (3, 11):
        errors.append(f'python {sys.version.split()[0]} is too old; UniXNU needs 3.11+')
    else:
        ok(f'python {sys.version.split()[0]}')


def check_files(errors: list[str]) -> None:
    missing = [name for name in REQUIRED_FILES if not (REPO / name).exists()]
    for name in missing:
        errors.append(f'missing required file: {name}')
    if not missing:
        ok(f'required files present ({len(REQUIRED_FILES)})')
    for folder in ('drivers', 'kits', 'patches', '.cache/firmware'):
        if not (REPO / folder).is_dir():
            errors.append(f'missing directory: {folder}/ (UniXNU device-repository layout)')
    if not errors or all('directory' not in item for item in errors):
        ok('repository layout')


def check_imports(errors: list[str]) -> None:
    for path in sorted(SCRIPTS.glob('*.py')):
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError as error:
            errors.append(f'{path.relative_to(REPO)}: syntax error at line {error.lineno}: {error.msg}')
    ok('scripts parse as Python')


def check_identity_module(errors: list[str]) -> None:
    path = SCRIPTS / 'luming_identity.py'
    if not path.is_file():
        return
    try:
        module: dict = {}
        exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module)
    except Exception as error:  # noqa: BLE001 - report any failure
        errors.append(f'scripts/luming_identity.py: cannot execute: {error}')
        return
    product = dict(module.get('PRODUCT_PROPS', []))
    expectations = {
        'product-name': b'iPhone X\0',
        'product-description': b'iPhone X\0',
        'artwork-device-idiom': b'phone\0',
        'artwork-display-gamut': b'P3\0',
        'compatible-device-fallback': b'iPhone9,2\0',
        'artwork-scale-factor': (3).to_bytes(4, 'little'),
        'artwork-device-subtype': (2436).to_bytes(4, 'little'),
        'thin-bezel': (1).to_bytes(4, 'little'),
    }
    for key, value in expectations.items():
        if product.get(key) != value:
            errors.append(f'scripts/luming_identity.py: /product {key} is {product.get(key)!r}, expected {value!r}')
    if 'product-id' in product:
        errors.append('scripts/luming_identity.py: the per-chip product-id must not be copied from the donor')
    buttons = dict(module.get('BUTTON_PROPS', []))
    if product and not product:
        return
    if 'button-names' not in buttons:
        errors.append('scripts/luming_identity.py: BUTTON_PROPS has no button-names')
    else:
        for name in (b'volup\0', b'voldown\0', b'hold\0'):
            if name not in buttons['button-names']:
                errors.append(f'scripts/luming_identity.py: /buttons names lack {name!r}')
    if buttons.get('home-button-type') != (2).to_bytes(4, 'little'):
        errors.append('scripts/luming_identity.py: /buttons home-button-type is not 2')
    if product and buttons:
        ok(f'D22AP identity data ({len(product)} /product, {len(buttons)} /buttons properties)')


def find_base_repo() -> Path | None:
    """Locate a UniXNU base checkout next to this device repository, if present."""
    candidates = [REPO.parent / 'UniXNU-main' / 'UniXNU-main', REPO.parent / 'UniXNU', REPO.parent / 'unixnu']
    for candidate in candidates:
        if (candidate / 'unixnu').is_file() and (candidate / 'scripts' / 'unixnu' / 'config.py').is_file():
            return candidate
    return None


def check_archives(kernel: dict, errors: list[str], warnings: list[str]) -> None:
    """Every [kernel] archives entry must name a script the build tree will have."""
    base = find_base_repo()
    entries = [entry.split()[0] for entry in kernel.get('archives', []) if entry.strip()]
    device_scripts = {path.name for path in SCRIPTS.glob('*.py')}
    if base is None:
        warnings.append('[kernel] archives: no UniXNU base checkout found next to the device repository; '
                        'cannot verify that every builder exists in the build tree')
        return
    base_scripts = {path.name for path in (base / 'scripts').glob('*.py')}
    for entry in entries:
        if entry not in base_scripts and entry not in device_scripts:
            errors.append(f'board.toml: [kernel] archives entry {entry!r} exists in neither the base repo '
                          f'({base.name}/scripts) nor the device repo scripts/; unixnu build kernel would fail')
    if entries:
        ok(f'[kernel] archives builders exist in the base repo ({len(entries)} entries)')


def check_board(board: dict, errors: list[str], warnings: list[str]) -> dict:
    spec = board.get('board', {})
    for key in ('name', 'soc', 'description', 'kit'):
        if not isinstance(spec.get(key), str) or not spec.get(key):
            errors.append(f'board.toml: [board] {key} must be a non-empty string')
    if spec.get('name') != 'luming':
        errors.append(f'board.toml: [board] name is {spec.get("name")!r}, expected "luming"')
    if spec.get('soc') != 'SM8735':
        errors.append(f'board.toml: [board] soc is {spec.get("soc")!r}, expected "SM8735"')
    if spec.get('kit') != 'luming':
        errors.append(f'board.toml: [board] kit is {spec.get("kit")!r}, expected "luming"')
    if spec.get('example'):
        errors.append('board.toml: [board] example = true would make build/bootimg refuse the repository')
    if spec.get('name') == 'luming' and not errors:
        ok('board.toml [board]')

    ipsw = board.get('ipsw', {})
    expected_ipsw = {
        'product': 'iPhone10,3', 'version': '16.7.16', 'build': '20H392',
        'device_class': 'd22ap', 'chip_id': 0x8015, 'board_id': 6,
    }
    for key, value in expected_ipsw.items():
        if ipsw.get(key) != value:
            errors.append(f'board.toml: [ipsw] {key} is {ipsw.get(key)!r}, expected {value!r}')
    url = ipsw.get('url')
    if not isinstance(url, str) or not url.startswith('https://'):
        errors.append('board.toml: [ipsw] url must be an https URL')
    elif 'iPhone10,3' not in url or '16.7.16' not in url or '20H392' not in url:
        errors.append('board.toml: [ipsw] url does not look like the iPhone10,3 16.7.16 20H392 restore')
    fetch = ipsw.get('fetch', [])
    for member in ('KernelCache', 'DeviceTree', 'StaticTrustCache', 'Cryptex1,SystemTrustCache'):
        if member not in fetch:
            errors.append(f'board.toml: [ipsw] fetch lacks {member}')
    if not ipsw.get('sha256'):
        warnings.append('board.toml: [ipsw.sha256] member pins are TODO(ipsw): '
                        'the extracted members are not pinned yet (whole-IPSW hash is not a member pin)')
    ok('board.toml [ipsw] identity matches the verified d22ap iOS 16.7.16 build')

    userdata = board.get('userdata', {})
    if userdata.get('lba_bytes') != 4096:
        errors.append(f'board.toml: [userdata] lba_bytes is {userdata.get("lba_bytes")!r}, expected 4096')
    lba_values = []
    for key in ('first_lba', 'last_lba'):
        value = userdata.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f'board.toml: [userdata] {key} must be an integer')
            lba_values = []
            continue
        lba_values.append(value)
        if value == 0:
            errors.append(f'board.toml: [userdata] {key} = 0 is the unmeasured sentinel: '
                          'the GPT entry "userdata" was measured on 2026-10-22 and must not regress')
    if len(lba_values) == 2:
        first, last = lba_values
        if last <= first:
            errors.append(f'board.toml: [userdata] last_lba {last} does not follow first_lba {first}')
        else:
            capacity = (last - first + 1) * (userdata.get('lba_bytes') or 4096)
            ok(f'board.toml [userdata] measured GPT entry {first}..{last} '
               f'({capacity / 2**30:.2f} GiB)')
    windows = {window.get('name'): window.get('component') for window in userdata.get('windows', [])}
    if windows.get('OS') != 'OS' or windows.get('SystemOS') != 'Cryptex1,SystemOS':
        errors.append('board.toml: [userdata] windows must map OS->OS and SystemOS->Cryptex1,SystemOS')

    kernel = board.get('kernel', {})
    if kernel.get('defconfig') != 'luming_defconfig':
        errors.append('board.toml: [kernel] defconfig must be "luming_defconfig"')
    if not isinstance(kernel.get('archives'), list) or not kernel.get('archives'):
        errors.append('board.toml: [kernel] archives must be a non-empty list')
    check_archives(kernel, errors, warnings)
    prelink = kernel.get('prelink', {})
    if prelink.get('tool') != 'kits/NativePrelink/emit_prelink.py':
        errors.append('board.toml: [kernel.prelink] tool must be kits/NativePrelink/emit_prelink.py')
    if not prelink.get('kernel'):
        errors.append('board.toml: [kernel.prelink] kernel path is missing')
    if not prelink.get('slide'):
        warnings.append('board.toml: [kernel.prelink] slide is TODO(device)/UNVERIFIED (Darwin 22 prelink not demonstrated)')
    if not prelink.get('early_security_init'):
        warnings.append('board.toml: [kernel.prelink] early_security_init is TODO(device)/UNVERIFIED')
    pin = prelink.get('kernelcache_sha256')
    if not pin:
        warnings.append('board.toml: [kernel.prelink] kernelcache_sha256 is TODO(device)')
    elif not re.fullmatch(r'[0-9a-f]{64}', str(pin)):
        errors.append('board.toml: [kernel.prelink] kernelcache_sha256 must be 64 lowercase hex characters '
                      '(sha256 of the decompressed arm64 Mach-O slice)')
    else:
        ok('[kernel.prelink] kernelcache_sha256 pinned (decompressed arm64 Mach-O)')
    if not prelink.get('roots'):
        warnings.append('board.toml: [kernel.prelink] roots is TODO(kernel): no Darwin 22 prelink evidence')

    firmware = board.get('firmware', {})
    if firmware.get('config') != 'luming':
        errors.append('board.toml: [firmware] config must be "luming"')
    if firmware.get('dsc') != 'Platforms/Xiaomi/lumingPkg/luming.dsc':
        errors.append('board.toml: [firmware] dsc must be Platforms/Xiaomi/lumingPkg/luming.dsc')
    if not str(firmware.get('fd', '')).startswith('Build/lumingPkg/') or '{target}' not in str(firmware.get('fd')):
        errors.append('board.toml: [firmware] fd must be Build/lumingPkg/{target}_CLANGPDB/FV/SILICIUM_UEFI.fd')
    if firmware.get('preflight') != ['scripts/check-luming.py']:
        errors.append('board.toml: [firmware] preflight must be ["scripts/check-luming.py"]')
    for package in firmware.get('packages', []):
        if package.count('{') - package.count('}') != 0:
            errors.append(f'board.toml: [firmware] packages entry has unbalanced braces: {package}')
    if '{tree}/package' not in firmware.get('packages', []):
        errors.append('board.toml: [firmware] packages lacks {tree}/package')
    defines = board.get('firmware', {}).get('defines', {})
    if defines.get('DEVICE_MODEL') != 'luming':
        errors.append('board.toml: [firmware.defines] DEVICE_MODEL must be "luming"')

    payload = board.get('payload', {})
    volume = payload.get('volume', '')
    if not isinstance(volume, str) or not 0 < len(volume) <= 11:
        errors.append('board.toml: [payload] volume must be a FAT label of at most 11 characters')
    if payload.get('loader', 'firmware') != 'firmware':
        errors.append('board.toml: [payload] loader must be "firmware"')
    dram = payload.get('dram_size')
    if not isinstance(dram, str) or not re.fullmatch(r'0x[0-9a-fA-F]+', dram):
        errors.append('board.toml: [payload] dram_size must be a hex string like "0x300000000"')
    elif int(dram, 0) not in (0x300000000, 0x400000000):
        warnings.append(f'board.toml: [payload] dram_size={dram} is neither the measured 12 GiB SKU '
                        '(0x300000000) nor the 16 GiB SKU (0x400000000); re-measure the unit')
    else:
        ok(f'board.toml [payload] dram_size={dram} measured ('
           f'{"12" if int(dram, 0) == 0x300000000 else "16"} GiB SKU)')
    for argument in payload.get('kernel_args', []):
        if not re.fullmatch(r'[A-Za-z0-9_.\-]+(=[A-Za-z0-9_.,\-]+)?', argument):
            errors.append(f'board.toml: [payload] kernel argument has unsupported characters: {argument}')
    if not payload.get('panic_screen'):
        warnings.append('board.toml: [payload] panic_screen is disabled for the pre-build set: the '
                        'patch-xnu-entry-probe.py script it needs exists only in UniXNU-sheng '
                        '(TODO(kernel): port it for Darwin 22 before enabling)')

    adt = payload.get('adt', {})
    if adt.get('tool') != 'scripts/make-luming-adt.py':
        errors.append('board.toml: [payload.adt] tool must be "scripts/make-luming-adt.py"')
    if not isinstance(adt.get('chip_id'), str):
        errors.append('board.toml: [payload.adt] chip_id must be a string (hex)')
    if adt.get('chip_id') != '8015':
        errors.append(f'board.toml: [payload.adt] chip_id is {adt.get("chip_id")!r}, '
                      'expected "8015" (A11 CPID identity seed of the iPhone X userland)')
    if adt.get('unique_chip_id') != 'required':
        errors.append('board.toml: [payload.adt] unique_chip_id must be "required" (seed is never stored)')
    if adt.get('ipad_identity') is not True:
        warnings.append('board.toml: [payload.adt] ipad_identity=true keeps the four activation/security-status '
                        '/chosen properties of the iOS 16 identity; verify this is intended')
    if adt.get('packed_data_ramdisk') is not True:
        errors.append('board.toml: [payload.adt] packed_data_ramdisk must be true for the DBRDISK1 Data seed')
    cpu_regs = adt.get('cpu_regs', '')
    try:
        regs = [int(item, 0) for item in cpu_regs.split(',') if item]
    except (AttributeError, ValueError):
        regs = []
        errors.append('board.toml: [payload.adt] cpu_regs must be comma-separated integers')
    if len(regs) != 8:
        errors.append(f'board.toml: [payload.adt] cpu_regs has {len(regs)} entries, expected 8 (SM8735 1+3+2+2)')
    if regs and regs[0] != 0:
        errors.append('board.toml: [payload.adt] cpu_regs must start with the boot CPU MPIDR 0')
    if adt.get('owned_memory_size') is not None:
        warnings.append('board.toml: [payload.adt] owned_memory_size is a candidate window (TODO(device))')
    if not adt.get('root_match_volume_uuid'):
        warnings.append('board.toml: [payload.adt] root_match_volume_uuid is TODO(device): '
                        'the SystemOS APFS UUID is not stored in the repository')
    if not payload.get('trust_cache_sha256'):
        warnings.append('board.toml: [payload] trust_cache_sha256 and the Data seed are pending device inputs: '
                        'the trust-cache owner bundle is built from the 20H392 IPSW and the Data seed is supplied '
                        'with --data-ramdisk; neither is stored in this repository')
    ok('board.toml [payload]/[payload.adt]/[firmware] wiring')

    bootimg = board.get('bootimg', {})
    image = bootimg.get('image', {}) if isinstance(bootimg, dict) else {}
    expected_image = {'base': 0x0, 'kernel_offset': 0x8000, 'ramdisk_offset': 0x01000000,
                      'tags_offset': 0x100, 'pagesize': 4096, 'header_version': 4}
    bootimg_errors = len(errors)
    if not image:
        errors.append('board.toml: [bootimg.image] missing: the stock boot.img header v4 and its '
                      'mkbootimg args were measured on 2026-10-22 and must be recorded')
    else:
        for key, value in expected_image.items():
            if image.get(key) != value:
                errors.append(f'board.toml: [bootimg.image] {key} is {image.get(key)!r}, '
                              f'expected measured value {value!r}')
    nodes = bootimg.get('dtb_node', []) if isinstance(bootimg, dict) else []
    splash = [node for node in nodes
              if isinstance(node, dict) and node.get('path') == '/reserved-memory/splash_region']
    if not splash:
        errors.append('board.toml: [[bootimg.dtb_node]] /reserved-memory/splash_region is missing: '
                      'the measured splash region keeps the stock ABL from powering the panel off')
    else:
        props = {prop.get('name'): prop for prop in splash[0].get('props', []) if isinstance(prop, dict)}
        if props.get('label', {}).get('value') != 'cont_splash_region':
            errors.append('board.toml: [[bootimg.dtb_node]] splash label is not "cont_splash_region"')
        expected_reg = ['0x0', '0xfc800000', '0x0', '0x2b00000']
        if props.get('reg', {}).get('value') != expected_reg:
            errors.append(f'board.toml: [[bootimg.dtb_node]] splash reg is '
                          f'{props.get("reg", {}).get("value")!r}, expected measured {expected_reg!r}')
        if props.get('reg', {}).get('type') != 'x':
            errors.append('board.toml: [[bootimg.dtb_node]] splash reg type must be "x" (32-bit cells)')
    if len(errors) == bootimg_errors:
        ok('[bootimg] measured header-v4 geometry and splash-reserved-memory node')
    return board


def parse_defconfig(text: str) -> dict:
    """Same rules as scripts/unixnu/config.py:parse_config()."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        unset = re.fullmatch(r'#\s*CONFIG_(\w+) is not set', line)
        if unset:
            values[unset.group(1)] = False
            continue
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'CONFIG_(\w+)=(.*)', line)
        if not match:
            raise ValueError(f'line {number} is not CONFIG_NAME=value: {line}')
        name, raw = match.groups()
        if raw == 'y':
            values[name] = True
        elif raw == 'n':
            values[name] = False
        elif raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            values[name] = raw[1:-1]
        elif re.fullmatch(r'-?(0x[0-9a-fA-F]+|\d+)', raw):
            values[name] = int(raw, 16 if raw.lstrip('-').startswith('0x') else 10)
        else:
            raise ValueError(f'line {number}: value must be y, n, a number or a quoted string: {line}')
    return values


def check_defconfig(errors: list[str], warnings: list[str]) -> None:
    if not CONFIG.is_file():
        return
    try:
        values = parse_defconfig(CONFIG.read_text(encoding='utf-8'))
    except ValueError as error:
        errors.append(f'configs/luming_defconfig: {error}')
        return
    for key in values:
        if not key.startswith('XNU_'):
            errors.append(f'configs/luming_defconfig: {key} does not start with XNU_ (becomes a kernel variable)')
        elif key not in DEFCONFIG_DOCUMENTED:
            warnings.append(f'configs/luming_defconfig: {key} is not in the docs/guide/build-kernel.md flag table '
                            '(verify the kernel script consumes it)')
    if values.get('XNU_ARM_DT_PLATFORM') is not True:
        errors.append('configs/luming_defconfig: CONFIG_XNU_ARM_DT_PLATFORM=y is required for the ADT platform expert')
    if values.get('XNU_NATIVE_UFS') is not True:
        errors.append('configs/luming_defconfig: CONFIG_XNU_NATIVE_UFS=y is required for the UFS root path')
    if values.get('XNU_ARMVIRT_PLATFORM') is not False:
        errors.append('configs/luming_defconfig: CONFIG_XNU_ARMVIRT_PLATFORM must not be set for a phone board')
    if values.get('XNU_CRYPTO_BOOTSTRAP') != 'prelinked':
        errors.append('configs/luming_defconfig: CONFIG_XNU_CRYPTO_BOOTSTRAP="prelinked" is required '
                      'by the prelink build path')
    # Mirror the hard prerequisite of scripts/build-xnu-4k-baseline.sh:64-69, which exits
    # before compiling when boot progress is enabled without the audited build chain.
    if values.get('XNU_BOOT_PROGRESS') is True:
        missing = [flag for flag in ('XNU_NATIVE_UFS', 'XNU_OWNER_ROOT_AUDIT',
                                     'XNU_NATIVE_VIRTIO_BLOCK', 'XNU_SOURCE_CORECRYPTO14')
                   if values.get(flag) is not True]
        if missing:
            errors.append('configs/luming_defconfig: CONFIG_XNU_BOOT_PROGRESS=y requires '
                          + ', '.join(f'CONFIG_{flag}=y' for flag in missing)
                          + ' (scripts/build-xnu-4k-baseline.sh:64-69 exits before compiling otherwise); '
                            'keep BOOT_PROGRESS=n until the Darwin 22 audited chain exists')
        base = find_base_repo()
        boot_progress_builder = (SCRIPTS / 'build-boot-progress.py').is_file() or (
            base is not None and (base / 'scripts' / 'build-boot-progress.py').is_file())
        if not boot_progress_builder:
            errors.append('configs/luming_defconfig: CONFIG_XNU_BOOT_PROGRESS=y needs '
                          'scripts/build-boot-progress.py, absent from the base and device repositories')
    enabled = sorted(key for key, value in values.items() if value is True)
    ok(f'configs/luming_defconfig ({len(values)} flags, {len(enabled)} enabled)')


def check_feeds_and_package(errors: list[str]) -> None:
    feeds = load_toml(REPO / 'feeds.toml', errors)
    for source in feeds.get('source', []):
        for key in ('name', 'kind'):
            if key not in source:
                errors.append(f'feeds.toml: every [[source]] needs {key}')
        if source.get('kind') not in ('driver', 'kit', 'patch', 'package', 'refer', 'script'):
            errors.append(f'feeds.toml: unknown kind {source.get("kind")!r}')
    if not feeds.get('source'):
        warn('feeds.toml: no extra sources (fine for the pre-build stage; device drivers come from package/ or submodules)')
    package_config = load_toml(REPO / 'package' / 'unixnu.toml', errors)
    if package_config and 'version' not in package_config:
        errors.append('package/unixnu.toml: missing version')
    kit = load_toml(REPO / 'package' / 'LumingKit' / 'Kit.toml', errors)
    if kit:
        if kit.get('schema') != 1:
            errors.append('package/LumingKit/Kit.toml: schema must be 1')
        if not kit.get('kit', {}).get('name'):
            errors.append('package/LumingKit/Kit.toml: [kit] name is missing')
        match = kit.get('match', {})
        for key in ('arch', 'fdt_compatible', 'firmware_vendor', 'requires_protocol'):
            if key not in match:
                errors.append(f'package/LumingKit/Kit.toml: [match] {key} is required')
        if not kit.get('driver') and not kit.get('kext') and not kit.get('overlay'):
            warn('package/LumingKit/Kit.toml: no drivers/kexts/overlays yet (honest pre-build state)')
    # every TOML in the repository parses
    for path in sorted(REPO.rglob('*.toml')):
        if any(part in SCAN_SKIP for part in path.parts):
            continue
        load_toml(path, errors)
    ok('TOML files parse (board, feeds, package)')


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=120)


def check_adt(errors: list[str], warnings: list[str]) -> None:
    if not ADT_TOOL.is_file():
        errors.append('scripts/make-luming-adt.py missing')
        return
    payload_step = [sys.executable, str(ADT_TOOL),
                    '--dram-size', '0x300000000',
                    '--owned-memory-size', '0x40000000',
                    '--chip-id', '8015',
                    '--unique-chip-id', '0011223344556677',
                    '--cpu-regs', '0,0x100,0x200,0x300,0x400,0x500,0x600,0x700',
                    '--psci-method', 'smc',
                    '--packed-data-ramdisk',
                    '--trust-cache',
                    '--ipad-identity']
    check_step = [sys.executable, str(ADT_TOOL), '--check',
                  '--dram-size', '0x300000000',
                  '--cpu-regs', '0,0x100,0x200,0x300,0x400,0x500,0x600,0x700',
                  '--packed-data-ramdisk', '--trust-cache']
    start = len(errors)
    with tempfile.TemporaryDirectory(prefix='luming-check-') as scratch:
        scratch_path = Path(scratch)
        tree = scratch_path / 'kernel.macho.adt'
        # 1) generation with the measured constants; no placeholder flag is needed
        result = run(payload_step + ['--output', str(tree)], REPO)
        if result.returncode:
            errors.append('ADT tool failed to generate with the measured constants:\n'
                          + result.stderr.strip())
            return
        if 'MEASURED' not in result.stderr:
            errors.append('ADT tool did not report the platform addresses as measured')
        if 'NOT BOOTABLE' in result.stderr or 'SYNTHETIC' in result.stderr:
            errors.append('ADT tool generation with the measured constants still reports a '
                          'synthetic/non-bootable tree')
        if not tree.is_file() or tree.stat().st_size < 4096:
            errors.append(f'ADT tool produced no usable tree '
                          f'({tree.stat().st_size if tree.is_file() else 0} bytes)')
            return
        # 2) the tree must pass the structural contract and the measured-address check
        result = run(check_step + ['--output', str(tree)], REPO)
        if result.returncode:
            errors.append('ADT --check rejected the measured tree:\n' + result.stderr.strip())
        elif 'PASS' not in result.stdout or 'measured' not in result.stdout:
            errors.append('ADT --check produced no measured PASS line')
        # 3) negative regression: a tree with a non-measured address must be rejected
        altered = scratch_path / 'altered.adt'
        result = run(payload_step + ['--dram-base', '0x90000000', '--output', str(altered)], REPO)
        if result.returncode:
            errors.append('ADT tool failed to generate the address-verification fixture:\n'
                          + result.stderr.strip())
        else:
            result = run(check_step + ['--output', str(altered)], REPO)
            if result.returncode == 0:
                errors.append('ADT --check accepted a tree whose DRAM base is not the measured constant '
                              '(measured-address verification regression failed)')
        # 4) negative regression: a truncated tree must be refused
        truncated = scratch_path / 'truncated.adt'
        truncated.write_bytes(tree.read_bytes()[:-16])
        result = run(check_step + ['--output', str(truncated)], REPO)
        if result.returncode == 0:
            errors.append('ADT --check accepted a truncated tree (negative regression failed)')
        # 5) no fabricated addresses: a copy of the tool with one constant unset must
        #    refuse, and the explicit synthetic-placeholder mode must still work.
        source = ADT_TOOL.read_text(encoding='utf-8')
        mutated = source.replace('DRAM_BASE = 0x80000000', 'DRAM_BASE = None', 1)
        if mutated == source:
            errors.append('ADT negative-regression fixture: DRAM_BASE assignment not found in the tool')
        else:
            fixture = scratch_path / 'fixture'
            fixture.mkdir()
            (fixture / 'make-luming-adt.py').write_text(mutated, encoding='utf-8')
            shutil.copyfile(SCRIPTS / 'luming_identity.py', fixture / 'luming_identity.py')
            fixture_tool = fixture / 'make-luming-adt.py'
            refused = fixture / 'refused.adt'
            result = run([sys.executable, str(fixture_tool), *payload_step[2:],
                          '--output', str(refused)], fixture)
            if result.returncode == 0:
                errors.append('ADT tool generated a tree with an unmeasured (None) address and without '
                              '--todo-placeholder-addresses; it must refuse (no fabricated SoC values)')
            elif 'TODO(device)' not in result.stderr:
                errors.append('ADT tool refusal message does not name TODO(device)')
            placeholder = fixture / 'placeholder.adt'
            result = run([sys.executable, str(fixture_tool), *payload_step[2:],
                          '--todo-placeholder-addresses', '--output', str(placeholder)], fixture)
            if result.returncode:
                errors.append('ADT tool failed in --todo-placeholder-addresses mode for an unmeasured '
                              'value:\n' + result.stderr.strip())
            elif 'NOT BOOTABLE' not in result.stderr:
                errors.append('ADT tool did not warn that the placeholder tree is not bootable')
    if len(errors) == start:
        ok('ADT tool: measured generation, --check + address verification, truncation refusal, '
           'unmeasured-value refusal, placeholder encoding test')


def todo_files() -> list[tuple[Path, int, str]]:
    found = []
    for path in sorted(REPO.rglob('*')):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SCAN_SKIP for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for match in TODO_RE.finditer(line):
                found.append((path, number, match.group(0)))
    return found


def check_todos(errors: list[str], warnings: list[str], strict: bool, list_todos: bool) -> None:
    found = todo_files()
    by_file: dict[Path, int] = {}
    for path, _, _ in found:
        by_file[path] = by_file.get(path, 0) + 1
    device = sum(1 for _, _, marker in found if marker == 'TODO(device)')
    # Markers in runnable config/code are the actionable pre-build gates; the docs
    # also *mention* TODO(device) when explaining the gates, so doc counts are larger.
    code_suffixes = ('.toml', '.py', '.ps1', '.yml', '.yaml', '.sh', '.cfg')
    code_markers = [(path, number, marker) for path, number, marker in found
                    if path.suffix in code_suffixes]
    code_device = sum(1 for _, _, marker in code_markers if marker == 'TODO(device)')
    if list_todos:
        for path, number, marker in found:
            print(f'{path.relative_to(REPO)}:{number}: {marker}')
    if found:
        warn(f'TODO inventory: {len(found)} open marker(s) '
             f'({len(code_markers)} in code/config, {code_device} of those TODO(device); '
             f'the rest are documentation references)')
        for path, count in sorted(by_file.items()):
            print(f'      {count:>3}  {path.relative_to(REPO)}')
        if strict:
            errors.append(f'{device} TODO(device) marker(s) remain and --strict was requested')
    else:
        ok('no TODO markers remain')
    if found and not strict:
        warnings.append('pre-build stage: any TODO(device) value must be measured before a boot attempt '
                        '(see PORTING.md "device inputs"); the authoritative per-field list is the '
                        'code/config marker set printed by --list-todos, not the documentation counts')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Preflight validator for UniXNU-luming')
    parser.add_argument('--strict', action='store_true', help='fail on any TODO(device) value that remains')
    parser.add_argument('--list-todos', action='store_true', help='print every TODO marker as file:line')
    parser.add_argument('--skip-adt', action='store_true', help='skip the ADT tool smoke test')
    args = parser.parse_args(argv)

    print(f'UniXNU-luming preflight: {REPO}')
    errors: list[str] = []
    warnings: list[str] = []

    check_python(errors)
    check_files(errors)
    if not errors:
        board = load_toml(REPO / 'board.toml', errors)
        check_board(board, errors, warnings)
        check_identity_module(errors)
        check_imports(errors)
        check_defconfig(errors, warnings)
        check_feeds_and_package(errors)
        if not args.skip_adt:
            check_adt(errors, warnings)
    check_todos(errors, warnings, args.strict, args.list_todos)

    for line in warnings:
        print(f'WARN  {line}')
    if errors:
        print(f'\nPREFLIGHT FAILED: {len(errors)} error(s), {len(warnings)} warning(s)')
        for message in errors:
            print(f'  - {message}')
        return 1
    print(f'\nPREFLIGHT PASSED: {len(warnings)} warning(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
