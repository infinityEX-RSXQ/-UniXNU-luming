"""Build three upstream platform archives using actual Darwin22 compile records.

Cloud RUNNER_TEMP only; never link them into a kernel or execute device code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# Preserve diagnostics from old upstream headers/macros without changing their
# definitions, ABI or initializer semantics. All other warnings remain errors.
HEADER_STYLE_WARNINGS = ('missing-designated-field-initializers',
                         'suggest-override', 'suggest-destructor-override')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_record(path, work):
    record = json.loads(path.read_text().strip().removesuffix(','))
    directory = Path(record['directory']).resolve()
    source = (directory / record['file']).resolve()
    arguments = record['arguments']
    if not directory.is_relative_to(work) or not source.is_relative_to(work):
        raise ValueError('Compile record must refer to this temporary build')
    if not source.is_file() or '-c' not in arguments or any(
            flag in arguments for flag in ('-E', '-S', '-fsyntax-only')):
        raise ValueError('Need an actual object compilation record')
    if not Path(arguments[0]).is_file():
        raise ValueError('Recorded compiler is unavailable')
    output = (directory / arguments[arguments.index('-o') + 1]).resolve()
    if not output.is_relative_to(work) or not output.is_file():
        raise ValueError('Recorded object was not produced in this build')
    return record, output


def driver_command(record, source, destination, work, removed_includes=None):
    """Keep ABI flags; replace source/output and remove dependency side effects."""
    command = []
    replaced = 0
    args = record['arguments']
    i = 0
    directory = Path(record['directory'])
    original = (directory / record['file']).resolve()
    while i < len(args):
        value = args[i]
        if value == '-I' and i + 1 < len(args):
            include = (directory / args[i + 1]).resolve()
            legacy = (work / 'build/xnu/ios-obj/EXPORT_HDRS/libsa').resolve()
            if include == legacy and not include.is_dir():
                if removed_includes is not None:
                    removed_includes.append(str(include))
                i += 2
                continue
        if value in ('-o', '-MF', '-MT', '-MQ', '-MJ'):
            if i + 1 == len(args):
                raise ValueError(f'Missing argument after {value}')
            if value == '-o':
                command += ['-o', str(destination)]
            i += 2
            continue
        if value in ('-MD', '-MMD', '-MP'):
            i += 1
            continue
        if not value.startswith('-') and (directory / value).resolve() == original:
            command.append(str(source))
            replaced += 1
        else:
            command.append(value)
        i += 1
    if replaced != 1 or command.count('-o') != 1:
        raise ValueError('Ambiguous original source or object output')
    command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                '-I', str(work / '.cache/xnu-work/osfmk')]
    command += ['-Wno-error=' + warning for warning in HEADER_STYLE_WARNINGS]
    return command


def inspect_object(path):
    data = path.read_bytes()
    if len(data) < 32 or struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise ValueError('Expected ordinary ARM64 MH_OBJECT')
    count, size = struct.unpack_from('<2I', data, 16)
    end = 32 + size
    if end > len(data):
        raise ValueError('Truncated Mach-O commands')
    at = 32
    versions = []
    for _ in range(count):
        if at + 8 > end:
            raise ValueError('Truncated load command')
        command, length = struct.unpack_from('<2I', data, at)
        if length < 8 or length % 8 or at + length > end:
            raise ValueError('Invalid load command')
        if command == 0x32:
            if length < 24:
                raise ValueError('Truncated build version')
            versions.append(struct.unpack_from('<3I', data, at + 8))
        at += length
    if at != end or len(versions) != 1 or versions[0][:2] != (2, 0x100000):
        raise ValueError(f'Expected iOS16.0 object build version: {versions}')
    return {'bytes': len(data), 'sha256': sha(path), 'format': 'ordinary ARM64 MH_OBJECT',
            'platform': versions[0][0], 'minimum_os_raw': versions[0][1],
            'sdk_metadata_raw': versions[0][2]}


def names(path, mode):
    command = ['xcrun', 'nm', '-g', mode, '-j', str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return {line.strip() for line in result.stdout.splitlines() if line.startswith('_')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--base', type=Path, required=True)
    args = parser.parse_args()
    work, base = args.work.resolve(), args.base.resolve()
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('Archive build requires macOS Actions')
    if not work.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('Only the cloud temporary build is writable')
    target = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    out = work / 'build/driver-archives'
    report_dir = work / 'reports/driver-archives'
    out.mkdir(exist_ok=False)
    report_dir.mkdir(exist_ok=False)
    summary = {'scope': 'compile/archive and global symbol-name closure only; no kernel relink',
               'components': [], 'passed': False, 'device_verified': False,
               'hardware_execution': False,
               'visible_header_style_warning_exceptions': list(HEADER_STYLE_WARNINGS)}
    try:
        records = {}
        for name in ('startup.o.json', 'kern_exec.o.json', 'IOPlatformExpert.cpo.json', 'prng_random.o.json'):
            matches = list(target.rglob(name))
            if len(matches) != 1:
                raise ValueError(f'Expected exactly one compile record: {name}')
            record, obj = load_record(matches[0], work)
            records[name] = {'record': record, 'record_sha256': sha(matches[0]),
                             'original_object': inspect_object(obj)}
        summary['compile_inputs'] = records
        record = records['IOPlatformExpert.cpo.json']['record']
        kernel = target / 'mach.development.bcm2837.unstripped'
        kernel_names = names(kernel, '-U')
        summary['kernel_sha256'] = sha(kernel)
        definitions_by_component = {}
        imports_by_component = {}
        for component in ('ArmCpu', 'ArmDTPlatform', 'ArmVirtPlatform'):
            source = base / 'drivers' / component / (component + '.cpp')
            obj = out / (component + '.o')
            archive = out / ('lib' + component + '.a')
            removed_includes = []
            command = driver_command(record, source, obj, work, removed_includes)
            declaration_headers = {'ArmCpu': 'ArmCpuExports.h',
                                   'ArmDTPlatform': 'ArmDTPlatformDeclarations.h'}
            forced_header = (ROOT / 'include/nuwa' / declaration_headers[component]
                             if component in declaration_headers else None)
            if forced_header:
                command += ['-include', str(forced_header)]
            item = {'component': component, 'source': str(source), 'source_sha256': sha(source),
                    'arguments': command, 'directory': record['directory'], 'compiled': False,
                    'omitted_missing_include_directories': removed_includes,
                    'declaration_header_sha256': sha(forced_header) if forced_header else None}
            summary['components'].append(item)
            print('+', ' '.join(command), flush=True)
            result = subprocess.run(command, cwd=record['directory'], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (report_dir / (component + '.log')).write_text(result.stdout)
            print(result.stdout, flush=True)
            item['compiler_exit_code'] = result.returncode
            if result.returncode:
                raise RuntimeError(f'{component} compile failed; see its actual compiler log')
            item['object'] = inspect_object(obj)
            item['compiled'] = True
            subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj)], check=True)
            if archive.read_bytes()[:8] != b'!<arch>\n':
                raise ValueError('Archive output is not an ar container')
            members = subprocess.check_output(['xcrun', 'ar', 't', str(archive)], text=True).splitlines()
            index_members = {'__.SYMDEF', '__.SYMDEF SORTED', '__.SYMDEF_64', '__.SYMDEF_64 SORTED'}
            if [name for name in members if name not in index_members] != [obj.name]:
                raise ValueError(f'Unexpected archive members: {members}')
            item['archive'] = {'path': str(archive.relative_to(work)), 'bytes': archive.stat().st_size,
                               'sha256': sha(archive), 'members': members}
            definitions_by_component[component] = names(obj, '-U')
            imports_by_component[component] = names(obj, '-u')
            item['defined_global_names'] = sorted(definitions_by_component[component])
            item['imported_global_names'] = sorted(imports_by_component[component])
        all_definitions = set().union(*definitions_by_component.values())
        for item in summary['components']:
            component = item['component']
            item['kernel_name_collisions'] = sorted(definitions_by_component[component] & kernel_names)
            imports = imports_by_component[component] - all_definitions - kernel_names
            # Mach-O static executable link synthesizes this identity; not a driver stub.
            item['linker_synthesized_imports'] = sorted(imports & {'___dso_handle'})
            item['missing_global_names'] = sorted(imports - {'___dso_handle'})
        if any(item['kernel_name_collisions'] or item['missing_global_names'] for item in summary['components']):
            raise ValueError('Platform archive global symbol closure failed')
        summary['passed'] = True
    finally:
        (report_dir / 'audit.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Platform archives: actual ARM64/iOS16 compilation, ar members and symbol-name closure passed', flush=True)


if __name__ == '__main__':
    main()
