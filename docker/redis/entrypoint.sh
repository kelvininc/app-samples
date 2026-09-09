#!/bin/bash

# Single-node Redis for Kelvin.
#
# The official redis image takes all configuration as redis-server command-line
# arguments and supports no environment variables. This script reads the Kelvin
# app configuration mounted at /opt/kelvin/share/config.yaml, translates it into
# arguments and hands off to the image's own entrypoint (which keeps its /data
# chown and privilege drop to the redis user).
#
# Configuration keys (see ui_schemas/configuration.json and README.md):
#
#   port                       Listener port (default: 6379)
#   memory.maxmemory           Memory cap for data (default: 256mb). Keep below
#                              the workload memory limit.
#   memory.maxmemory_policy    Behaviour at the cap (default: noeviction)
#   persistence.appendonly     true enables AOF in addition to the default RDB
#                              snapshots (default: false)
#   security.mode              PLAINTEXT | PASSWORD | TLS | PASSWORD_TLS
#                              (default: PLAINTEXT)
#   security.password          Required by PASSWORD and PASSWORD_TLS
#   security.tls.ca_crt        Required by TLS and PASSWORD_TLS (PEM content)
#   security.tls.tls_crt
#   security.tls.tls_key
#   extra_args                 Extra redis-server arguments, e.g.
#                              "--loglevel debug". Appended last, so they
#                              override every managed setting above; that is
#                              what makes "--tls-auth-clients yes" work, and it
#                              also means "--requirepass ''" or "--port 6379"
#                              can undo the security settings. The string is
#                              only split on whitespace: quotes survive as
#                              literal characters, so an argument containing a
#                              space, or an empty-string argument such as the
#                              "--save ''" idiom, cannot be expressed here.
#
# The platform merges no defaults: the container only sees the keys the deploy
# supplied, so every default below is the one that actually applies.

set -e

log() { echo "$(date +%s): $*"; }
die() { log "Error: $*"; exit 1; }

# Certificates and password files are written below. Create them owner-only
# rather than chmod-ing after the fact, which leaves a readable window.
umask 077

DATA_DIR="/data"
CERTS_DIR="/etc/redis-tls"
AUTH_DIR="/etc/redis-auth"
AUTH_CONF="$AUTH_DIR/requirepass.conf"

CFG="${KELVIN_CONFIG:-/opt/kelvin/share/config.yaml}"

# Two unrelated programs are called yq. Alpine's is mikefarah/yq (Go), Debian's
# is python-yq (a jq wrapper). `yq -r '.a.b'` behaves identically on both, so
# this invocation style is deliberate — do not "modernise" it to `yq e`.
cfg() { [ -f "$CFG" ] && yq -r ".$1 // \"\"" "$CFG" 2>/dev/null || true; }

# A config the parser chokes on would silently read as all-defaults, which for
# the security settings means an open broker. Refuse to start instead.
#
# `yq -r .` is deliberate: a deployment that supplies no configuration gets a
# zero-byte config.yaml from the platform, which is legitimate and must fall
# through to the defaults below. `yq -r .` returns 0 for an empty document and
# non-zero only for a malformed one, on both yq implementations. `yq -e .`
# rejects the empty document too, which would break every default deployment.
if [ -f "$CFG" ] && ! yq -r '.' "$CFG" >/dev/null 2>&1; then
    die "$CFG is not valid YAML."
fi

check_port() {  # $1 = setting name, $2 = value
    case "$2" in
        ''|*[!0-9]*) die "$1 is not a valid port number: '$2'." ;;
    esac
    [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || die "$1 must be 1-65535, got '$2'."
}

REDIS_PORT="$(cfg port)"; : "${REDIS_PORT:=6379}"
check_port port "$REDIS_PORT"

REDIS_MAXMEMORY="$(cfg memory.maxmemory)"; : "${REDIS_MAXMEMORY:=256mb}"
REDIS_MAXMEMORY_POLICY="$(cfg memory.maxmemory_policy)"; : "${REDIS_MAXMEMORY_POLICY:=noeviction}"

# persistence.appendonly is a boolean in the configuration; redis wants yes/no.
# The `// ""` in cfg() turns a JSON false into the empty string, so an explicit
# `appendonly: false` is indistinguishable from an absent key and falls through
# to the default below. Same value here, but do not assume a boolean read can
# tell the two apart. Any other value still reaches the die below.
APPENDONLY_RAW="$(cfg persistence.appendonly)"; : "${APPENDONLY_RAW:=false}"
case "$APPENDONLY_RAW" in
    true|True|yes)  REDIS_APPENDONLY="yes" ;;
    false|False|no) REDIS_APPENDONLY="no" ;;
    *) die "persistence.appendonly must be true or false, got '$APPENDONLY_RAW'." ;;
esac

# --- Security ---
SECURITY_MODE="$(cfg security.mode)"; : "${SECURITY_MODE:=PLAINTEXT}"
REDIS_PASSWORD="$(cfg security.password)"
REDIS_SSL_CA_CRT="$(cfg security.tls.ca_crt)"
REDIS_SSL_TLS_CRT="$(cfg security.tls.tls_crt)"
REDIS_SSL_TLS_KEY="$(cfg security.tls.tls_key)"

