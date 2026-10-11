#!/bin/sh
set -eu

REPOSITORY="ggujjbkbuh-a11y/tachyon-core"
BRANCH="main"
PACKAGE_ROOT="openwrt/addon/packages"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)

fail() {
	echo "tachyon-addon: $*" >&2
	exit 1
}

[ "$(id -u)" = "0" ] || fail "run this installer as root"
command -v opkg >/dev/null 2>&1 || fail "opkg is required"
command -v wget >/dev/null 2>&1 || fail "wget is required"

ARCH=$(opkg print-architecture | awk '
	$1 == "arch" && $2 != "all" && ($3 + 0) > best {
		best = $3 + 0
		selected = $2
	}
	END { print selected }
')
[ -n "$ARCH" ] || fail "could not detect the OpenWrt package architecture"

case "$ARCH" in
	aarch64_cortex-a53) ;;
	*) fail "no addon packages are published for $ARCH yet" ;;
esac

LOCAL_PACKAGE_DIR="$SCRIPT_DIR/packages/$ARCH"
WORK_DIR=$(mktemp -d /tmp/tachyon-addon.XXXXXX)
trap 'rm -rf "$WORK_DIR"' EXIT

if [ -d "$LOCAL_PACKAGE_DIR" ] && [ -f "$LOCAL_PACKAGE_DIR/SHA256SUMS" ]; then
	PACKAGE_DIR="$LOCAL_PACKAGE_DIR"
else
	PACKAGE_DIR="$WORK_DIR/packages"
	mkdir -p "$PACKAGE_DIR"
	BASE_URL="https://raw.githubusercontent.com/$REPOSITORY/$BRANCH/$PACKAGE_ROOT/$ARCH"
	wget -qO "$PACKAGE_DIR/SHA256SUMS" "$BASE_URL/SHA256SUMS" ||
		fail "could not download the package manifest from GitHub"
	while read -r checksum package; do
		[ -n "$checksum" ] || continue
		package=${package#\*}
		case "$package" in
			*.ipk) ;;
			*) fail "invalid package name in checksum manifest" ;;
		esac
		wget -qO "$PACKAGE_DIR/$package" "$BASE_URL/$package" ||
			fail "could not download $package"
	done < "$PACKAGE_DIR/SHA256SUMS"
fi

if ! command -v sha256sum >/dev/null 2>&1; then
	opkg update || fail "opkg update failed while preparing checksum verification"
	opkg install coreutils-sha256sum || fail "could not install sha256sum"
fi
(
	cd "$PACKAGE_DIR"
	sha256sum -c SHA256SUMS
) || fail "package checksum verification failed"

CORE_PACKAGE=$(awk '
	$2 ~ /^tachyon-core_[^/]+_aarch64_cortex-a53\.ipk$/ {
		count++
		package = $2
	}
	END {
		if (count != 1)
			exit 1
		print package
	}
' "$PACKAGE_DIR/SHA256SUMS") || fail "expected exactly one tachyon-core package in SHA256SUMS"

manifest_package() {
	awk -v pattern="$1" '
		$2 ~ pattern {
			count++
			package = $2
		}
		END {
			if (count != 1)
				exit 1
			print package
		}
	' "$PACKAGE_DIR/SHA256SUMS"
}

TACHYON_PACKAGE=$(manifest_package '^tachyon_[0-9]+\.[0-9]+\.[0-9]+(-[0-9]+)?\.ipk$') ||
	fail "expected exactly one Tachyon package in SHA256SUMS"
LUCI_PACKAGE=$(manifest_package '^luci-app-tachyon_[0-9]+\.[0-9]+\.[0-9]+(-[0-9]+)?\.ipk$') ||
	fail "expected exactly one LuCI package in SHA256SUMS"
LUCI_I18N_PACKAGE=$(manifest_package '^luci-i18n-tachyon-ru_[0-9]+\.[0-9]+\.[0-9]+(-[0-9]+)?\.ipk$') ||
	fail "expected exactly one Russian LuCI translation package in SHA256SUMS"

for package in luci-app-passwall luci-app-passwall2; do
	if opkg status "$package" 2>/dev/null | grep -q '^Status:.*installed'; then
		fail "$package conflicts with Tachyon; remove it before installing the addon"
	fi
done

if opkg status https-dns-proxy 2>/dev/null | grep -q '^Status:.*installed'; then
	echo "Removing conflicting https-dns-proxy (its UCI configuration will be retained)"
	opkg remove https-dns-proxy || fail "could not remove https-dns-proxy"
fi

waited=0
while [ -e /var/lock/opkg.lock ]; do
	[ "$waited" -lt 120 ] || fail "timed out waiting for another opkg operation"
	echo "Waiting for another opkg operation to finish..."
	sleep 2
	waited=$((waited + 1))
done

echo "Installing Tachyon Core, Tachyon, and LuCI for $ARCH..."
opkg install --force-reinstall \
	"$PACKAGE_DIR/$CORE_PACKAGE" \
	"$PACKAGE_DIR/$TACHYON_PACKAGE" \
	"$PACKAGE_DIR/$LUCI_PACKAGE" \
	"$PACKAGE_DIR/$LUCI_I18N_PACKAGE" ||
	fail "package installation failed; see opkg output above"

[ -x /etc/init.d/tachyon ] || fail "Tachyon init script was not installed"

# Tachyon owns the generated sing-box configuration and process. Do not also
# start the standalone Core init service against the same configuration.
if [ -x /etc/init.d/tachyon-core ]; then
	/etc/init.d/tachyon-core stop >/dev/null 2>&1 || true
	/etc/init.d/tachyon-core disable >/dev/null 2>&1 || true
fi
if [ -x /etc/init.d/sing-box ]; then
	/etc/init.d/sing-box stop >/dev/null 2>&1 || true
	/etc/init.d/sing-box disable >/dev/null 2>&1 || true
fi

mkdir -p /etc/tachyon
printf '%s\n' tachyon-core > /etc/tachyon/sing-box-variant.tmp
mv /etc/tachyon/sing-box-variant.tmp /etc/tachyon/sing-box-variant

/etc/init.d/tachyon enable || fail "could not enable the Tachyon service"
/etc/init.d/tachyon restart || fail "Tachyon failed to start; inspect logread for details"

if ! /usr/bin/sing-box version; then
	fail "Tachyon is running, but the sing-box-compatible Core command failed"
fi

echo "Tachyon addon installation completed."
echo "Open LuCI and navigate to Services > Tachyon."
