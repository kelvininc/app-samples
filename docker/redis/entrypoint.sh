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
#                              "--save '' --loglevel debug"
#
# The platform merges no defaults: the container only sees the keys the deploy
# supplied, so every default below is the one that actually applies.

set -e

DATA_DIR="/data"
CERTS_DIR="/etc/redis-tls"

CFG="${KELVIN_CONFIG:-/opt/kelvin/share/config.yaml}"
cfg(){ [ -f "$CFG" ] && yq -r ".$1 // \"\"" "$CFG" 2>/dev/null || true; }

log() { echo "$(date +%s): $*"; }
die() { log "Error: $*"; exit 1; }

REDIS_PORT="$(cfg port)"; : "${REDIS_PORT:=6379}"
case "$REDIS_PORT" in
    *[!0-9]*|'')
        die "port is not a valid port number: '$REDIS_PORT'."
        ;;
esac

REDIS_MAXMEMORY="$(cfg memory.maxmemory)"; : "${REDIS_MAXMEMORY:=256mb}"
REDIS_MAXMEMORY_POLICY="$(cfg memory.maxmemory_policy)"; : "${REDIS_MAXMEMORY_POLICY:=noeviction}"

# persistence.appendonly is a boolean in the configuration; redis wants yes/no
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

    printf '%s\n' "$REDIS_SSL_CA_CRT"  > "$CERTS_DIR/ca.crt"
    printf '%s\n' "$REDIS_SSL_TLS_CRT" > "$CERTS_DIR/tls.crt"
    printf '%s\n' "$REDIS_SSL_TLS_KEY" > "$CERTS_DIR/tls.key"
    chown -R redis:redis "$CERTS_DIR"
    chmod 600 "$CERTS_DIR/tls.key"

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
    # Word-split intentionally; values with spaces need a config file instead
    ARGS+=($REDIS_EXTRA_ARGS)
fi

# Hand off to the redis image's own entrypoint
exec /usr/local/bin/docker-entrypoint.sh redis-server "${ARGS[@]}"
