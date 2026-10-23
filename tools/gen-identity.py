#!/usr/bin/env python3
"""Emit scripts/luming_identity.py from the decoded Apple iPhone X DeviceTree.

Input is the read-only research artifact produced by ref-scout:

    refs/ios16-ipsw/d22ap-product.json

which is a structured decode of Apple's own `DeviceTree.d22ap.im4p` from the
official iPhone10,3 iOS 16.7.16 (20H392) restore image.  This generator exists
so the identity block the ADT tool embeds is reproducible and auditable instead
of typed by hand.

Usage:
    python tools/gen-identity.py --product-json ../../refs/ios16-ipsw/d22ap-product.json

The output is a plain Python data module (no dependencies).  The ADT tool only
imports a sibling module, so the generated file is committed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

SOURCE = """\"\"\"Identity data decoded from the Apple iPhone X (D22AP) DeviceTree.

Provenance (read-only research input, not device measurements):
  source          refs/ios16-ipsw/Firmware/all_flash/DeviceTree.d22ap.im4p
  im4p sha256     480e8b0888949b4128b7aa2cd254497581c69e1bafa596a760b2fa955a8a6368
  decoder         refs/UniXNU-guacamole/scripts/adt.py (IM4P unwrap + Apple ADT parse)
  structured      refs/ios16-ipsw/d22ap-product.json
  target           iPhone10,3 / iOS 16.7.16 / 20H392 / d22ap

make-luming-adt.py emits these as raw Apple property bytes so the identity is
byte-exact.  Two deliberate omissions:
  * the 20-byte per-chip ``product-id`` (a donor handset identifier) is not copied;
  * properties iBoot fills from the fused syscfg on real hardware are not reused.

Do not edit by hand: run ``python tools/gen-identity.py`` instead.
\"\"\"

"""

# /buttons properties the early boot path and MobileGestalt read.  The real
# tree has 20 entries; the priority/delay/function-button/interrupt entries are
# iBoot plumbing and reference phandles this tree does not define.
BUTTON_KEEP = ('button-names', 'home-button-type', 'opposed-power-vol-buttons')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-json', type=Path, required=True,
                        help='the decoded d22ap-product.json from the reference tree')
    parser.add_argument('--output', type=Path, default=HERE / 'scripts/luming_identity.py')
    args = parser.parse_args()

    data = json.loads(args.product_json.read_text(encoding='utf-8'))

    product = [entry for entry in data['product'] if entry['name'] != 'product-id']
    if len(data['product']) - len(product) != 1:
        raise SystemExit('expected exactly one product-id entry to omit')
    buttons = {}
    for entry in data['buttons']:
        buttons.setdefault(entry['name'], entry)
    missing = [name for name in BUTTON_KEEP if name not in buttons]
    if missing:
        raise SystemExit(f'real tree is missing expected /buttons properties: {missing}')

    lines = [SOURCE.rstrip('\n') + '\n', '']
    lines.append('# /product properties of the real D22AP tree, in tree order.')
    lines.append('# The real tree has 61; product-id is omitted (per-unit identifier).')
    lines.append('PRODUCT_PROPS = [')
    for entry in product:
        lines.append(f"    ({entry['name']!r}, bytes.fromhex({entry['hex']!r})),")
    lines.append(']')
    lines.append('')
    lines.append('# /buttons properties the early boot path and MobileGestalt read.')
    lines.append('# The real tree has 20 entries; the priority/delay/function-button and')
    lines.append('# interrupt ones are iBoot plumbing and reference phandles we do not define.')
    lines.append('BUTTON_PROPS = [')
    for name in BUTTON_KEEP:
        entry = buttons[name]
        lines.append(f"    ({name!r}, bytes.fromhex({entry['hex']!r})),")
    lines.append(']')
    lines.append('')
    lines.append('# Root identity the userland must see. Root compatible strings stay the')
    lines.append("# board's own (docs/guide/mobilegestalt.md: \"Leave the root compatible")
    lines.append('# strings as the board\'s own"); the port must not match an original Apple')
    lines.append('# platform kext.  model/target-type are the documented iOS 16 identity and')
    lines.append('# match the real D22AP tree; target-sub-type is its extra Apple string.')
    lines.append('ROOT_IDENTITY = [')
    lines.append("    ('model', b'iPhone10,3\\0'),")
    lines.append("    ('target-type', b'D22\\0'),")
    lines.append("    ('target-sub-type', b'D22AP\\0'),")
    lines.append(']')
    lines.append('')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(lines), encoding='utf-8')
    print(f'wrote {args.output}: {len(product)} /product properties, '
          f'{len(BUTTON_KEEP)} /buttons properties')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
