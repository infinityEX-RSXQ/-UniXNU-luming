"""Identity data decoded from the Apple iPhone X (D22AP) DeviceTree.

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
"""


# /product properties of the real D22AP tree, in tree order.
# The real tree has 61; product-id is omitted (per-unit identifier).
PRODUCT_PROPS = [
    ('sandman-support', bytes.fromhex('01000000')),
    ('display-mirroring', bytes.fromhex('01000000')),
    ('car-integration', bytes.fromhex('01000000')),
    ('assistant', bytes.fromhex('01000000')),
    ('lockdown-certtype', bytes.fromhex('01000000')),
    ('ui-pip', bytes.fromhex('01000000')),
    ('wifi-chipset', bytes.fromhex('3433353700')),
    ('product-name', bytes.fromhex('6950686f6e65205800')),
    ('hearingaid-audio-equalization', bytes.fromhex('01000000')),
    ('RF-exposure-separation-distance', bytes.fromhex('05000000')),
    ('mobiledevice-min-ver', bytes.fromhex('3134303000')),
    ('gps-capable', bytes.fromhex('01000000')),
    ('product-description', bytes.fromhex('6950686f6e65205800')),
    ('allow-32bit-apps', bytes.fromhex('01000000')),
    ('low-power-wallet-mode', bytes.fromhex('01000000')),
    ('builtin-mics', bytes.fromhex('04000000')),
    ('has-boot-chime', bytes.fromhex('00000000')),
    ('supports-burnin-mitigation', bytes.fromhex('01000000')),
    ('compatible-device-fallback', bytes.fromhex('6950686f6e65392c3200')),
    ('device-perf-memory-class', bytes.fromhex('04000000')),
    ('artwork-scale-factor', bytes.fromhex('03000000')),
    ('baseband-chipset', bytes.fromhex('6d6176313700')),
    ('hearingaid-low-energy-audio', bytes.fromhex('01000000')),
    ('ui-background-quality', bytes.fromhex('64000000')),
    ('supports-avatars', bytes.fromhex('')),
    ('public-key-accelerator', bytes.fromhex('01000000')),
    ('disable-globe-map', bytes.fromhex('')),
    ('artwork-device-idiom', bytes.fromhex('70686f6e6500')),
    ('device-color-policy', bytes.fromhex('00000000')),
    ('graphics-featureset-class', bytes.fromhex('4150504c453400')),
    ('siri-gesture', bytes.fromhex('01000000')),
    ('supports-maps-optical-heading', bytes.fromhex('')),
    ('compatible-app-variant', bytes.fromhex('3000')),
    ('artwork-dynamic-displaymode', bytes.fromhex('3000')),
    ('bluetooth-le', bytes.fromhex('01000000')),
    ('watch-companion', bytes.fromhex('01000000')),
    ('has-applelpm', bytes.fromhex('00000000')),
    ('iap2-protocol-supported', bytes.fromhex('01000000')),
    ('ephemeral-data-mode', bytes.fromhex('00000000')),
    ('activation-protocol-version', bytes.fromhex('02000000')),
    ('volume-up-button-location', bytes.fromhex('78b00900ff96140030e00300ca9d010010270000')),
    ('ptp-large-files', bytes.fromhex('01000000')),
    ('udid-version', bytes.fromhex('01000000')),
    ('offline-dictation', bytes.fromhex('01000000')),
    ('location-reminders', bytes.fromhex('01000000')),
    ('ui-weather-quality', bytes.fromhex('64000000')),
    ('graphics-featureset-fallbacks', bytes.fromhex('4150504c45333a4150504c453376313a4150504c45323a4150504c45313a474c4553322c3000')),
    ('dictation', bytes.fromhex('01000000')),
    ('itunes-min-ver', bytes.fromhex('03070c00')),
    ('artwork-display-gamut', bytes.fromhex('503300')),
    ('front-cam-offset-from-center', bytes.fromhex('d00101009e1d0000e803000000000000')),
    ('ui-reachability', bytes.fromhex('01000000')),
    ('volume-down-button-location', bytes.fromhex('78b00900ff96140048d00500ca9d010010270000')),
    ('personal-hotspot', bytes.fromhex('01000000')),
    ('single-stage-boot', bytes.fromhex('01000000')),
    ('mobiledevice-beta-min-ver', bytes.fromhex('313139302e3130302e3200')),
    ('oled-display', bytes.fromhex('01000000')),
    ('thin-bezel', bytes.fromhex('01000000')),
    ('artwork-device-subtype', bytes.fromhex('84090000')),
    ('side-button-location', bytes.fromhex('75f300002a0f020000710000ea3f0000e8030000')),
]

# /buttons properties the early boot path and MobileGestalt read.
# The real tree has 20 entries; the priority/delay/function-button and
# interrupt ones are iBoot plumbing and reference phandles we do not define.
BUTTON_PROPS = [
    ('button-names', bytes.fromhex('766f6c757000766f6c646f776e00686f6c640072696e676572616200')),
    ('home-button-type', bytes.fromhex('02000000')),
    ('opposed-power-vol-buttons', bytes.fromhex('01000000')),
]

# Root identity the userland must see. Root compatible strings stay the
# board's own (docs/guide/mobilegestalt.md: "Leave the root compatible
# strings as the board's own"); the port must not match an original Apple
# platform kext.  model/target-type are the documented iOS 16 identity and
# match the real D22AP tree; target-sub-type is its extra Apple string.
ROOT_IDENTITY = [
    ('model', b'iPhone10,3\0'),
    ('target-type', b'D22\0'),
    ('target-sub-type', b'D22AP\0'),
]
