# OpenWrt package maintenance

`repackage_core.py` replaces the static ARM64 Core binary in an existing
`tachyon-core` IPK, updates its OpenWrt version metadata, and validates the
resulting IPK. The upstream update workflow merges the tagged upstream release
into the fork, preserving fork-specific changes such as the NaiveProxy Clash
API, then builds from the merged source. It also verifies the official upstream
ARM64 binary against the release checksum manifest as a release integrity
check.

The updater polls the latest stable upstream release daily and can also be run
manually from GitHub Actions. It prepares a pull request rather than merging or
publishing packages automatically. It syncs the tagged upstream history into
the fork, then runs the fork's parser tests and builds for
`aarch64-unknown-linux-musl`. Merge conflicts or failed tests stop the update
and require manual attention. The build job has read-only repository
permissions; a separate job only repackages the verified build artifact and
opens the pull request. This keeps upstream updates reviewable and prevents
untrusted build code from receiving write permissions.

If the current package has the same upstream version but is missing the fork's
parser, the package release number is incremented and a repair update is
proposed. For example, `0.0.6-1` becomes `0.0.6-2`.
