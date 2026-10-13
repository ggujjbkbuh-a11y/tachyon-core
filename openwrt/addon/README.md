# Tachyon Core Fork: OpenWrt Packages

This repository is a fork of [Dushnilin/tachyon-core](https://github.com/Dushnilin/tachyon-core).
This package set bundles the Tachyon management service, its LuCI interface,
and `tachyon-core` as the managed sing-box-compatible engine. It also includes
the fork's NaiveProxy support. The existing `/etc/config/tachyon` configuration
is preserved during installation.

## Supported device

The checked-in package set currently targets OpenWrt devices with package
architecture `aarch64_cortex-a53`.

## Install from the router

Run as `root` over SSH:

```sh
wget -O /tmp/tachyon-addon-install.sh \
  https://raw.githubusercontent.com/ggujjbkbuh-a11y/tachyon-core/main/openwrt/addon/install.sh
sh /tmp/tachyon-addon-install.sh
```

The installer verifies package SHA-256 checksums, installs the Core, Tachyon,
and LuCI packages, marks Tachyon Core as the active engine, disables the
standalone Core service to prevent competing processes, and restarts Tachyon.
If `https-dns-proxy` is installed, the installer removes that conflicting
package while retaining its UCI configuration. It does not remove Passwall
packages; those must be removed manually if present.

The installer does not overwrite the saved Tachyon configuration. LuCI is
available under **Services → Tachyon** after installation.

## Published package set

Packages and their checksums are in `packages/aarch64_cortex-a53/`:

- `tachyon-core_0.0.7-1_aarch64_cortex-a53.ipk`
- `tachyon_1.5.12.ipk`
- `luci-app-tachyon_1.5.12.ipk`
- `luci-i18n-tachyon-ru_1.5.12.ipk`

The LuCI package has normalized directory permissions (`0755`) to avoid
restricting access to `/etc` and disrupting system services such as `ubusd`.

The NaiveProxy backend parser change and its tests are preserved in the
compressed patch `patches/tachyon-naiveproxy.patch.gz`. The LuCI package already
includes `naive+https://` URL validation and protocol display.
The Tachyon release updater reapplies the compressed package overlays
`patches/tachyon-naiveproxy-package.patch.gz` and
`patches/luci-naiveproxy-package.patch.gz` to upstream IPKs. It stops for review
if upstream changes make either patch fail to apply cleanly.

## Updating from upstream

The `Check tachyon-core upstream release` and `Check Tachyon upstream release`
workflows poll their respective latest stable releases daily. The Core workflow
verifies the official ARM64 binary checksum, merges the tagged upstream history
into the fork, builds the static OpenWrt binary, runs the Clash API tests, and
opens a pull request for review. The Tachyon workflow verifies the release
checksums for Tachyon and LuCI IPKs, reapplies the fork's NaiveProxy changes with
strict patches, and opens a separate pull request. A patch conflict or failed
verification stops the update. Neither workflow merges a PR or publishes a
package release automatically. See
[`scripts/README.md`](scripts/README.md) for package maintenance details.

These workflows update the repository and open PRs; they do not install
packages on routers. After reviewing and merging an update PR, rerun the install
command above on each router to fetch and install the new package set.
