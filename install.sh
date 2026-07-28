#!/bin/sh
set -eu

VERSION="1.3.1"
UV_VERSION="0.11.28"
NODE_VERSION="22.23.1"
UV_DARWIN_SHA256="33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232"
UV_LINUX_SHA256="e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224"
NODE_DARWIN_SHA256="fb526811860f81dcac7dd8b2b55eca4accfc5d61c3b7c2508f2639faee8a738d"
NODE_LINUX_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"
ASSET_BASE="https://github.com/JAGGER-L/awesome_agent/releases/download/v$VERSION"

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

# BEGIN INSTALL TRANSACTION FUNCTIONS
set_install_transaction_paths() {
    INSTALL_APP="$INSTALL_ROOT/app"
    INSTALL_ROLLBACK="$INSTALL_ROOT/app.rollback"
    INSTALL_TRANSACTION="$INSTALL_ROOT/.install-transaction"
    INSTALL_LOCK="$INSTALL_ROOT/.install.lock"
    INSTALL_LOCK_OWNER="$INSTALL_LOCK/owner"
}

_install_process_start() {
    LC_ALL=C ps -p "$1" -o lstart= 2>/dev/null |
        sed -e 's/^ *//' -e 's/  */ /g'
}

_install_directory_identity() {
    ls -di "$1" 2>/dev/null | awk '{print $1}'
}

_install_directory_mtime() {
    case $(uname -s) in
        Darwin) stat -f %m "$1" 2>/dev/null ;;
        *) stat -c %Y "$1" 2>/dev/null ;;
    esac
}

_install_owner_state() {
    owner_file=$1
    if [ ! -f "$owner_file" ] || [ -L "$owner_file" ]; then
        printf '%s\n' invalid
        return
    fi
    owner_pid=$(sed -n '1p' "$owner_file" 2>/dev/null || true)
    owner_start=$(sed -n '2p' "$owner_file" 2>/dev/null || true)
    owner_token=$(sed -n '3p' "$owner_file" 2>/dev/null || true)
    case "$owner_pid" in
        "" | *[!0-9]*)
            printf '%s\n' invalid
            return
            ;;
    esac
    if [ -z "$owner_start" ] || [ -z "$owner_token" ]; then
        printf '%s\n' invalid
        return
    fi
    current_start=$(_install_process_start "$owner_pid" || true)
    if [ "$current_start" = "$owner_start" ]; then
        printf '%s\n' active
    else
        printf '%s\n' dead
    fi
}

_install_remove_stale_reclaim_claim() {
    stale_claim=$1
    [ "$stale_claim" != "${INSTALL_RECLAIM_CLAIM:-}" ] || return 1
    [ -d "$stale_claim" ] && [ ! -L "$stale_claim" ] || return 1
    stale_identity=$(_install_directory_identity "$stale_claim")
    [ -n "$stale_identity" ] || return 1
    stale_owner="$stale_claim/owner"
    stale_state=$(_install_owner_state "$stale_owner")
    [ "$stale_state" != active ] || return 1
    if [ "$stale_state" = invalid ]; then
        stale_mtime=$(_install_directory_mtime "$stale_claim" || true)
        now=$(date +%s)
        case "$stale_mtime:$now" in
            *[!0-9:]* | :* | *:) return 1 ;;
        esac
        [ $((now - stale_mtime)) -ge 30 ] || return 1
        stale_snapshot=
        if [ -f "$stale_owner" ] && [ ! -L "$stale_owner" ]; then
            stale_snapshot=$(cat "$stale_owner" 2>/dev/null || true)
        elif [ -e "$stale_owner" ] || [ -L "$stale_owner" ]; then
            return 1
        fi
        sleep 1
        [ "$(_install_directory_identity "$stale_claim")" = "$stale_identity" ] ||
            return 1
        if [ -e "$stale_owner" ] || [ -L "$stale_owner" ]; then
            [ -f "$stale_owner" ] && [ ! -L "$stale_owner" ] || return 1
            [ "$(cat "$stale_owner" 2>/dev/null || true)" = "$stale_snapshot" ] ||
                return 1
        else
            [ -z "$stale_snapshot" ] || return 1
        fi
    fi
    if [ -e "$stale_owner" ] || [ -L "$stale_owner" ]; then
        [ -f "$stale_owner" ] && [ ! -L "$stale_owner" ] || return 1
        rm -f "$stale_owner" || return 1
    fi
    rmdir "$stale_claim" 2>/dev/null
}

