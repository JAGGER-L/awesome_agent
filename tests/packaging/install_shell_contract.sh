#!/bin/sh
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/awesome-installer-contract.XXXXXX")
NETWORK_MARKER="$STAGE/network-called"

cleanup() {
    for background_pid in "${reclaim_holder_pid:-}" "${holder_pid:-}" \
        "${crashed_pid:-}"
    do
        if [ -n "$background_pid" ] && kill -0 "$background_pid" 2>/dev/null; then
            kill -9 "$background_pid" 2>/dev/null || true
            wait "$background_pid" 2>/dev/null || true
        fi
    done
    rm -rf "$STAGE"
}
trap cleanup EXIT

mkdir -p "$STAGE/bin"
cat >"$STAGE/bin/curl" <<'EOF'
#!/bin/sh
: >"$AWESOME_INSTALL_NETWORK_MARKER"
exit 97
EOF
cat >"$STAGE/bin/uname" <<'EOF'
#!/bin/sh
echo Unsupported
EOF
chmod 755 "$STAGE/bin/curl" "$STAGE/bin/uname"

run_guard() {
    mode=$1
    base=$2
    expected=$3
    rm -f "$NETWORK_MARKER"
    set +e
    output=$(
        AWESOME_INSTALL_CANDIDATE="$mode" \
        AWESOME_INSTALL_CANDIDATE_ASSET_BASE="$base" \
        AWESOME_INSTALL_NETWORK_MARKER="$NETWORK_MARKER" \
        PATH="$STAGE/bin:$PATH" \
        sh "$ROOT/install.sh" 2>&1
    )
    status=$?
    set -e
    [ "$status" -ne 0 ] || {
        echo "installer guard unexpectedly succeeded" >&2
        exit 1
    }
    printf '%s\n' "$output" | grep -F "$expected" >/dev/null
    [ ! -e "$NETWORK_MARKER" ] || {
        echo "installer guard reached the network" >&2
        exit 1
    }
}

run_guard 0 "http://127.0.0.1:1" "candidate asset base requires candidate mode"
run_guard 2 "" "candidate mode must be 0 or 1"
for rejected in \
    "" \
    "https://127.0.0.1:1" \
    "http://localhost:1" \
    "http://127.0.0.1:0" \
    "http://127.0.0.1:65536" \
    "http://127.0.0.1:999999999999999999999" \
    "http://127.0.0.1:1/path" \
    "http://user@127.0.0.1:1" \
    "http://127.0.0.1:1?query" \
    "http://127.0.0.1:1#fragment"
do
    run_guard 1 "$rejected" "candidate asset base must be loopback HTTP"
done

run_guard 1 "http://127.0.0.1:1" "supported hosts are"
run_guard 1 "http://127.0.0.1:65535/" "supported hosts are"

FUNCTIONS="$STAGE/install-transaction-functions.sh"
sed -n \
    '/^# BEGIN INSTALL TRANSACTION FUNCTIONS$/,/^# END INSTALL TRANSACTION FUNCTIONS$/p' \
    "$ROOT/install.sh" >"$FUNCTIONS"
grep -F "commit_staged_install()" "$FUNCTIONS" >/dev/null
# shellcheck disable=SC1090
. "$FUNCTIONS"

prepare_case() {
    INSTALL_ROOT=$1
    LAUNCHER_DIR="$INSTALL_ROOT/bin"
    mkdir -p "$INSTALL_ROOT" "$LAUNCHER_DIR"
    set_install_transaction_paths
}

assert_app_version() {
    expected=$1
    actual=$(cat "$INSTALL_APP/VERSION")
    [ "$actual" = "$expected" ] || {
        echo "expected app version $expected, found $actual" >&2
        exit 1
    }
}

wait_for_file() {
    target=$1
    attempts=0
    while [ ! -e "$target" ]; do
        attempts=$((attempts + 1))
        [ "$attempts" -le 15 ] || {
            echo "timed out waiting for lock holder" >&2
            exit 1
        }
        sleep 1
    done
}

