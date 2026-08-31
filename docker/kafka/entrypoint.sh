#!/bin/bash

# Single-node Kafka (KRaft) for Kelvin.
#
# This script translates the app's BROKER_* environment variables into the
# KAFKA_* variables the apache/kafka image natively maps onto
# server.properties, then hands off to the image's own run script. Any
# KAFKA_<PROPERTY> variable set on the deployment passes straight through to
# the broker configuration (e.g. KAFKA_LOG_RETENTION_HOURS -> log.retention.hours).
#
# Internal listener (always on):
#   BROKER_PORT                      Client listener port (default: 9092)
#   BROKER_ADVERTISED_HOST           Address advertised to clients inside the
#                                    cluster. Defaults to KELVIN_WORKLOAD_NAME
#                                    (the workload's service DNS name), then
#                                    localhost.
#
# External listener (off unless BROKER_EXTERNAL_PORT is set):
#   BROKER_EXTERNAL_PORT             Port for clients outside the cluster.
#                                    Must match a host-type port added on the
#                                    deployment (see README.md).
#   BROKER_EXTERNAL_ADVERTISED_HOST  Node address advertised to external
#                                    clients. Required with BROKER_EXTERNAL_PORT.
#
# Authentication (SASL/PLAIN on both client listeners when both are set,
# otherwise no authentication):
#   BROKER_USER, BROKER_PASSWORD
#
# TLS (enabled on both client listeners when all three are set; PEM content,
# private key must be PKCS#8):
#   BROKER_SSL_CA_CRT, BROKER_SSL_TLS_CRT, BROKER_SSL_TLS_KEY

set -e

DATA_DIR="/var/lib/kafka/data"
CERTS_DIR="/etc/kafka/secrets"

log() { echo "$(date +%s): $*"; }

# --- Internal listener ---
BROKER_PORT="${BROKER_PORT:-9092}"
case "$BROKER_PORT" in
    *[!0-9]*|'')
        log "Error: BROKER_PORT is not a valid port number."
        exit 1
        ;;
esac

ADVERTISED_HOST="${BROKER_ADVERTISED_HOST:-${KELVIN_WORKLOAD_NAME:-localhost}}"

# --- External listener ---
EXTERNAL_ENABLED="false"
if [ -n "$BROKER_EXTERNAL_PORT" ]; then
    EXTERNAL_ENABLED="true"
    case "$BROKER_EXTERNAL_PORT" in
        *[!0-9]*|'')
            log "Warning: BROKER_EXTERNAL_PORT is not a valid port number. Disabling external listener."
            EXTERNAL_ENABLED="false"
            ;;
    esac
    if [ "$EXTERNAL_ENABLED" = "true" ] && [ -z "$BROKER_EXTERNAL_ADVERTISED_HOST" ]; then
        log "Warning: BROKER_EXTERNAL_PORT is set but BROKER_EXTERNAL_ADVERTISED_HOST is not. Disabling external listener."
        EXTERNAL_ENABLED="false"
    fi
fi

# --- Authentication ---
AUTH_ENABLED="false"
if [ -n "$BROKER_USER" ] && [ -n "$BROKER_PASSWORD" ]; then
    AUTH_ENABLED="true"
fi

# --- TLS ---
TLS_ENABLED="false"
if [ -n "$BROKER_SSL_CA_CRT" ] || [ -n "$BROKER_SSL_TLS_CRT" ] || [ -n "$BROKER_SSL_TLS_KEY" ]; then
    if [ -n "$BROKER_SSL_CA_CRT" ] && [ -n "$BROKER_SSL_TLS_CRT" ] && [ -n "$BROKER_SSL_TLS_KEY" ]; then
        TLS_ENABLED="true"
    else
        log "Warning: TLS requires BROKER_SSL_CA_CRT, BROKER_SSL_TLS_CRT and BROKER_SSL_TLS_KEY. Starting without TLS."
    fi
fi

# --- Client listener security protocol ---
if [ "$AUTH_ENABLED" = "true" ] && [ "$TLS_ENABLED" = "true" ]; then
    PROTOCOL="SASL_SSL"
elif [ "$AUTH_ENABLED" = "true" ]; then
    PROTOCOL="SASL_PLAINTEXT"
elif [ "$TLS_ENABLED" = "true" ]; then
    PROTOCOL="SSL"
else
    PROTOCOL="PLAINTEXT"
fi

log "Internal listener on port: $BROKER_PORT (advertised as $ADVERTISED_HOST)"
log "External listener enabled: $EXTERNAL_ENABLED"
if [ "$EXTERNAL_ENABLED" = "true" ]; then
    log "External listener on port: $BROKER_EXTERNAL_PORT (advertised as $BROKER_EXTERNAL_ADVERTISED_HOST)"
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
    LISTENERS="${LISTENERS},EXTERNAL://0.0.0.0:${BROKER_EXTERNAL_PORT}"
    ADVERTISED="${ADVERTISED},EXTERNAL://${BROKER_EXTERNAL_ADVERTISED_HOST}:${BROKER_EXTERNAL_PORT}"
    PROTOCOL_MAP="${PROTOCOL_MAP},EXTERNAL:${PROTOCOL}"
fi

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
    JAAS="org.apache.kafka.common.security.plain.PlainLoginModule required username=\"${BROKER_USER}\" password=\"${BROKER_PASSWORD}\" user_${BROKER_USER}=\"${BROKER_PASSWORD}\";"
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

    # Secrets arrive as single-line values with literal \n sequences
    echo "$BROKER_SSL_CA_CRT" | sed 's/\\n/\'$'\n''/g' > "$CERTS_DIR/ca.crt"
    echo "$BROKER_SSL_TLS_KEY" | sed 's/\\n/\'$'\n''/g' > "$CERTS_DIR/keystore.pem"
    echo "$BROKER_SSL_TLS_CRT" | sed 's/\\n/\'$'\n''/g' >> "$CERTS_DIR/keystore.pem"
    chmod 600 "$CERTS_DIR/keystore.pem"

    export KAFKA_SSL_KEYSTORE_TYPE="PEM"
    export KAFKA_SSL_KEYSTORE_LOCATION="$CERTS_DIR/keystore.pem"
    export KAFKA_SSL_TRUSTSTORE_TYPE="PEM"
    export KAFKA_SSL_TRUSTSTORE_LOCATION="$CERTS_DIR/ca.crt"
fi

# Hand off to the apache/kafka image's own startup (env vars -> server.properties)
exec /etc/kafka/docker/run
