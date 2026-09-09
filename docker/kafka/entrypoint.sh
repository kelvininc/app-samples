#!/bin/bash

# Single-node Kafka (KRaft) for Kelvin.
#
# Every operator setting comes from the Kelvin application configuration, which
# the platform mounts at /opt/kelvin/share/config.yaml. This script reads that
# file and translates it into the KAFKA_* variables the apache/kafka image maps
# onto server.properties, then hands off to the image's own run script.
#
# Configuration keys (see ui_schemas/configuration.json and README.md):
#
#   listener.port                 Client listener port (default: 9092)
#   listener.advertised_host      Address advertised to in-cluster clients.
#                                 Empty falls back to KELVIN_WORKLOAD_NAME (the
#                                 workload's service DNS name), then localhost.
#
#   external.port                 Enables a second listener for clients outside
#                                 the cluster; must match a host-type port on
#                                 the deployment.
#   external.advertised_host      Node address advertised to external clients.
#                                 Required together with external.port.
#
#   security.protocol             PLAINTEXT | SSL | SASL_PLAINTEXT | SASL_SSL
#   security.sasl.username        Required by the SASL_* protocols.
#   security.sasl.password
#   security.tls.ca_crt           Required by the *_SSL protocols. PEM content;
#   security.tls.tls_crt          the private key must be PKCS#8.
#   security.tls.tls_key
#
#   jvm.heap_opts                 JVM heap flags (default: -Xmx512m -Xms512m)
#   extra_properties              Map of server.properties entries applied
#                                 verbatim, e.g. log.retention.hours: "48".
#
# KELVIN_WORKLOAD_NAME is the only operator-independent input still read from
# the environment: the platform injects it.

set -e

DATA_DIR="/var/lib/kafka/data"
CERTS_DIR="/etc/kafka/secrets"
CFG="${KELVIN_CONFIG:-/opt/kelvin/share/config.yaml}"

log() { echo "$(date +%s): $*"; }
cfg() { [ -f "$CFG" ] && yq -r ".$1 // \"\"" "$CFG" 2>/dev/null || true; }

# A config the parser chokes on would silently read as all-defaults, which for
# the security settings means an open broker. Refuse to start instead.
if [ -f "$CFG" ] && ! yq -e '.' "$CFG" >/dev/null 2>&1; then
    log "Error: $CFG is not valid YAML."
    exit 1
fi

# --- Internal listener ---
BROKER_PORT="$(cfg listener.port)"
: "${BROKER_PORT:=9092}"
case "$BROKER_PORT" in
    *[!0-9]*|'')
        log "Error: listener.port is not a valid port number."
        exit 1
        ;;
esac

ADVERTISED_HOST="$(cfg listener.advertised_host)"
: "${ADVERTISED_HOST:=${KELVIN_WORKLOAD_NAME:-localhost}}"

# --- External listener ---
EXTERNAL_PORT="$(cfg external.port)"
EXTERNAL_ADVERTISED_HOST="$(cfg external.advertised_host)"

EXTERNAL_ENABLED="false"
if [ -n "$EXTERNAL_PORT" ] || [ -n "$EXTERNAL_ADVERTISED_HOST" ]; then
    if [ -z "$EXTERNAL_PORT" ] || [ -z "$EXTERNAL_ADVERTISED_HOST" ]; then
        log "Error: external access needs both external.port and external.advertised_host."
        exit 1
    fi
    case "$EXTERNAL_PORT" in
        *[!0-9]*)
            log "Error: external.port is not a valid port number."
            exit 1
            ;;
    esac
    EXTERNAL_ENABLED="true"
fi

# --- Security ---
PROTOCOL="$(cfg security.protocol)"
: "${PROTOCOL:=PLAINTEXT}"

SASL_USERNAME="$(cfg security.sasl.username)"
SASL_PASSWORD="$(cfg security.sasl.password)"
SSL_CA_CRT="$(cfg security.tls.ca_crt)"
SSL_TLS_CRT="$(cfg security.tls.tls_crt)"
SSL_TLS_KEY="$(cfg security.tls.tls_key)"

case "$PROTOCOL" in
    PLAINTEXT)      AUTH_ENABLED="false"; TLS_ENABLED="false" ;;
    SSL)            AUTH_ENABLED="false"; TLS_ENABLED="true"  ;;
    SASL_PLAINTEXT) AUTH_ENABLED="true";  TLS_ENABLED="false" ;;
    SASL_SSL)       AUTH_ENABLED="true";  TLS_ENABLED="true"  ;;
    *)
        log "Error: security.protocol must be PLAINTEXT, SSL, SASL_PLAINTEXT or SASL_SSL (got '$PROTOCOL')."
        exit 1
        ;;
esac

if [ "$AUTH_ENABLED" = "true" ] && { [ -z "$SASL_USERNAME" ] || [ -z "$SASL_PASSWORD" ]; }; then
    log "Error: security.protocol $PROTOCOL requires security.sasl.username and security.sasl.password."
    exit 1
fi

# A typo in one certificate field must never silently produce an open broker.
if [ "$TLS_ENABLED" = "true" ] && { [ -z "$SSL_CA_CRT" ] || [ -z "$SSL_TLS_CRT" ] || [ -z "$SSL_TLS_KEY" ]; }; then
    log "Error: security.protocol $PROTOCOL requires security.tls.ca_crt, security.tls.tls_crt and security.tls.tls_key."
    exit 1