wait_for_process_file() {
    target=$1
    process_id=$2
    process_log=$3
    attempts=0
    while [ ! -e "$target" ]; do
        if ! kill -0 "$process_id" 2>/dev/null; then
            cat "$process_log" >&2
            wait "$process_id" 2>/dev/null || true
            echo "lock holder exited before reaching its barrier" >&2
            exit 1
        fi
        attempts=$((attempts + 1))
        [ "$attempts" -le 15 ] || {
            cat "$process_log" >&2
            echo "timed out waiting for lock holder" >&2
            exit 1
        }
        sleep 1
    done
}

count_directory_entries() {
    directory=$1
    entry_count=0
    for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
        if [ -e "$entry" ] || [ -L "$entry" ]; then
            entry_count=$((entry_count + 1))
        fi
    done
    printf '%s\n' "$entry_count"
}

FAULT_ROOT="$STAGE/fault"
prepare_case "$FAULT_ROOT"
mkdir -p "$INSTALL_APP" "$FAULT_ROOT/candidate"
printf '%s\n' old >"$INSTALL_APP/VERSION"
printf '%s\n' new >"$FAULT_ROOT/candidate/VERSION"
acquire_install_lock
(
    install_launcher_atomically() {
        return 71
    }
    if commit_staged_install "$FAULT_ROOT/candidate"; then
        echo "fault-injected transaction unexpectedly committed" >&2
        exit 1
    fi
)
assert_app_version old
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

FRESH_FAULT_ROOT="$STAGE/fresh-fault"
prepare_case "$FRESH_FAULT_ROOT"
mkdir -p "$FRESH_FAULT_ROOT/candidate"
printf '%s\n' new >"$FRESH_FAULT_ROOT/candidate/VERSION"
acquire_install_lock
(
    install_launcher_atomically() {
        return 71
    }
    if commit_staged_install "$FRESH_FAULT_ROOT/candidate"; then
        echo "fresh fault-injected transaction unexpectedly committed" >&2
        exit 1
    fi
)
[ ! -e "$INSTALL_APP" ]
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

SUCCESS_ROOT="$STAGE/success"
prepare_case "$SUCCESS_ROOT"
mkdir -p "$INSTALL_APP" "$SUCCESS_ROOT/candidate"
printf '%s\n' old >"$INSTALL_APP/VERSION"
printf '%s\n' new >"$SUCCESS_ROOT/candidate/VERSION"
printf '%s\n' old-launcher >"$LAUNCHER_DIR/awesome"
acquire_install_lock
commit_staged_install "$SUCCESS_ROOT/candidate"
assert_app_version new
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
[ ! -e "$SUCCESS_ROOT/candidate" ]
grep -F "APP_ROOT='$SUCCESS_ROOT/app'" "$LAUNCHER_DIR/awesome" >/dev/null
for launcher_residue in "$LAUNCHER_DIR"/.awesome.*; do
    if [ -e "$launcher_residue" ] || [ -L "$launcher_residue" ]; then
        echo "atomic launcher replacement left a temporary file" >&2
        exit 1
    fi
done
release_install_lock

