#!/bin/sh
set -eu

VERSION="1.2.0"
UV_VERSION="0.11.28"
NODE_VERSION="22.23.1"
UV_DARWIN_SHA256="33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232"
UV_LINUX_SHA256="e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224"
NODE_DARWIN_SHA256="fb526811860f81dcac7dd8b2b55eca4accfc5d61c3b7c2508f2639faee8a738d"
NODE_LINUX_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
ASSET_BASE="https://github.com/JAGGER-L/awesome_agent/releases/latest/download"

fail() {
    echo "awesome install: $*" >&2
    exit 1
}

sha256_file() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

[ "$#" -eq 0 ] || fail "this installer accepts no options"
command -v curl >/dev/null 2>&1 || fail "curl is required"

SYSTEM=$(uname -s)
MACHINE=$(uname -m)
case "$SYSTEM:$MACHINE" in
    Darwin:arm64)
        UV_PLATFORM="aarch64-apple-darwin"
        UV_SHA256="$UV_DARWIN_SHA256"
        NODE_PLATFORM="darwin-arm64"
        NODE_SHA256="$NODE_DARWIN_SHA256"
        PROFILE="$HOME/.zprofile"
        ;;
    Linux:x86_64)
        KERNEL_RELEASE=$(cat /proc/sys/kernel/osrelease 2>/dev/null || true)
        case $(printf '%s' "$KERNEL_RELEASE" | tr '[:upper:]' '[:lower:]') in
            *microsoft*wsl2*) ;;
            *) fail "only WSL2 Ubuntu 24.04 x64 is supported" ;;
        esac
        [ -r /etc/os-release ] || fail "cannot identify the WSL distribution"
        # shellcheck disable=SC1091
        . /etc/os-release
        [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] ||
            fail "only WSL2 Ubuntu 24.04 x64 is supported"
        UV_PLATFORM="x86_64-unknown-linux-gnu"
        UV_SHA256="$UV_LINUX_SHA256"
        NODE_PLATFORM="linux-x64"
        NODE_SHA256="$NODE_LINUX_SHA256"
        PROFILE="$HOME/.profile"
        ;;
    *)
        fail "supported hosts are Apple Silicon macOS and WSL2 Ubuntu 24.04 x64"
        ;;
esac

INSTALL_ROOT="$HOME/.local/share/awesome"
LAUNCHER_DIR="$HOME/.local/bin"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/awesome-install.XXXXXX")
cleanup() {
    rm -rf "$STAGE"
}
trap cleanup EXIT

UV_DIR="$STAGE/uv"
STAGED_APP="$STAGE/app"
DOWNLOADS="$STAGE/downloads"
mkdir -p "$UV_DIR" "$STAGED_APP/runtimes/python" "$DOWNLOADS"

UV_ARCHIVE="uv-$UV_PLATFORM.tar.gz"
curl -fsSL \
    "https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/$UV_ARCHIVE" \
    -o "$DOWNLOADS/$UV_ARCHIVE"
[ "$(sha256_file "$DOWNLOADS/$UV_ARCHIVE")" = "$UV_SHA256" ] ||
    fail "uv checksum does not match"
tar -xzf "$DOWNLOADS/$UV_ARCHIVE" -C "$UV_DIR" --strip-components=1
UV="$UV_DIR/uv"
[ -x "$UV" ] || fail "uv bootstrap did not produce an executable"

UV_PYTHON_INSTALL_DIR="$STAGED_APP/runtimes/python" \
    "$UV" python install 3.12 --no-bin
PYTHON=$(UV_PYTHON_INSTALL_DIR="$STAGED_APP/runtimes/python" \
    "$UV" python find --managed-python 3.12)
