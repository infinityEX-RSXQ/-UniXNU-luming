# Device package for the Xiaomi Civi 5 Pro (luming).
#
# UniXNU maps this directory to `kits/luming` inside out/luming/tree
# (docs/porting/device-repository.md).  It is the EDK II / UniXNU kit content of
# the board, not the firmware itself: the firmware lives in the separate
# uefi-luming Mu-Silicium port.
#
# PRE-BUILD STATE - HONEST INVENTORY
#
# Present:
#   unixnu.toml            boot-manager config of the kit volume (placeholder entries)
#   LumingKit/Kit.toml     kit skeleton; intentionally injects nothing yet
#
# Missing (and not pretended to exist):
#   - EDK II DXE drivers for luming hardware (UFS, display, touch, GPU)
#   - XNU kexts / device tree overlays for the board
#   - the `build.sh`/`UniXNUPkg` package build used by the base repository, which
#     depends on the Mu-Silicium/edk2 toolchain and on the driver sources above
#
# The UniXNU firmware step passes this directory in PACKAGES_PATH as `{tree}/package`
# (see board.toml [firmware] packages), so once drivers are added they are picked up
# without changing board.toml.