case "$SECURITY_MODE" in
    PLAINTEXT)    AUTH_ENABLED="false"; TLS_ENABLED="false" ;;
    PASSWORD)     AUTH_ENABLED="true";  TLS_ENABLED="false" ;;
    TLS)          AUTH_ENABLED="false"; TLS_ENABLED="true" ;;
    PASSWORD_TLS) AUTH_ENABLED="true";  TLS_ENABLED="true" ;;
    *) die "security.mode must be PLAINTEXT, PASSWORD, TLS or PASSWORD_TLS, got '$SECURITY_MODE'." ;;
esac

# Fail fast on a half-configured mode. A typo in one field must never quietly
# downgrade the server to unauthenticated or unencrypted.
if [ "$AUTH_ENABLED" = "true" ] && [ -z "$REDIS_PASSWORD" ]; then
    die "security.mode is $SECURITY_MODE but security.password is empty."
fi

if [ "$TLS_ENABLED" = "true" ]; then
    MISSING=""
    [ -z "$REDIS_SSL_CA_CRT" ]  && MISSING="$MISSING security.tls.ca_crt"
    [ -z "$REDIS_SSL_TLS_CRT" ] && MISSING="$MISSING security.tls.tls_crt"
    [ -z "$REDIS_SSL_TLS_KEY" ] && MISSING="$MISSING security.tls.tls_key"
    if [ -n "$MISSING" ]; then
        die "security.mode is $SECURITY_MODE but these are empty:$MISSING. Refusing to start without TLS."
    fi
else
    if [ -n "$REDIS_SSL_CA_CRT$REDIS_SSL_TLS_CRT$REDIS_SSL_TLS_KEY" ]; then
        die "security.tls.* is set but security.mode is $SECURITY_MODE. Refusing to start an unencrypted server with certificates configured."
    fi
fi

REDIS_EXTRA_ARGS="$(cfg extra_args)"

log "Listener on port: $REDIS_PORT"
log "Security mode: $SECURITY_MODE"
log "Authentication: $AUTH_ENABLED"
log "TLS: $TLS_ENABLED"
log "Max memory: $REDIS_MAXMEMORY ($REDIS_MAXMEMORY_POLICY)"
log "Append-only file: $REDIS_APPENDONLY"

ARGS=()

if [ "$AUTH_ENABLED" = "true" ]; then
    # --requirepass on the command line leaves the password in /proc/*/cmdline
    # for the lifetime of the process, which is why the Redis docs ask for a
    # config file. It is the first argument, so every flag below still wins:
    # the extra_args precedence documented above is unchanged. The umask above
    # creates it owner-only; redis-server reads it after the privilege drop.
    mkdir -p "$AUTH_DIR"
    ESCAPED_PASSWORD="${REDIS_PASSWORD//\\/\\\\}"
    ESCAPED_PASSWORD="${ESCAPED_PASSWORD//\"/\\\"}"
    printf 'requirepass "%s"\n' "$ESCAPED_PASSWORD" > "$AUTH_CONF"
    chown -R redis:redis "$AUTH_DIR"
    ARGS+=("$AUTH_CONF")
fi

ARGS+=(
    --dir "$DATA_DIR"
    --maxmemory "$REDIS_MAXMEMORY"
    --maxmemory-policy "$REDIS_MAXMEMORY_POLICY"
    --appendonly "$REDIS_APPENDONLY"
)

if [ "$TLS_ENABLED" = "true" ]; then
    mkdir -p "$CERTS_DIR"

    printf '%s\n' "$REDIS_SSL_CA_CRT"  > "$CERTS_DIR/ca.crt"
    printf '%s\n' "$REDIS_SSL_TLS_CRT" > "$CERTS_DIR/tls.crt"
    printf '%s\n' "$REDIS_SSL_TLS_KEY" > "$CERTS_DIR/tls.key"
    chown -R redis:redis "$CERTS_DIR"

    # TLS-only: the plaintext listener is disabled. Client certificates are not
    # required; set --tls-auth-clients yes via extra_args for mTLS.
    ARGS+=(
        --port 0
        --tls-port "$REDIS_PORT"
        --tls-cert-file "$CERTS_DIR/tls.crt"
        --tls-key-file "$CERTS_DIR/tls.key"
        --tls-ca-cert-file "$CERTS_DIR/ca.crt"
        --tls-auth-clients no
    )
else
    ARGS+=(--port "$REDIS_PORT")
fi

if [ -n "$REDIS_EXTRA_ARGS" ]; then
    # Split on whitespace with globbing disabled: the working directory is
    # /data, so an unquoted "*" would otherwise expand against the RDB and AOF
    # files sitting there. Quotes are not removed by the split, so values
    # containing spaces need a config file instead.
    set -f
    # shellcheck disable=SC2206  # the word split is the point; set -f covers the glob
    ARGS+=($REDIS_EXTRA_ARGS)
    set +f
fi

# Hand off to the redis image's own entrypoint
exec /usr/local/bin/docker-entrypoint.sh redis-server "${ARGS[@]}"
