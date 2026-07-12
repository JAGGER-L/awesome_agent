#!/bin/sh
set -eu

VERSION="1.0.0"
UV_VERSION="0.11.28"
NODE_VERSION="22.23.1"
ASSET_BASE="https://github.com/JAGGER-L/awesome_agent/releases/latest/download"

fail() {
    echo "awesome install: $*" >&2
    exit 1
}

[ "$#" -eq 0 ] || fail "this installer accepts no options"
command -v curl >/dev/null 2>&1 || fail "curl is required"

SYSTEM=$(uname -s)
MACHINE=$(uname -m)
case "$SYSTEM:$MACHINE" in
    Darwin:arm64)
        NODE_PLATFORM="darwin-arm64"
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
        NODE_PLATFORM="linux-x64"
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

curl -fsSL "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$STAGE/uv-install.sh"
UV_UNMANAGED_INSTALL="$UV_DIR" UV_NO_MODIFY_PATH=1 sh "$STAGE/uv-install.sh"
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
if command -v shasum >/dev/null 2>&1; then
    ACTUAL=$(shasum -a 256 "$DOWNLOADS/$BUNDLE" | awk '{print $1}')
else
    ACTUAL=$(sha256sum "$DOWNLOADS/$BUNDLE" | awk '{print $1}')
fi
[ "$ACTUAL" = "$EXPECTED" ] || fail "release checksum does not match"

EXTRACTED="$STAGE/extracted"
mkdir -p "$EXTRACTED"
"$PYTHON" -m zipfile -e "$DOWNLOADS/$BUNDLE" "$EXTRACTED"
[ -d "$EXTRACTED/awesome-$VERSION" ] || fail "release bundle root is invalid"
cp -R "$EXTRACTED/awesome-$VERSION/." "$STAGED_APP/"

NODE_ARCHIVE="node-v$NODE_VERSION-$NODE_PLATFORM.tar.xz"
curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/$NODE_ARCHIVE" \
    -o "$DOWNLOADS/$NODE_ARCHIVE"
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