fi

log "Internal listener on port: $BROKER_PORT (advertised as $ADVERTISED_HOST)"
log "External listener enabled: $EXTERNAL_ENABLED"
if [ "$EXTERNAL_ENABLED" = "true" ]; then
    log "External listener on port: $EXTERNAL_PORT (advertised as $EXTERNAL_ADVERTISED_HOST)"
fi
log "Authentication (SASL/PLAIN): $AUTH_ENABLED"
log "TLS: $TLS_ENABLED"
log "Client security protocol: $PROTOCOL"

# --- Listeners ---
# The CONTROLLER listener stays plaintext on localhost: single-node KRaft
# traffic never leaves the container and the port is not exposed.
LISTENERS="INTERNAL://0.0.0.0:${BROKER_PORT},CONTROLLER://localhost:9093"
ADVERTISED="INTERNAL://${ADVERTISED_HOST}:${BROKER_PORT}"
PROTOCOL_MAP="INTERNAL:${PROTOCOL},CONTROLLER:PLAINTEXT"

if [ "$EXTERNAL_ENABLED" = "true" ]; then
    LISTENERS="${LISTENERS},EXTERNAL://0.0.0.0:${EXTERNAL_PORT}"
    ADVERTISED="${ADVERTISED},EXTERNAL://${EXTERNAL_ADVERTISED_HOST}:${EXTERNAL_PORT}"
    PROTOCOL_MAP="${PROTOCOL_MAP},EXTERNAL:${PROTOCOL}"
fi

# --- Extra broker properties ---
# Exported first so the managed settings below always win on a conflict.
if [ -f "$CFG" ]; then
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        property="${entry%%=*}"
        value="${entry#*=}"
        variable="KAFKA_$(echo "$property" | tr '.' '_' | tr '[:lower:]' '[:upper:]')"
        log "Extra broker property: $property"
        export "$variable=$value"
    done < <(yq -r '(.extra_properties // {}) | to_entries | .[] | .key + "=" + .value' "$CFG" 2>/dev/null)
fi

HEAP_OPTS="$(cfg jvm.heap_opts)"
: "${HEAP_OPTS:=-Xmx512m -Xms512m}"
export KAFKA_HEAP_OPTS="$HEAP_OPTS"

# Single-node KRaft baseline. The image's own defaults only apply when no user
# configuration is provided at all, so the full set must be declared here.
export KAFKA_NODE_ID="1"
export KAFKA_PROCESS_ROLES="broker,controller"
export KAFKA_CONTROLLER_QUORUM_VOTERS="1@localhost:9093"
export KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR="1"
export KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR="1"
export KAFKA_TRANSACTION_STATE_LOG_MIN_ISR="1"

export KAFKA_LISTENERS="$LISTENERS"
export KAFKA_ADVERTISED_LISTENERS="$ADVERTISED"
export KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="$PROTOCOL_MAP"
export KAFKA_INTER_BROKER_LISTENER_NAME="INTERNAL"
export KAFKA_CONTROLLER_LISTENER_NAMES="CONTROLLER"
export KAFKA_LOG_DIRS="$DATA_DIR"

# --- SASL/PLAIN ---
if [ "$AUTH_ENABLED" = "true" ]; then
    # username/password authenticate the broker's own inter-broker client;
    # user_<name> declares the accepted credentials.
    JAAS="org.apache.kafka.common.security.plain.PlainLoginModule required username=\"${SASL_USERNAME}\" password=\"${SASL_PASSWORD}\" user_${SASL_USERNAME}=\"${SASL_PASSWORD}\";"
    export KAFKA_SASL_ENABLED_MECHANISMS="PLAIN"
    export KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL="PLAIN"
    export KAFKA_LISTENER_NAME_INTERNAL_PLAIN_SASL_JAAS_CONFIG="$JAAS"
    if [ "$EXTERNAL_ENABLED" = "true" ]; then
        export KAFKA_LISTENER_NAME_EXTERNAL_PLAIN_SASL_JAAS_CONFIG="$JAAS"
    fi
fi

# --- TLS (PEM, no keystore generation) ---
if [ "$TLS_ENABLED" = "true" ]; then
    mkdir -p "$CERTS_DIR"

    printf '%s\n' "$SSL_CA_CRT" > "$CERTS_DIR/ca.crt"
    printf '%s\n' "$SSL_TLS_KEY" > "$CERTS_DIR/keystore.pem"
    printf '%s\n' "$SSL_TLS_CRT" >> "$CERTS_DIR/keystore.pem"
    chmod 600 "$CERTS_DIR/keystore.pem"

    export KAFKA_SSL_KEYSTORE_TYPE="PEM"
    export KAFKA_SSL_KEYSTORE_LOCATION="$CERTS_DIR/keystore.pem"
    export KAFKA_SSL_TRUSTSTORE_TYPE="PEM"
    export KAFKA_SSL_TRUSTSTORE_LOCATION="$CERTS_DIR/ca.crt"
fi

# Hand off to the apache/kafka image's own startup (env vars -> server.properties)
exec /etc/kafka/docker/run
