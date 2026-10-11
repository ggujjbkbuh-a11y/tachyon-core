# OpenWrt package maintenance

`repackage_core.py` replaces the static ARM64 Core binary in an existing
`tachyon-core` IPK, updates its OpenWrt version metadata, and validates the
resulting IPK. The upstream update workflow merges the tagged upstream release
into the fork, preserving fork-specific changes such as the NaiveProxy Clash
API, then builds from the merged source. It also verifies the official upstream
ARM64 binary against the release checksum manifest as a release integrity
check.

The `Check tachyon-core upstream release` workflow polls the latest stable Core
release daily and can also be run manually from GitHub Actions. It prepares a
pull request rather than merging or publishing packages automatically. It syncs
the tagged upstream history into the fork, then runs the fork's parser tests and
builds for `aarch64-unknown-linux-musl`. Merge conflicts or failed tests stop
the update and require manual attention. The build job has read-only repository
permissions; a separate job only repackages the verified build artifact and
opens the pull request.

The separate `Check Tachyon upstream release` workflow polls
[`Dushnilin/tachyon`](https://github.com/Dushnilin/tachyon) daily. It verifies
the upstream release checksums for the Tachyon, LuCI, and Russian translation
IPKs, then reapplies the fork's NaiveProxy changes from
`openwrt/addon/patches/` with zero patch fuzz. It validates package metadata and
checks for NaiveProxy parsing and LuCI display markers before proposing the
updated packages in a pull request. If upstream changes conflict with a patch,
the update stops for manual review. The updater does not merge PRs or publish
releases automatically.

For same-version upstream asset refreshes, the workflow compares the official
package hashes with `openwrt/addon/packages/UPSTREAM_TACHYON_SHA256SUMS`. If the
upstream packages changed without a semver change, it increments the OpenWrt
package release (for example, `1.5.5` to `1.5.5-1`) so `opkg` recognizes an
upgrade.

Neither workflow installs updates on routers. After reviewing and merging its
PR, rerun `openwrt/addon/install.sh` on each router to install the updated
packages.

If the current package has the same upstream version but is missing the fork's
parser, the package release number is incremented and a repair update is
proposed. For example, `0.0.6-1` becomes `0.0.6-2`.