POST_COMMIT_ROOT="$STAGE/post-commit-cleanup"
prepare_case "$POST_COMMIT_ROOT"
mkdir -p "$INSTALL_APP" "$POST_COMMIT_ROOT/candidate"
printf '%s\n' old >"$INSTALL_APP/VERSION"
printf '%s\n' new >"$POST_COMMIT_ROOT/candidate/VERSION"
acquire_install_lock
(
    cleanup_committed_rollback() {
        return 73
    }
    commit_staged_install "$POST_COMMIT_ROOT/candidate"
)
assert_app_version new
[ -d "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
reconcile_install_transaction
assert_app_version new
[ ! -e "$INSTALL_ROLLBACK" ]
release_install_lock

QUOTED_ROOT="$STAGE/path with ' quote"
prepare_case "$QUOTED_ROOT"
mkdir -p "$QUOTED_ROOT/candidate"
printf '%s\n' new >"$QUOTED_ROOT/candidate/VERSION"
acquire_install_lock
commit_staged_install "$QUOTED_ROOT/candidate"
sh -n "$LAUNCHER_DIR/awesome"
assert_app_version new
release_install_lock

INVALID_LAUNCHER_ROOT="$STAGE/invalid-launcher"
prepare_case "$INVALID_LAUNCHER_ROOT"
mkdir -p "$INSTALL_APP" "$INVALID_LAUNCHER_ROOT/candidate" \
    "$LAUNCHER_DIR/awesome"
printf '%s\n' old >"$INSTALL_APP/VERSION"
printf '%s\n' new >"$INVALID_LAUNCHER_ROOT/candidate/VERSION"
acquire_install_lock
if commit_staged_install "$INVALID_LAUNCHER_ROOT/candidate"; then
    echo "directory launcher slot unexpectedly committed" >&2
    exit 1
fi
assert_app_version old
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

EXTERNAL_LAUNCHER="$STAGE/external-launcher"
LINKED_LAUNCHER_ROOT="$STAGE/linked-launcher"
mkdir -p "$EXTERNAL_LAUNCHER" "$LINKED_LAUNCHER_ROOT/app" \
    "$LINKED_LAUNCHER_ROOT/candidate"
printf '%s\n' safe >"$EXTERNAL_LAUNCHER/sentinel"
printf '%s\n' old >"$LINKED_LAUNCHER_ROOT/app/VERSION"
printf '%s\n' new >"$LINKED_LAUNCHER_ROOT/candidate/VERSION"
ln -s "$EXTERNAL_LAUNCHER" "$LINKED_LAUNCHER_ROOT/bin"
prepare_case "$LINKED_LAUNCHER_ROOT"
acquire_install_lock
if commit_staged_install "$LINKED_LAUNCHER_ROOT/candidate"; then
    echo "symlink launcher directory unexpectedly committed" >&2
    exit 1
fi
assert_app_version old
[ "$(cat "$EXTERNAL_LAUNCHER/sentinel")" = safe ]
[ "$(count_directory_entries "$EXTERNAL_LAUNCHER")" -eq 1 ]
release_install_lock

CRASH_ROOT="$STAGE/crash-residue"
prepare_case "$CRASH_ROOT"
mkdir -p "$INSTALL_APP" "$INSTALL_ROLLBACK" "$INSTALL_TRANSACTION"
printf '%s\n' interrupted-new >"$INSTALL_APP/VERSION"
printf '%s\n' old >"$INSTALL_ROLLBACK/VERSION"
acquire_install_lock
reconcile_install_transaction
assert_app_version old
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

COMMITTED_ROOT="$STAGE/committed-residue"
prepare_case "$COMMITTED_ROOT"
mkdir -p "$INSTALL_APP" "$INSTALL_ROLLBACK"
printf '%s\n' new >"$INSTALL_APP/VERSION"
printf '%s\n' old >"$INSTALL_ROLLBACK/VERSION"
acquire_install_lock
reconcile_install_transaction
assert_app_version new
[ ! -e "$INSTALL_ROLLBACK" ]
release_install_lock

BLOCKED_MARKER_ROOT="$STAGE/blocked-marker"
prepare_case "$BLOCKED_MARKER_ROOT"
mkdir -p "$INSTALL_APP" "$INSTALL_ROLLBACK" "$INSTALL_TRANSACTION"
printf '%s\n' interrupted-new >"$INSTALL_APP/VERSION"
printf '%s\n' old >"$INSTALL_ROLLBACK/VERSION"
printf '%s\n' blocker >"$INSTALL_TRANSACTION/blocker"
acquire_install_lock
if reconcile_install_transaction 2>/dev/null; then
    echo "nonempty transaction marker unexpectedly reconciled" >&2
    exit 1
fi
[ ! -e "$INSTALL_APP" ]
[ -d "$INSTALL_ROLLBACK" ]
[ -d "$INSTALL_TRANSACTION" ]
rm -f "$INSTALL_TRANSACTION/blocker"
reconcile_install_transaction
assert_app_version old
[ ! -e "$INSTALL_ROLLBACK" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

FRESH_CRASH_ROOT="$STAGE/fresh-crash-residue"
prepare_case "$FRESH_CRASH_ROOT"
mkdir -p "$INSTALL_APP" "$INSTALL_TRANSACTION"
printf '%s\n' interrupted-new >"$INSTALL_APP/VERSION"
acquire_install_lock
reconcile_install_transaction
reconcile_install_transaction
[ ! -e "$INSTALL_APP" ]
[ ! -e "$INSTALL_TRANSACTION" ]
release_install_lock

ONLY_ROLLBACK_ROOT="$STAGE/only-rollback-residue"
prepare_case "$ONLY_ROLLBACK_ROOT"
mkdir -p "$INSTALL_ROLLBACK"
printf '%s\n' old >"$INSTALL_ROLLBACK/VERSION"
acquire_install_lock
reconcile_install_transaction
reconcile_install_transaction
assert_app_version old
[ ! -e "$INSTALL_ROLLBACK" ]
release_install_lock

OWNERLESS_ROOT="$STAGE/ownerless-lock"
prepare_case "$OWNERLESS_ROOT"
mkdir "$INSTALL_LOCK"
touch -t 200001010000 "$INSTALL_LOCK"
acquire_install_lock
release_install_lock

REAL_INSTALL_ROOT="$STAGE/real-install-root"
LINK_INSTALL_ROOT="$STAGE/link-install-root"
mkdir -p "$REAL_INSTALL_ROOT"
printf '%s\n' safe >"$REAL_INSTALL_ROOT/sentinel"
ln -s "$REAL_INSTALL_ROOT" "$LINK_INSTALL_ROOT"
INSTALL_ROOT=$LINK_INSTALL_ROOT
LAUNCHER_DIR="$INSTALL_ROOT/bin"
set_install_transaction_paths
if acquire_install_lock; then
    echo "installer lock accepted a symlink install root" >&2
    exit 1
fi
[ "$(cat "$REAL_INSTALL_ROOT/sentinel")" = safe ]
[ "$(count_directory_entries "$REAL_INSTALL_ROOT")" -eq 1 ]

LOCALE_BIN="$STAGE/locale-bin"
LOCALE_MARKER="$STAGE/locale-seen"
mkdir -p "$LOCALE_BIN"
cat >"$LOCALE_BIN/ps" <<'EOF'
#!/bin/sh
printf '%s\n' "${LC_ALL:-}" >"$AWESOME_TEST_LOCALE_MARKER"
printf '%s\n' 'Mon Jan  1 00:00:00 2001'
EOF
chmod 755 "$LOCALE_BIN/ps"
AWESOME_TEST_LOCALE_MARKER="$LOCALE_MARKER" \
    PATH="$LOCALE_BIN:$PATH" _install_process_start "$$" >/dev/null
[ "$(cat "$LOCALE_MARKER")" = C ]

RECLAIM_ROOT="$STAGE/reclaim-race"
prepare_case "$RECLAIM_ROOT"
mkdir "$INSTALL_LOCK"
printf '%s\n%s\n%s\n' 99999999 dead-start dead-token >"$INSTALL_LOCK_OWNER"
touch -t 200001010000 "$INSTALL_LOCK"

REAL_MKTEMP=$(command -v mktemp)
RECLAIM_BIN="$STAGE/reclaim-bin"
RECLAIM_READY="$STAGE/reclaim-ready"
RECLAIM_RELEASE="$STAGE/reclaim-release"
RECLAIM_ACQUIRED="$STAGE/reclaim-acquired"
RECLAIM_DONE="$STAGE/reclaim-done"
RECLAIM_LOG="$STAGE/reclaim-holder.log"
mkdir -p "$RECLAIM_BIN"
cat >"$RECLAIM_BIN/mktemp" <<'EOF'
#!/bin/sh
set -eu
claim=$($AWESOME_REAL_MKTEMP "$@")
template=
for argument in "$@"; do
    template=$argument
done
case "$template" in
    */.install.lock/.reclaim.XXXXXX)
        if [ "${AWESOME_TEST_RECLAIM_PAUSE:-0}" = 1 ]; then
            : >"$AWESOME_TEST_RECLAIM_READY"
            while [ ! -e "$AWESOME_TEST_RECLAIM_RELEASE" ]; do
                sleep 1
            done
        fi
        ;;
esac
printf '%s\n' "$claim"
EOF
chmod 755 "$RECLAIM_BIN/mktemp"

RECLAIM_HOLDER="$STAGE/reclaim-holder.sh"
cat >"$RECLAIM_HOLDER" <<'EOF'
#!/bin/sh
set -eu
. "$1"
INSTALL_ROOT=$2
LAUNCHER_DIR="$INSTALL_ROOT/bin"
set_install_transaction_paths
acquire_install_lock
: >"$3"
while [ ! -e "$4" ]; do
    sleep 1
done
release_install_lock
EOF
chmod 755 "$RECLAIM_HOLDER"

env AWESOME_REAL_MKTEMP="$REAL_MKTEMP" \
    AWESOME_TEST_RECLAIM_PAUSE=1 \
    AWESOME_TEST_RECLAIM_READY="$RECLAIM_READY" \
    AWESOME_TEST_RECLAIM_RELEASE="$RECLAIM_RELEASE" \
    PATH="$RECLAIM_BIN:$PATH" \
    sh "$RECLAIM_HOLDER" "$FUNCTIONS" "$RECLAIM_ROOT" \
        "$RECLAIM_ACQUIRED" "$RECLAIM_DONE" >"$RECLAIM_LOG" 2>&1 &
reclaim_holder_pid=$!
wait_for_process_file "$RECLAIM_READY" "$reclaim_holder_pid" "$RECLAIM_LOG"
(
    prepare_case "$RECLAIM_ROOT"
    if AWESOME_REAL_MKTEMP="$REAL_MKTEMP" PATH="$RECLAIM_BIN:$PATH" \
        acquire_install_lock; then
        echo "second reclaimer crossed the active claim barrier" >&2
        exit 1
    fi
)
: >"$RECLAIM_RELEASE"
wait_for_process_file "$RECLAIM_ACQUIRED" "$reclaim_holder_pid" "$RECLAIM_LOG"
(
    prepare_case "$RECLAIM_ROOT"
    if acquire_install_lock; then
        release_install_lock
        echo "contender removed a newly active lock" >&2
        exit 1
    fi
)
: >"$RECLAIM_DONE"
wait "$reclaim_holder_pid"
reclaim_holder_pid=
prepare_case "$RECLAIM_ROOT"
acquire_install_lock
release_install_lock

HOLDER="$STAGE/lock-holder.sh"
cat >"$HOLDER" <<'EOF'
#!/bin/sh
set -eu
. "$1"
INSTALL_ROOT=$2
LAUNCHER_DIR="$INSTALL_ROOT/bin"
mkdir -p "$LAUNCHER_DIR"
set_install_transaction_paths
acquire_install_lock
: >"$3"
while [ ! -e "$4" ]; do
    sleep 1
done
release_install_lock
EOF
chmod 755 "$HOLDER"

LOCK_ROOT="$STAGE/concurrent-lock"
READY="$STAGE/lock-ready"
RELEASE="$STAGE/lock-release"
sh "$HOLDER" "$FUNCTIONS" "$LOCK_ROOT" "$READY" "$RELEASE" &
holder_pid=$!
wait_for_file "$READY"
(
    prepare_case "$LOCK_ROOT"
    if acquire_install_lock; then
        echo "concurrent installer acquired an active lock" >&2
        exit 1
    fi
)
: >"$RELEASE"
wait "$holder_pid"
holder_pid=
prepare_case "$LOCK_ROOT"
acquire_install_lock
release_install_lock

CRASH_LOCK_ROOT="$STAGE/crashed-lock"
CRASH_READY="$STAGE/crash-lock-ready"
CRASH_RELEASE="$STAGE/crash-lock-release"
sh "$HOLDER" "$FUNCTIONS" "$CRASH_LOCK_ROOT" "$CRASH_READY" "$CRASH_RELEASE" &
crashed_pid=$!
wait_for_file "$CRASH_READY"
kill -9 "$crashed_pid"
set +e
wait "$crashed_pid" 2>/dev/null
set -e
crashed_pid=
prepare_case "$CRASH_LOCK_ROOT"
acquire_install_lock
release_install_lock
