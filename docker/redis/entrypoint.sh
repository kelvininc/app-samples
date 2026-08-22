#!/bin/bash

# Single-node Redis for Kelvin.
#
# The official redis image takes all configuration as redis-server command-line
# arguments and supports no environment variables. This script translates the
# app's REDIS_* environment variables into arguments and hands off to the
# image's own entrypoint (which keeps its /data chown and privilege drop to the
# redis user).
#
#   REDIS_PORT               Listener port (default: 6379)
#   REDIS_MAXMEMORY          Memory cap for data (default: 256mb). Keep below
#                            the workload memory limit.
#   REDIS_MAXMEMORY_POLICY   Behavior at the cap (default: noeviction)
#   REDIS_APPENDONLY         "yes" enables AOF persistence in addition to the
#                            default RDB snapshots (default: no)
#   REDIS_PASSWORD           Set -> clients must AUTH with this password.
#                            Unset -> no authentication.
#   REDIS_SSL_CA_CRT, REDIS_SSL_TLS_CRT, REDIS_SSL_TLS_KEY
#                            All set (PEM content) -> the listener switches to
#                            TLS on REDIS_PORT (plaintext disabled)
#   REDIS_EXTRA_ARGS         Extra redis-server arguments, e.g.
#                            "--save '' --loglevel debug"

set -e

DATA_DIR="/data"
CERTS_DIR="/etc/redis-tls"

log() { echo "$(date +%s): $*"; }

REDIS_PORT="${REDIS_PORT:-6379}"
case "$REDIS_PORT" in
    *[!0-9]*|'')
        log "Error: REDIS_PORT is not a valid port number."
        exit 1
        ;;
esac

REDIS_MAXMEMORY="${REDIS_MAXMEMORY:-256mb}"
REDIS_MAXMEMORY_POLICY="${REDIS_MAXMEMORY_POLICY:-noeviction}"
REDIS_APPENDONLY="${REDIS_APPENDONLY:-no}"

# --- Authentication ---
AUTH_ENABLED="false"
if [ -n "$REDIS_PASSWORD" ]; then
    AUTH_ENABLED="true"
fi

# --- TLS ---
TLS_ENABLED="false"
if [ -n "$REDIS_SSL_CA_CRT" ] || [ -n "$REDIS_SSL_TLS_CRT" ] || [ -n "$REDIS_SSL_TLS_KEY" ]; then
    if [ -n "$REDIS_SSL_CA_CRT" ] && [ -n "$REDIS_SSL_TLS_CRT" ] && [ -n "$REDIS_SSL_TLS_KEY" ]; then
        TLS_ENABLED="true"
    else
        log "Warning: TLS requires REDIS_SSL_CA_CRT, REDIS_SSL_TLS_CRT and REDIS_SSL_TLS_KEY. Starting without TLS."
    fi
fi

log "Listener on port: $REDIS_PORT"
log "Authentication: $AUTH_ENABLED"
log "TLS: $TLS_ENABLED"
log "Max memory: $REDIS_MAXMEMORY ($REDIS_MAXMEMORY_POLICY)"
log "Append-only file: $REDIS_APPENDONLY"

ARGS=(
    --dir "$DATA_DIR"
    --maxmemory "$REDIS_MAXMEMORY"
    --maxmemory-policy "$REDIS_MAXMEMORY_POLICY"
    --appendonly "$REDIS_APPENDONLY"
)

if [ "$AUTH_ENABLED" = "true" ]; then
    ARGS+=(--requirepass "$REDIS_PASSWORD")
fi

if [ "$TLS_ENABLED" = "true" ]; then
    mkdir -p "$CERTS_DIR"

    # Secrets arrive as single-line values with literal \n sequences
    echo "$REDIS_SSL_CA_CRT" | sed 's/\\n/\'$'\n''/g' > "$CERTS_DIR/ca.crt"
    echo "$REDIS_SSL_TLS_CRT" | sed 's/\\n/\'$'\n''/g' > "$CERTS_DIR/tls.crt"
    echo "$REDIS_SSL_TLS_KEY" | sed 's/\\n/\'$'\n''/g' > "$CERTS_DIR/tls.key"
    chown -R redis:redis "$CERTS_DIR"
    chmod 600 "$CERTS_DIR/tls.key"

    # TLS-only: the plaintext listener is disabled. Client certificates are not
    # required; set --tls-auth-clients yes via REDIS_EXTRA_ARGS for mTLS.
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
    # Word-split intentionally; values with spaces need a config file instead
    ARGS+=($REDIS_EXTRA_ARGS)
fi

# Hand off to the redis image's own entrypoint
exec /usr/local/bin/docker-entrypoint.sh redis-server "${ARGS[@]}"