_install_release_reclaim_claim() {
    [ -n "${INSTALL_RECLAIM_CLAIM:-}" ] || return 0
    claim_owner="$INSTALL_RECLAIM_CLAIM/owner"
    if [ -f "$claim_owner" ] && [ ! -L "$claim_owner" ]; then
        observed_token=$(sed -n '3p' "$claim_owner" 2>/dev/null || true)
        if [ "$observed_token" = "${INSTALL_RECLAIM_TOKEN:-}" ]; then
            rm -f "$claim_owner"
        fi
    fi
    rmdir "$INSTALL_RECLAIM_CLAIM" 2>/dev/null || true
    INSTALL_RECLAIM_CLAIM=
    INSTALL_RECLAIM_TOKEN=
}

_reclaim_install_lock() {
    [ -d "$INSTALL_LOCK" ] && [ ! -L "$INSTALL_LOCK" ] || return 1
    observed_identity=$(_install_directory_identity "$INSTALL_LOCK")
    observed_mtime=$(_install_directory_mtime "$INSTALL_LOCK" || true)
    [ -n "$observed_identity" ] || return 1

    INSTALL_RECLAIM_CLAIM=$(mktemp -d "$INSTALL_LOCK/.reclaim.XXXXXX" 2>/dev/null) ||
        return 1
    INSTALL_RECLAIM_TOKEN="$$.$(date +%s).${INSTALL_LOCK_ATTEMPT}"
    claim_start=$(_install_process_start "$$" || true)
    if [ -z "$claim_start" ] || ! (
        umask 077
        printf '%s\n%s\n%s\n' "$$" "$claim_start" \
            "$INSTALL_RECLAIM_TOKEN" >"$INSTALL_RECLAIM_CLAIM/owner"
    ); then
        _install_release_reclaim_claim
        return 1
    fi
    if [ "$(_install_directory_identity "$INSTALL_LOCK")" != "$observed_identity" ]; then
        _install_release_reclaim_claim
        return 1
    fi

    for stale_claim in "$INSTALL_LOCK"/.reclaim.*; do
        if [ -d "$stale_claim" ] && [ ! -L "$stale_claim" ]; then
            _install_remove_stale_reclaim_claim "$stale_claim" || true
        fi
    done

    owner_snapshot=
    if [ -f "$INSTALL_LOCK_OWNER" ] && [ ! -L "$INSTALL_LOCK_OWNER" ]; then
        owner_snapshot=$(cat "$INSTALL_LOCK_OWNER" 2>/dev/null || true)
    elif [ -e "$INSTALL_LOCK_OWNER" ] || [ -L "$INSTALL_LOCK_OWNER" ]; then
        _install_release_reclaim_claim
        return 1
    fi
    owner_state=$(_install_owner_state "$INSTALL_LOCK_OWNER")
    if [ "$owner_state" = active ]; then
        _install_release_reclaim_claim
        return 1
    fi
    if [ "$owner_state" = invalid ]; then
        now=$(date +%s)
        case "$observed_mtime:$now" in
            *[!0-9:]* | :* | *:)
                _install_release_reclaim_claim
                return 1
                ;;
        esac
        if [ $((now - observed_mtime)) -lt 30 ]; then
            _install_release_reclaim_claim
            return 1
        fi
        sleep 1
    fi
    if [ "$(_install_directory_identity "$INSTALL_LOCK")" != "$observed_identity" ]; then
        _install_release_reclaim_claim
        return 1
    fi
    if [ -e "$INSTALL_LOCK_OWNER" ] || [ -L "$INSTALL_LOCK_OWNER" ]; then
        if [ ! -f "$INSTALL_LOCK_OWNER" ] || [ -L "$INSTALL_LOCK_OWNER" ] ||
            [ "$(cat "$INSTALL_LOCK_OWNER" 2>/dev/null || true)" != "$owner_snapshot" ]; then
            _install_release_reclaim_claim
            return 1
        fi
        rm -f "$INSTALL_LOCK_OWNER" || {
            _install_release_reclaim_claim
            return 1
        }
    fi

    _install_release_reclaim_claim
    rmdir "$INSTALL_LOCK" 2>/dev/null
}

acquire_install_lock() {
    set_install_transaction_paths
    [ -d "$INSTALL_ROOT" ] && [ ! -L "$INSTALL_ROOT" ] || return 1
    INSTALL_LOCK_TOKEN=
    INSTALL_LOCK_ATTEMPT=0
    while [ "$INSTALL_LOCK_ATTEMPT" -lt 4 ]; do
        INSTALL_LOCK_ATTEMPT=$((INSTALL_LOCK_ATTEMPT + 1))
        if mkdir "$INSTALL_LOCK" 2>/dev/null; then
            chmod 700 "$INSTALL_LOCK" 2>/dev/null || true
            owner_start=$(_install_process_start "$$" || true)
            [ -n "$owner_start" ] || {
                rmdir "$INSTALL_LOCK" 2>/dev/null || true
                return 1
            }
            INSTALL_LOCK_TOKEN="$$.$(date +%s).$INSTALL_LOCK_ATTEMPT"
            if ! (
                umask 077
                printf '%s\n%s\n%s\n' "$$" "$owner_start" \
                    "$INSTALL_LOCK_TOKEN" >"$INSTALL_LOCK_OWNER"
            ); then
                rm -f "$INSTALL_LOCK_OWNER"
                rmdir "$INSTALL_LOCK" 2>/dev/null || true
                INSTALL_LOCK_TOKEN=
                return 1
            fi
            return 0
        fi
        _reclaim_install_lock || return 1
    done
    return 1
}

