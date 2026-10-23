# UniXNU-luming patches

`unixnu-bootimg-header-v4.patch` — local integration patch for the **base** UniXNU driver
`scripts/unixnu/bootimg.py` (not a tree/XNU patch). It teaches `unixnu bootimg` to verify
Android header-v4 images: no load addresses, 4096-byte page, header_size 1584, and an empty
4096-byte boot signature after the data.

Why it is needed: the stock luming boot image is header v4 (`header_version=4`, header_size
1584), but upstream's `verify()` only understood v0–v2. This patch was validated locally with
a synthetic v4 image (pack + verify + tampered-signature rejection) at
`backup/analysis/test-bootimg-v4.py`.

Apply it to the base UniXNU checkout before running `unixnu bootimg` on Windows:

```powershell
git -C <path-to-UniXNU-base> apply <path-to-this-repo>/patches/unixnu-bootimg-header-v4.patch
```

Boundary: this only changes the host-side boot-image verifier; it does not make any image
bootable and was not tested on the phone.

## mu-silicium-bootshim-header-size.patch (also in workspace patches/)

The Mu-Silicium BootShim/BootShim.S writes only FD_SIZE into the ARM64 header image-size
field, while unixnu.bootimg.compile_shim() requires len(shim)+FD_SIZE; apply the workspace
patch to the Mu-Silicium checkout before unixnu bootimg. Host-side dummy-FD pipeline test
passes; real ABL acceptance remains unverified. See ackup/analysis/BOOTIMG-PIPELINE-TEST.md.