[ -x "$PYTHON" ] || fail "private Python 3.12 was not installed"
PYTHON=$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.executable))')
case "$PYTHON" in
    "$STAGED_APP/runtimes/python"/*) ;;
    *) fail "private Python escaped the staged runtime" ;;
esac

BUNDLE="awesome-$VERSION.zip"
curl -fsSL "$ASSET_BASE/$BUNDLE" -o "$DOWNLOADS/$BUNDLE"
curl -fsSL "$ASSET_BASE/SHA256SUMS" -o "$DOWNLOADS/SHA256SUMS"
EXPECTED=$(awk -v name="$BUNDLE" '$2 == name {print $1}' "$DOWNLOADS/SHA256SUMS")
[ -n "$EXPECTED" ] || fail "release checksum is missing"
ACTUAL=$(sha256_file "$DOWNLOADS/$BUNDLE")
[ "$ACTUAL" = "$EXPECTED" ] || fail "release checksum does not match"

EXTRACTED="$STAGE/extracted"
mkdir -p "$EXTRACTED"
"$PYTHON" -m zipfile -e "$DOWNLOADS/$BUNDLE" "$EXTRACTED"
[ -d "$EXTRACTED/awesome-$VERSION" ] || fail "release bundle root is invalid"
cp -R "$EXTRACTED/awesome-$VERSION/." "$STAGED_APP/"

NODE_ARCHIVE="node-v$NODE_VERSION-$NODE_PLATFORM.tar.xz"
curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/$NODE_ARCHIVE" \
    -o "$DOWNLOADS/$NODE_ARCHIVE"
[ "$(sha256_file "$DOWNLOADS/$NODE_ARCHIVE")" = "$NODE_SHA256" ] ||
    fail "Node checksum does not match"
mkdir -p "$STAGED_APP/runtimes/node"
tar -xJf "$DOWNLOADS/$NODE_ARCHIVE" \
    -C "$STAGED_APP/runtimes/node" --strip-components=1
NODE="$STAGED_APP/runtimes/node/bin/node"
NPM_CLI="$STAGED_APP/runtimes/node/lib/node_modules/npm/bin/npm-cli.js"
[ -x "$NODE" ] && [ -f "$NPM_CLI" ] || fail "private Node runtime is incomplete"

CORE_ENV="$STAGED_APP/core/.venv"
SITE_PACKAGES="$CORE_ENV/site-packages"
CORE_BIN="$CORE_ENV/bin"
mkdir -p "$SITE_PACKAGES" "$CORE_BIN"
WHEEL="$STAGED_APP/core/awesome_agent-$VERSION-py3-none-any.whl"
REQUIREMENTS="$STAGED_APP/core/requirements.lock"
[ -f "$REQUIREMENTS" ] || fail "locked Core requirements are missing"
"$UV" pip install --python "$PYTHON" --target "$SITE_PACKAGES" \
    --require-hashes --requirement "$REQUIREMENTS"
"$UV" pip install --python "$PYTHON" --target "$SITE_PACKAGES" \
    --no-deps "${WHEEL}[memory]"
PYTHON_RELATIVE=${PYTHON#"$STAGED_APP/"}
cat >"$CORE_BIN/awesome-core" <<EOF
#!/bin/sh
APP_ROOT=\$(CDPATH= cd "\$(dirname "\$0")/../../.." && pwd)
PYTHONPATH="\$APP_ROOT/core/.venv/site-packages"
export PYTHONPATH
exec "\$APP_ROOT/$PYTHON_RELATIVE" -c \
    'import site,sys; site.addsitedir(sys.argv.pop(1)); from awesome_agent.protocol.stdio import main; main()' \
    "\$PYTHONPATH" "\$@"
EOF
chmod 755 "$CORE_BIN/awesome-core"
"$NODE" "$NPM_CLI" ci --omit=dev --ignore-scripts --prefix "$STAGED_APP/tui"

PYTHON_VERSION=$(PYTHONPATH="$SITE_PACKAGES" "$PYTHON" -c \
    'from awesome_agent.version import PRODUCT_VERSION; print(PRODUCT_VERSION)')
NODE_MAJOR=$("$NODE" -p 'process.versions.node.split(".")[0]')
CLI_VERSION=$(PATH="$CORE_BIN:$PATH" \
    "$NODE" "$STAGED_APP/tui/dist/cli/index.js" --version)
[ "$PYTHON_VERSION" = "$VERSION" ] || fail "private Core version check failed"
[ "$NODE_MAJOR" = "22" ] || fail "private Node version check failed"
[ "$CLI_VERSION" = "$VERSION" ] || fail "public CLI version check failed"
echo "validated"

mkdir -p "$INSTALL_ROOT"
rm -rf "$INSTALL_ROOT/app"
mv "$STAGED_APP" "$INSTALL_ROOT/app"

mkdir -p "$LAUNCHER_DIR"
cat >"$LAUNCHER_DIR/awesome" <<EOF
#!/bin/sh
APP_ROOT='$INSTALL_ROOT/app'
PATH="\$APP_ROOT/core/.venv/bin:\$PATH"
export PATH
exec "\$APP_ROOT/runtimes/node/bin/node" "\$APP_ROOT/tui/dist/cli/index.js" "\$@"
EOF
chmod 755 "$LAUNCHER_DIR/awesome"

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1; then
    printf '\n%s\n' "$PATH_LINE" >>"$PROFILE"
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Git is not installed. Install it from https://git-scm.com/downloads"
fi
echo "Awesome $VERSION installed. Open a new terminal and run: awesome"
echo "Close every existing AWESOME session before rerunning this installer."
