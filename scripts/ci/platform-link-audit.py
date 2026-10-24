"""Relink the actual source kernel with two audited upstream platform archives.

This is source link validation only. It does not enable runtime timer/crypto,
create a boot container, emit donor prelink, or execute the resulting kernel.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess

COMPONENTS = ('ArmCpu', 'ArmDTPlatform')
REQUIRED = ('_armcpu_registered', '_armcpu_gic_ready', '_armcpu_timer_interrupts',
            '_armdt_platform_configured', '_armdt_platform_nvram_proxy')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def link_command(original, work, archives):
    # Do not silently replace a different link/provider profile or old archives.
    if original[:3] != ['make', '-C', '.cache/xnu-work'] or original[3] != 'build':
        raise ValueError('Expected this source build make command')
    if any(arg.startswith(('LD_KERNEL_ARCHIVES=', 'XNU_EXTERNAL_KERNEL_ARCHIVES=')) for arg in original):
        raise ValueError('Existing archive/provider override requires separate review')
    roots = [arg for arg in original if arg.startswith('OBJROOT=')]
    if roots != ['OBJROOT=' + str(work / 'build/xnu/ios-obj')]:
        raise ValueError('Output must stay in this exact temporary build')
    if len(archives) != 2 or any(' ' in str(path) or ',' in str(path) for path in archives):
        raise ValueError('Need exactly two unambiguous archive paths')
    fake_sdk = work / '.cache/fakeroot'
    inputs = f'-L{fake_sdk}/usr/local/lib/kernel -lfirehose_kernel'
    inputs += ''.join(' -Wl,-force_load,' + str(path) for path in archives)
    return original + ['LD_KERNEL_ARCHIVES=' + inputs,
                       'XNU_EXTERNAL_KERNEL_ARCHIVES=' + ' '.join(map(str, archives))]


def global_names(path, mode):
    output = subprocess.check_output(['xcrun', 'nm', '-g', mode, '-j', str(path)], text=True)
    return {line.strip() for line in output.splitlines() if line.startswith('_')}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--work', type=Path, required=True)
    args = cli.parse_args()
    work = args.work.resolve()
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Real link validation requires macOS Actions')
    if not work.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('Only this cloud temporary build is writable')
    target = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    kernel = target / 'mach.development.bcm2837.unstripped'
    audit = json.loads((work / 'reports/driver-archives/audit.json').read_text())
    if not audit['passed'] or audit['kernel_sha256'] != sha(kernel):
        raise ValueError('Archive audit does not match this exact baseline kernel')
    selected = [next(item for item in audit['components'] if item['component'] == name)
                for name in COMPONENTS]
    definitions = set().union(*(set(item['defined_global_names']) for item in selected))
    kernel_names = global_names(kernel, '-U')
    archives = []
    for item in selected:
        missing = set(item['imported_global_names']) - definitions - kernel_names - {'___dso_handle'}
        if not item['compiled'] or missing or item['kernel_name_collisions']:
            raise ValueError('Selected two-library closure failed independently of ArmVirtPlatform')
        path = (work / item['archive']['path']).resolve()
        if not path.is_relative_to(work / 'build/driver-archives') or sha(path) != item['archive']['sha256']:
            raise ValueError('Actual archive changed after audit')
        archives.append(path)
    for name in REQUIRED:
        if name in kernel_names:
            raise ValueError('Baseline unexpectedly already contains the selected drivers')
    original = json.loads((work / 'reports/kernel-make-command.json').read_text())
    command = link_command(original, work, archives)
    before_flags = [arg for arg in original if 'FLAGS' in arg or 'ARCH_TIMER' in arg]
    after_flags = [arg for arg in command if 'FLAGS' in arg or 'ARCH_TIMER' in arg]
    if before_flags != after_flags:
        raise ValueError('Runtime/compiler flags changed during link validation')
    report_dir = work / 'reports/platform-link'
    report_dir.mkdir(exist_ok=False)
    report = {'scope': 'real source kernel relink only; not runtime/boot or complete ABI qualification',
              'components': list(COMPONENTS), 'original_make_arguments': original,
              'make_arguments': command, 'baseline_kernel_sha256': sha(kernel),
              'runtime_flags_unchanged': True, 'passed': False, 'device_verified': False,
              'archives': [item['archive'] for item in selected]}
    try:
        env = dict(os.environ, KERNEL_SDKROOT=str(work / '.cache/fakeroot'),
                   XNU_AVAILABILITY_PL=str(work / '.cache/fakeroot/usr/local/libexec/availability.pl'))
        # Other baseline exports (SDK-derived compiler variables) are resolved by
        # the same MakeInc.cmd. The real target's external-archive prerequisites
        # and .LDFLAGS force a new link; no old kernel/object is deleted here.
        print('+', ' '.join(command), flush=True)
        result = subprocess.run(command, cwd=work, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (report_dir / 'make.log').write_text(result.stdout)
        print(result.stdout, flush=True)
        report['make_exit_code'] = result.returncode
        if result.returncode:
            raise RuntimeError('Platform kernel real make/link failed')
        data = kernel.read_bytes()
        if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
            raise ValueError('Expected actual ordinary ARM64 MH_EXECUTE after relink')
        after_names = global_names(kernel, '-U')
        undefined = global_names(kernel, '-u')
        report['required_driver_symbols'] = {name: name in after_names for name in REQUIRED}
        report['undefined_global_names'] = sorted(undefined)
        report['kernel_sha256'] = sha(kernel)
        report['kernel_bytes'] = len(data)
        if (undefined or not all(report['required_driver_symbols'].values()) or
                report['kernel_sha256'] == report['baseline_kernel_sha256']):
            raise ValueError('Actual linked kernel lacks expected driver definitions or is stale')
        report['passed'] = True
    finally:
        (report_dir / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Platform link: real make, both archives and final driver symbols passed; no execution', flush=True)


if __name__ == '__main__':
    main()
