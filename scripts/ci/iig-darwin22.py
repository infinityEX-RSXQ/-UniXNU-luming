"""Adapt pinned IIG's split-content templates to Darwin22 inline RPC messages.

This is a source-generation compatibility candidate, not an ABI certification.
Only recognized leaf KERNEL conditionals select the generator's inline branch.
KERNEL stays defined; all message fields and descriptor layouts stay unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BANNER = 'iig(DriverKit-427.100.5 Apr 30 2025 01:57:02)'
DIRECTIVE = re.compile(r'^\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b(.*)')


def adapt(text):
    if BANNER not in text.split('\n', 1)[0]:
        raise ValueError('Unreviewed IIG version; inspect templates before adapting')
    lines = text.splitlines(keepends=True)
    stack, blocks = [], []
    for index, line in enumerate(lines):
        match = DIRECTIVE.match(line)
        if not match:
            continue
        directive, argument = match.groups()
        if directive in ('if', 'ifdef', 'ifndef'):
            if stack:
                stack[-1]['nested'] = True
            stack.append(dict(start=index, condition=(directive, argument.strip()),
                              alternate=None, nested=False, elif_seen=False))
        elif directive in ('else', 'elif'):
            if not stack:
                raise ValueError('Unbalanced IIG conditional')
            stack[-1]['alternate'] = index
            stack[-1]['elif_seen'] |= directive == 'elif'
        else:
            if not stack:
                raise ValueError('Unbalanced IIG conditional')
            block = stack.pop()
            block['end'] = index
            blocks.append(block)
    if stack:
        raise ValueError('Unclosed IIG conditional')
    edits = []
    for block in blocks:
        if block['nested'] or block['condition'] not in (('ifdef', 'KERNEL'), ('if', 'KERNEL')):
            continue
        start, end, alternate = block['start'], block['end'], block['alternate']
        kernel = ''.join(lines[start+1:alternate if alternate is not None else end])
        inline = ''.join(lines[alternate+1:end]) if alternate is not None else ''
        split_content = 'kernelContent' in kernel or 'messageContent' in kernel
        typed_message = (re.search(r'\b\w+_Msg\s*\*\s*message;', kernel) and
                         re.search(r'\b\w+_Msg_With_Content\s*\*\s*message;', inline))
        if split_content or typed_message:
            if block['elif_seen']:
                raise ValueError('Unreviewed IIG RPC #elif')
            if alternate is None:
                declaration = re.fullmatch(r'\s*const struct \w+_Msg_Content \* kernelContent;\s*', kernel)
                callback = re.fullmatch(r'\s*rpc\.kernelContent = &msg->content\.__hdr;\s*', kernel)
                if not declaration and not callback:
                    raise ValueError('Unreviewed IIG kernel-only RPC template')
            edits.append((start, end, inline))
    for start, end, inline in sorted(edits, reverse=True):
        lines[start:end+1] = ['/* Darwin22: retain inline RPC content and four-field IORPC. */\n', inline]
    result = ''.join(lines)
    if 'kernelContent' in result or 'messageContent' in result:
        raise ValueError('Unrecognized split-content IIG template remains')
    return result, len(edits)


def main():
    real = os.environ['NUWA_REAL_IIG']
    arguments = sys.argv[1:]
    completed = subprocess.run([real, *arguments])
    if completed.returncode:
        return completed.returncode
    if '--impl' not in arguments:
        raise ValueError('Expected explicit IIG implementation output')
    path = Path(arguments[arguments.index('--impl')+1]).resolve()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    if not path.is_relative_to(temporary):
        raise ValueError('IIG adaptation only allowed inside cloud RUNNER_TEMP')
    original = path.read_text()
    adapted, count = adapt(original)
    path.write_text(adapted, newline='\n')
    record = {'implementation': str(path), 'template_blocks': count,
              'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
              'adapted_sha256': hashlib.sha256(adapted.encode()).hexdigest(),
              'remaining_split_content_references': 0, 'device_verified': False}
    path.with_suffix(path.suffix + '.darwin22.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'Darwin22 IIG: {path.name}: {count} inline RPC template blocks', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