release_install_lock() {
    [ -n "${INSTALL_LOCK_TOKEN:-}" ] || return 0
    observed_token=
    if [ -f "$INSTALL_LOCK_OWNER" ] && [ ! -L "$INSTALL_LOCK_OWNER" ]; then
        observed_token=$(sed -n '3p' "$INSTALL_LOCK_OWNER" 2>/dev/null || true)
    fi
    if [ "$observed_token" = "$INSTALL_LOCK_TOKEN" ]; then
        rm -f "$INSTALL_LOCK_OWNER"
        release_attempt=0
        while [ "$release_attempt" -lt 3 ] && [ -d "$INSTALL_LOCK" ]; do
            rmdir "$INSTALL_LOCK" 2>/dev/null && break
            release_attempt=$((release_attempt + 1))
            sleep 1
        done
    fi
    INSTALL_LOCK_TOKEN=
}

_remove_install_directory() {
    target=$1
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        return 0
    fi
    [ -d "$target" ] && [ ! -L "$target" ] || return 1
    rm -rf "$target"
}

cleanup_stale_install_stages() {
    for stale_stage in "$INSTALL_ROOT"/.install-stage.*; do
        if [ ! -e "$stale_stage" ] && [ ! -L "$stale_stage" ]; then
            continue
        fi
        _remove_install_directory "$stale_stage" || return 1
    done
}

_validate_install_transaction_paths() {
    for slot in "$INSTALL_APP" "$INSTALL_ROLLBACK"; do
        if [ -e "$slot" ] || [ -L "$slot" ]; then
            [ -d "$slot" ] && [ ! -L "$slot" ] || return 1
        fi
    done
    if [ -e "$INSTALL_TRANSACTION" ] || [ -L "$INSTALL_TRANSACTION" ]; then
        [ -d "$INSTALL_TRANSACTION" ] && [ ! -L "$INSTALL_TRANSACTION" ] ||
            return 1
    fi
}

reconcile_install_transaction() {
    set_install_transaction_paths
    _validate_install_transaction_paths || return 1
    if [ -d "$INSTALL_TRANSACTION" ]; then
        if [ -d "$INSTALL_ROLLBACK" ]; then
            _remove_install_directory "$INSTALL_APP" || return 1
        else
            _remove_install_directory "$INSTALL_APP" || return 1
        fi
        rmdir "$INSTALL_TRANSACTION" || return 1
        if [ -d "$INSTALL_ROLLBACK" ]; then
            mv "$INSTALL_ROLLBACK" "$INSTALL_APP" || return 1
        fi
        return 0
    fi
    if [ -d "$INSTALL_ROLLBACK" ]; then
        if [ -d "$INSTALL_APP" ]; then
            _remove_install_directory "$INSTALL_ROLLBACK" || return 1
        else
            mv "$INSTALL_ROLLBACK" "$INSTALL_APP" || return 1
        fi
    fi
}

rollback_install_transaction() {
    _validate_install_transaction_paths || return 1
    if [ -d "$INSTALL_ROLLBACK" ]; then
        _remove_install_directory "$INSTALL_APP" || return 1
    else
        _remove_install_directory "$INSTALL_APP" || return 1
    fi
    if [ -d "$INSTALL_TRANSACTION" ]; then
        rmdir "$INSTALL_TRANSACTION" || return 1
    fi
    if [ -d "$INSTALL_ROLLBACK" ]; then
        mv "$INSTALL_ROLLBACK" "$INSTALL_APP" || return 1
    fi
}

_install_write_shell_quoted() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
}

