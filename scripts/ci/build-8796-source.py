"""Compile the reviewed 8796 source on macOS CI, without donor prelink or images.

The real build tree lives in RUNNER_TEMP, outside the device repository. Local
--check only validates inputs and adapts text in memory; it never copies sources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

DEVICE = Path(__file__).resolve().parents[2]
BASE_COMMIT = '75bc13dd34dc4e403826793739e316a264ce00dd'
XNU_COMMIT = '1b191cb58250d0705d8a51287127505aa4bc0789'
BASELINE_HASH = '3486867d8c639eb10d31be16e875dbbdbb1d27afd97aedbb8ba930f0093c6852'
FIREHOSE_HASH = 'e30d44b2633fb509fefbb316cb2824abb394e3e5779d4b6d86f8ed2b4f91c057'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_exact(text, before, after, count):
    if text.count(before) != count:
        raise ValueError(f'Unexpected upstream context count for {before!r}')
    return text.replace(before, after)


def capture_kernel_make(script):
    """Record the expanded argv while executing the exact same real make."""
    start = script.index('\nmake -C .cache/xnu-work "${XNU_BUILD_TARGET:-build}"') + 1
    end_marker = 'DSTROOT="$ROOT/build/xnu/ios-dst"'
    end = script.index(end_marker, start) + len(end_marker)
    command = script[start:end]
    if command.count('\nmake ') or script.count(end_marker) != 1:
        raise ValueError('Ambiguous final kernel make command')
    captured = 'NUWA_KERNEL_MAKE=(' + command + ')\n'
    captured += ('python3 -c \'import json,sys; from pathlib import Path; '
                 'Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:], indent=2)+"\\n")\' '
                 '"$ROOT/reports/kernel-make-command.json" "${NUWA_KERNEL_MAKE[@]}"\n')
    captured += '"${NUWA_KERNEL_MAKE[@]}"'
    return script[:start] + captured + script[end:]


def inputs(base):
    evidence = json.loads((DEVICE / 'reports/xnu-8796-sequence/results.json').read_text(encoding='utf-8'))
    if evidence['base_head'] != BASE_COMMIT or evidence['source_head'] != XNU_COMMIT:
        raise ValueError('Review evidence pins differ')
    selection = [item for item in evidence['selection'] if item['status'] == 'virtual-pass']
    if not evidence['complete_sequence'] or len(selection) != 48:
        raise ValueError('Need the complete reviewed 48-patch selection')
    patches = []
    for item in selection:
        name = item['patch']
        if Path(name).name != name or not name.endswith('.patch'):
            raise ValueError('Unsafe patch name')
        candidate = DEVICE / 'patches/xnu-8796-candidates' / name
        if not candidate.exists():
            directory = 'kits/NativePrelink/patches' if name.startswith('xnu-10063.') else 'patches/xnu'
            candidate = base / directory / name
        if sha(candidate) != item['sha256']:
            raise ValueError(f'Reviewed patch changed: {name}')
        patches.append(candidate)
    baseline = base / 'scripts/build-xnu-4k-baseline.sh'
    firehose = base / 'scripts/build-firehose.sh'
    if sha(baseline) != BASELINE_HASH or sha(firehose) != FIREHOSE_HASH:
        raise ValueError('Unexpected upstream build scripts')
    script = baseline.read_text(encoding='utf-8')
    script = replace_exact(script, '.unstripped.noctf', '.unstripped', 3)
    script = replace_exact(script, '-miphoneos-version-min=17.0', '-miphoneos-version-min=16.0', 1)
    script = replace_exact(script, '-Wl,-ios_version_min,17.0', '-Wl,-ios_version_min,16.0', 1)
    script = capture_kernel_make(script)
    firehose_script = replace_exact(firehose.read_text(encoding='utf-8'),
                                   'IPHONEOS_DEPLOYMENT_TARGET=17.0',
                                   'IPHONEOS_DEPLOYMENT_TARGET=16.0', 1)
    for name in ('clang17-zero-init.patch', 'clang17-uio-stackbuf.patch',
                 'clang-compile-records.patch',
                 'disabled-kperf-ast.patch', 'disabled-kperf-timer.patch',
                 'disabled-kperf-scheduler.patch',
                 'disabled-kperf-ktrace.patch',
                 'disabled-kpc-task.patch',
                 'disabled-kpc-context.patch',
                 'zzzz-ci-public-profiling.patch', 'zzzz-display-probe-feature.patch'):
        compatibility = DEVICE / 'patches/xnu-8796-ci-candidates' / name
        if not compatibility.is_file():
            raise ValueError(f'Missing reviewed clang17 compatibility patch: {name}')
        patches.append(compatibility)
    donor_bridge = DEVICE / 'patches/xnu-8796-donor-candidates/vfs-getvfs-with-vfsops.patch'
    if not donor_bridge.is_file():
        raise ValueError('Missing reviewed real VFS donor bridge')
    patches.append(donor_bridge)
    return patches, script, firehose_script


def run(command, **kwargs):
    print('+', ' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--with-ufs', action='store_true')
    args = parser.parse_args()
    base = args.base.resolve()
    patches, script, firehose_script = inputs(base)
    summary = {'base_commit': BASE_COMMIT, 'xnu_commit': XNU_COMMIT,
               'patches': [{'name': p.name, 'sha256': sha(p)} for p in patches],
               'baseline_adapter_sha256': hashlib.sha256(script.encode()).hexdigest(),
               'firehose_adapter_sha256': hashlib.sha256(firehose_script.encode()).hexdigest(),
               'reviewed_base_patch_count': 48,
               'ci_compatibility_patch_count': sum(p.parent.name == 'xnu-8796-ci-candidates' for p in patches),
               'donor_compatibility_patch_count': sum(p.parent.name == 'xnu-8796-donor-candidates' for p in patches),
               'scope': 'source compile/link only; optional device archives disabled',
               'kernel_built': False, 'prelinked': False, 'device_verified': False}
    if args.check:
        shell = ('C:/Program Files/Git/bin/bash.exe' if platform.system() == 'Windows'
                 else shutil.which('bash'))
        if not shell:
            raise ValueError('bash is required to syntax-check the adapted recipes')
        for content in (script, firehose_script):
            subprocess.run([shell, '-n'], input=content, text=True, encoding='utf-8', check=True)
        summary['adapted_shell_syntax'] = 'passed; no build commands executed'
        print(json.dumps(summary, indent=2))
        return
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build mode requires the macOS Actions runner; use --check locally')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    if temporary == DEVICE or temporary.is_relative_to(DEVICE):
        raise ValueError('RUNNER_TEMP must be outside the device repository')
    work = temporary / 'nuwa-8796-source'
    work.mkdir(exist_ok=False)
    (work / 'refer').mkdir()
    (work / 'reports').mkdir()
    report = work / 'reports/build.json'
    report.write_text(json.dumps(summary, indent=2) + '\n')
    shutil.copytree(base / 'scripts', work / 'scripts')
    for name in ('drivers', 'kits'):
        if (base / name).is_dir():
            shutil.copytree(base / name, work / name)
    if (DEVICE / 'include').is_dir():
        shutil.copytree(DEVICE / 'include', work / 'include')
    patched_builder = DEVICE / 'scripts' / 'build-arm-driver.py'
    if patched_builder.is_file():
        shutil.copyfile(patched_builder, work / 'scripts' / 'build-arm-driver.py')
    for path in patches:
        folder = 'kits/NativePrelink/patches' if path.name.startswith('xnu-10063.') else 'patches/xnu'
        target = work / folder / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (work / 'scripts/build-xnu-4k-baseline.sh').write_text(script, newline='\n')
    (work / 'scripts/build-firehose.sh').write_text(firehose_script, newline='\n')
    iig_adapter = work / 'scripts/iig-darwin22.py'
    adapter_source = DEVICE / 'scripts/ci/iig-darwin22.py'
    iig_adapter.write_text(f'#!{sys.executable}\n' + adapter_source.read_text(), newline='\n')
    iig_adapter.chmod(0o755)
    real_iig = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--find', 'iig'], text=True).strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('XNU_', 'UNIXNU_'))}
    env.update(UNIXNU_XNU_TAG='xnu-8796.141.3', UNIXNU_XNU_COMMIT=XNU_COMMIT,
               UNIXNU_XNU_DARWIN='22.6.0', UNIXNU_XNU_VERSION='8796.141.3',
               UNIXNU_DRIVERKIT_TAG='xnu-8796.141.3', UNIXNU_TRUSTCACHE_TAG='xnu-8796.141.3',
               XNU_BOOT_PROGRESS='0', XNU_ZONE_PGZ_ALIGNMENT_PROBE='0', JOBS='3',
               IIG=str(iig_adapter), NUWA_REAL_IIG=real_iig)
    summary['iig_adapter_sha256'] = sha(adapter_source)
    summary['iig_compatibility'] = 'pinned generator inline RPC branches; deployment-target candidate rejected'
    try:
        # Reuse the base recipe: real source checkout, ordered patches, installhdrs,
        # real libfirehose archive and XNU build. No stubs or suppressed link errors.
        run(['bash', work / 'scripts/build-xnu-4k-baseline.sh'], cwd=work, env=env)
        kernel = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped'
        magic, cpu, subtype, filetype = struct.unpack('<IIII', kernel.read_bytes()[:16])
        if (magic, cpu, subtype, filetype) != (0xfeedfacf, 0x100000c, 0, 2):
            raise ValueError('Expected ordinary ARM64 MH_EXECUTE kernel')
        summary.update(kernel_built=True, kernel_sha256=sha(kernel),
                       kernel_path=str(kernel.relative_to(work)))
        run(['file', kernel])
        run(['xcrun', 'otool', '-l', kernel])
        if args.with_ufs:
            storage_headers = work / 'build/storage-abi/include/IOKit/storage'
            device_headers = DEVICE / 'include/IOStorageFamily'
            if device_headers.is_dir():
                storage_headers.mkdir(parents=True, exist_ok=True)
                for header in device_headers.glob('*.h'):
                    shutil.copyfile(header, storage_headers / header.name)
                sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
                shutil.copyfile(sdk / 'usr/include/TargetConditionals.h',
                                work / 'build/storage-abi/include/TargetConditionals.h')
            for component in ('ArmDTPlatform', 'ArmCpu', 'NativeUfs'):
                run([sys.executable, '-B', work / 'scripts/build-arm-driver.py', component],
                    cwd=work, env=env)
            run([sys.executable, '-B', DEVICE / 'scripts/build-native-ufs-bindings.py'],
                cwd=work, env=env)
            archives = [work / 'build/armdt-platform/libArmDTPlatform.a',
                        work / 'build/armcpu/libArmCpu.a',
                        work / 'build/native-ufs/libNativeUfs.a']
            for archive in archives:
                if not archive.is_file():
                    raise ValueError(f'missing archive {archive}')
            captured = json.loads((work / 'reports/kernel-make-command.json').read_text())
            kernel.unlink()
            noctf = kernel.with_suffix('.unstripped.noctf')
            if noctf.exists():
                noctf.unlink()
            force = ' '.join(f'-Wl,-force_load,{archive}' for archive in archives)
            dependencies = ' '.join(str(archive) for archive in archives)
            relink = captured + [f'LD_KERNEL_ARCHIVES={force}',
                                 f'XNU_EXTERNAL_KERNEL_ARCHIVES={dependencies}']
            relink_env = dict(env)
            relink_env['KERNEL_SDKROOT'] = str(work / '.cache/fakeroot')
            relink_env['CPATH'] = str(work / '.cache/fakeroot/usr/local/include') + os.pathsep + env.get('CPATH', '')
            run(relink, cwd=work, env=relink_env)
            magic, cpu, subtype, filetype = struct.unpack('<IIII', kernel.read_bytes()[:16])
            if (magic, cpu, subtype, filetype) != (0xfeedfacf, 0x100000c, 0, 2):
                raise ValueError('Relinked kernel is not an ARM64 MH_EXECUTE')
            summary['ufs_archives'] = [str(archive.relative_to(work)) for archive in archives]
            summary['kernel_sha256'] = sha(kernel)
        run([sys.executable, '-B', DEVICE / 'scripts/ci/audit-driver-archives.py',
             '--work', work, '--base', base])
        summary['driver_archive_audit'] = 'passed; compile and symbol closure only'
        # Reuse this exact build's argv and real archives. Runtime feature flags,
        # crypto/pthread providers and the startup timer branch remain unchanged.
        summary['baseline_kernel_sha256'] = sha(kernel)
        run([sys.executable, '-B', DEVICE / 'scripts/ci/platform-link-audit.py',
             '--work', work])
        summary['platform_link_audit'] = 'passed; ArmCpu + ArmDTPlatform linked, no hardware execution'
        summary['kernel_sha256'] = sha(kernel)
    finally:
        source = work / '.cache/xnu-work'
        if source.is_dir():
            summary['source_status'] = subprocess.run(
                ['git', '-C', str(source), 'status', '--short'], text=True,
                capture_output=True, check=False).stdout
            original_rpc = work / 'refer/xnu/iokit/DriverKit/IORPC.h'
            built_rpc = source / 'iokit/DriverKit/IORPC.h'
            if original_rpc.exists() and built_rpc.exists():
                summary['iorpc_header_unchanged'] = sha(original_rpc) == sha(built_rpc)
            generated = list((work / 'build').rglob('*.iig.cpp'))
            summary['iig_generated_files'] = len(generated)
            summary['iig_kernel_content_references'] = [str(p.relative_to(work)) for p in generated
                if 'rpc.kernelContent' in p.read_text(errors='replace') or '.kernelContent =' in p.read_text(errors='replace')]
        summary['object_count'] = len(list((work / 'build').rglob('*.o')))
        artifact = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped'
        summary['linked_kernel_present'] = artifact.is_file()
        if artifact.is_file():
            summary['linked_kernel_bytes'] = artifact.stat().st_size
            summary['linked_kernel_sha256'] = sha(artifact)
        target_objects = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
        summary['xnu_target_object_count'] = len(list(target_objects.rglob('*.o')))
        records = list(target_objects.rglob('*.o.json')) + list(target_objects.rglob('*.cpo.json'))
        summary['compile_record_count'] = len(records)
        summary['required_compile_records'] = {}
        for name in ('startup.o.json', 'kern_exec.o.json', 'IOPlatformExpert.cpo.json', 'prng_random.o.json'):
            matches = list(target_objects.rglob(name))
            summary['required_compile_records'][name] = [
                {'path': str(path.relative_to(work)), 'sha256': sha(path)} for path in matches]
            for path in matches:
                target = work / 'reports/compile-records' / path.relative_to(target_objects)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        summary['key_objects'] = {name: [
            {'path': str(path.relative_to(work)), 'bytes': path.stat().st_size, 'sha256': sha(path)}
            for path in sorted(target_objects.rglob(name))]
            for name in ('ast.o', 'processor.o')}
        summary['iig_adaptations'] = [json.loads(p.read_text()) for p in
                                      sorted((work / 'build').rglob('*.darwin22.json'))]
        # Preserve unrecognized templates on failure; generated source stays in
        # cloud artifacts/ignored local out/, never in the tracked device tree.
        for path in (work / 'build').rglob('*.iig.cpp'):
            if 'kernelContent' in path.read_text(errors='replace'):
                target = work / 'reports/iig-failures' / path.relative_to(work / 'build')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        report.write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