install_launcher_atomically() {
    if [ -e "$LAUNCHER_DIR" ] || [ -L "$LAUNCHER_DIR" ]; then
        [ -d "$LAUNCHER_DIR" ] && [ ! -L "$LAUNCHER_DIR" ] || return 1
    else
        mkdir -p "$LAUNCHER_DIR" || return 1
    fi
    launcher="$LAUNCHER_DIR/awesome"
    if [ -e "$launcher" ] || [ -L "$launcher" ]; then
        [ -f "$launcher" ] && [ ! -L "$launcher" ] || return 1
    fi
    launcher_temp=$(mktemp "$LAUNCHER_DIR/.awesome.XXXXXX") || return 1
    if ! {
        printf '%s\n' '#!/bin/sh'
        printf 'APP_ROOT='
        _install_write_shell_quoted "$INSTALL_ROOT/app"
        printf '\n'
        cat <<'EOF'
PATH="$APP_ROOT/core/.venv/bin:$PATH"
export PATH
exec "$APP_ROOT/runtimes/node/bin/node" "$APP_ROOT/tui/dist/cli/index.js" "$@"
EOF
    } >"$launcher_temp"
    then
        rm -f "$launcher_temp"
        return 1
    fi
    chmod 755 "$launcher_temp" || {
        rm -f "$launcher_temp"
        return 1
    }
    mv -f "$launcher_temp" "$launcher" || {
        rm -f "$launcher_temp"
        return 1
    }
}

cleanup_committed_rollback() {
    _remove_install_directory "$INSTALL_ROLLBACK"
}

commit_staged_install() {
    candidate=$1
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || return 1
    reconcile_install_transaction || return 1
    if [ -d "$INSTALL_APP" ]; then
        mv "$INSTALL_APP" "$INSTALL_ROLLBACK" || {
            return 1
        }
    fi
    if ! mkdir "$INSTALL_TRANSACTION"; then
        if [ -d "$INSTALL_ROLLBACK" ]; then
            mv "$INSTALL_ROLLBACK" "$INSTALL_APP" 2>/dev/null || true
        fi
        return 1
    fi
    if ! mv "$candidate" "$INSTALL_APP"; then
        rollback_install_transaction || true
        return 1
    fi
    if ! install_launcher_atomically; then
        rollback_install_transaction || true
        return 1
    fi
    if ! rmdir "$INSTALL_TRANSACTION"; then
        rollback_install_transaction || true
        return 1
    fi
    if ! cleanup_committed_rollback; then
        echo "awesome install: warning: committed rollback cleanup was deferred" >&2
    fi
    return 0
}
# END INSTALL TRANSACTION FUNCTIONS

[ "$#" -eq 0 ] || fail "this installer accepts no options"
command -v curl >/dev/null 2>&1 || fail "curl is required"

case "${AWESOME_INSTALL_CANDIDATE:-0}" in
    0)
        [ -z "${AWESOME_INSTALL_CANDIDATE_ASSET_BASE:-}" ] ||
            fail "candidate asset base requires candidate mode"
        ;;
    1)
        CANDIDATE_BASE=${AWESOME_INSTALL_CANDIDATE_ASSET_BASE:-}
        case "$CANDIDATE_BASE" in
            http://127.0.0.1:*) ;;
            *) fail "candidate asset base must be loopback HTTP" ;;
        esac
        CANDIDATE_PORT=${CANDIDATE_BASE#http://127.0.0.1:}
        CANDIDATE_PORT=${CANDIDATE_PORT%/}
        case "$CANDIDATE_PORT" in
            "" | *[!0-9]*) fail "candidate asset base must be loopback HTTP" ;;
        esac
        [ "${#CANDIDATE_PORT}" -le 5 ] ||
            fail "candidate asset base must be loopback HTTP"
        [ "$CANDIDATE_PORT" -ge 1 ] && [ "$CANDIDATE_PORT" -le 65535 ] ||
            fail "candidate asset base must be loopback HTTP"
        ASSET_BASE="http://127.0.0.1:$CANDIDATE_PORT"
        ;;
    *) fail "candidate mode must be 0 or 1" ;;
esac

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
mkdir -p "$INSTALL_ROOT"
[ -d "$INSTALL_ROOT" ] && [ ! -L "$INSTALL_ROOT" ] ||
    fail "install root must be an ordinary directory"
set_install_transaction_paths
INSTALL_LOCK_TOKEN=
acquire_install_lock || fail "another installer is running or the install lock is invalid"
STAGE=
cleanup() {
    if [ -n "${STAGE:-}" ]; then
        _remove_install_directory "$STAGE" || true
    fi
    release_install_lock || true
}
trap cleanup EXIT
reconcile_install_transaction || fail "interrupted installation recovery failed"
cleanup_stale_install_stages || fail "stale installation staging cleanup failed"
STAGE=$(mktemp -d "$INSTALL_ROOT/.install-stage.XXXXXX") ||
    fail "installation staging directory could not be created"

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

commit_staged_install "$STAGED_APP" || fail "atomic application replacement failed"

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1; then
    if ! printf '\n%s\n' "$PATH_LINE" >>"$PROFILE"; then
        echo "awesome install: warning: update PATH in $PROFILE manually" >&2
    fi
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Git is not installed. Install it from https://git-scm.com/downloads"
fi
echo "Awesome $VERSION installed. Open a new terminal and run: awesome"
echo "Close every existing AWESOME session before rerunning this installer."
