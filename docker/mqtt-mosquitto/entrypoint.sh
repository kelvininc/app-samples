#!/bin/sh
#
# Generates /mosquitto/config/mosquitto.conf from the Kelvin application
# configuration and starts the broker.
#
# Configuration is mounted at /opt/kelvin/share/config.yaml. The YAML root
# mapping is the configuration; see example-config.yaml and README.md.
#
#   listeners.plain.port            plain listener port; empty disables the listener
#   listeners.plain.auth.mode       ANONYMOUS (default) | PASSWORD
#   listeners.plain.auth.username   required when mode is PASSWORD
#   listeners.plain.auth.password   required when mode is PASSWORD
#   listeners.secure.port           TLS listener port; empty disables the listener
#   listeners.secure.auth.mode      ANONYMOUS (default) | PASSWORD
#   listeners.secure.auth.username  required when mode is PASSWORD
#   listeners.secure.auth.password  required when mode is PASSWORD
#   listeners.secure.tls.ca_crt     PEM CA bundle, required with listeners.secure.port
#   listeners.secure.tls.tls_crt    PEM server certificate, required with listeners.secure.port
#   listeners.secure.tls.tls_key    PEM server key, required with listeners.secure.port
#
# Nothing is merged into the configuration server-side, so every default below
# is applied here: a listener with no port is disabled, and a listener with no
# auth.mode is anonymous.
#
# The script can be changed to fit your needs.

# Abort on any error
set -e

# Configuration replaced the MQTT_* environment variables in 2.0.0.
for v in MQTT_PORT MQTT_USER MQTT_PASSWORD MQTT_SSL_PORT MQTT_SSL_USER MQTT_SSL_PASSWORD \
         MQTT_SSL_CA_CRT MQTT_SSL_TLS_CRT MQTT_SSL_TLS_KEY; do
    eval "legacy_value=\${$v:-}"
    if [ -n "$legacy_value" ]; then
        echo "Error: $v is set but is no longer supported in 2.0.0. Move this setting to the app configuration (see README.md)."
        exit 1
    fi
done
unset legacy_value

CFG="${KELVIN_CONFIG:-/opt/kelvin/share/config.yaml}"
cfg(){ [ -f "$CFG" ] && yq -r ".$1 // \"\"" "$CFG" 2>/dev/null || true; }

CONFIG_FOLDER="/mosquitto/config"
SSL_FOLDER="/mosquitto/certs"
DST_CONFIG_FILE="$CONFIG_FOLDER/mosquitto.conf"
PASSWORD_FILE="$CONFIG_FOLDER/passwordfile"

TIMESTAMP=$(date +%s)

# Read the configuration, applying a default to every setting.
PLAIN_PORT=$(cfg listeners.plain.port)
PLAIN_AUTH_MODE=$(cfg listeners.plain.auth.mode)
PLAIN_AUTH_MODE=${PLAIN_AUTH_MODE:-ANONYMOUS}
PLAIN_USERNAME=$(cfg listeners.plain.auth.username)
PLAIN_PASSWORD=$(cfg listeners.plain.auth.password)

SECURE_PORT=$(cfg listeners.secure.port)
SECURE_AUTH_MODE=$(cfg listeners.secure.auth.mode)
SECURE_AUTH_MODE=${SECURE_AUTH_MODE:-ANONYMOUS}
SECURE_USERNAME=$(cfg listeners.secure.auth.username)
SECURE_PASSWORD=$(cfg listeners.secure.auth.password)
SECURE_CA_CRT=$(cfg listeners.secure.tls.ca_crt)
SECURE_TLS_CRT=$(cfg listeners.secure.tls.tls_crt)
SECURE_TLS_KEY=$(cfg listeners.secure.tls.tls_key)

PLAIN_LISTENER_ENABLED="false"
SECURE_LISTENER_ENABLED="false"

# A port must be a plain number; a typo disables nothing silently.
check_port(){
    case "$2" in
        *[!0-9]*|'')
            echo "$TIMESTAMP: Error: $1 is not a valid port number: '$2'."
            exit 1
            ;;
    esac
}

check_auth_mode(){
    case "$2" in
        ANONYMOUS|PASSWORD) ;;
        *)
            echo "$TIMESTAMP: Error: $1 must be ANONYMOUS or PASSWORD, got '$2'."
            exit 1
            ;;
    esac
}

check_credentials(){
    if [ -z "$2" ] || [ -z "$3" ]; then
        echo "$TIMESTAMP: Error: $1 is PASSWORD but username or password is empty."
        exit 1
    fi
}

if [ -n "$PLAIN_PORT" ]; then
    check_port "listeners.plain.port" "$PLAIN_PORT"
    check_auth_mode "listeners.plain.auth.mode" "$PLAIN_AUTH_MODE"
    if [ "$PLAIN_AUTH_MODE" = "PASSWORD" ]; then
        check_credentials "listeners.plain.auth.mode" "$PLAIN_USERNAME" "$PLAIN_PASSWORD"
    fi
    PLAIN_LISTENER_ENABLED="true"
fi

if [ -n "$SECURE_PORT" ]; then
    check_port "listeners.secure.port" "$SECURE_PORT"
    check_auth_mode "listeners.secure.auth.mode" "$SECURE_AUTH_MODE"
    if [ "$SECURE_AUTH_MODE" = "PASSWORD" ]; then
        check_credentials "listeners.secure.auth.mode" "$SECURE_USERNAME" "$SECURE_PASSWORD"
    fi
    # Refuse to start rather than expose the secure listener unencrypted.
    if [ -z "$SECURE_CA_CRT" ] || [ -z "$SECURE_TLS_CRT" ] || [ -z "$SECURE_TLS_KEY" ]; then
        echo "$TIMESTAMP: Error: listeners.secure.port is set but listeners.secure.tls is incomplete. All of ca_crt, tls_crt and tls_key are required."
        exit 1
    fi
    SECURE_LISTENER_ENABLED="true"
fi

# Log configuration
echo "$TIMESTAMP: Plain listener enabled: $PLAIN_LISTENER_ENABLED"
if [ "$PLAIN_LISTENER_ENABLED" = "true" ]; then
    echo "$TIMESTAMP: Plain listener on port: $PLAIN_PORT"
    echo "$TIMESTAMP: Plain listener auth mode: $PLAIN_AUTH_MODE"
fi

echo "$TIMESTAMP: Secure listener enabled: $SECURE_LISTENER_ENABLED"
if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    echo "$TIMESTAMP: Secure listener on port: $SECURE_PORT"
    echo "$TIMESTAMP: Secure listener with SSL: true"
    echo "$TIMESTAMP: Secure listener auth mode: $SECURE_AUTH_MODE"
fi

# If neither plain nor secure listener is enabled, exit with error
if [ "$PLAIN_LISTENER_ENABLED" = "false" ] && [ "$SECURE_LISTENER_ENABLED" = "false" ]; then
    echo "$TIMESTAMP: Error: No MQTT listener enabled. Set listeners.plain.port, listeners.secure.port, or both."
    exit 1
fi

# Ensure the config directory exists
mkdir -p $CONFIG_FOLDER
rm -f $PASSWORD_FILE

# Credentials are broker-wide: per_listener_settings is deprecated in mosquitto
# 2.1 and removed in 3.0, so every account lives in one password file and each
# listener only decides whether anonymous clients are accepted.
PASSWORD_FILE_ENABLED="false"
add_user(){
    if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
        mosquitto_passwd -b $PASSWORD_FILE "$1" "$2"
    else
        mosquitto_passwd -b -c $PASSWORD_FILE "$1" "$2"
        PASSWORD_FILE_ENABLED="true"
    fi
}

if [ "$PLAIN_LISTENER_ENABLED" = "true" ] && [ "$PLAIN_AUTH_MODE" = "PASSWORD" ]; then
    add_user "$PLAIN_USERNAME" "$PLAIN_PASSWORD"
fi
if [ "$SECURE_LISTENER_ENABLED" = "true" ] && [ "$SECURE_AUTH_MODE" = "PASSWORD" ]; then
    add_user "$SECURE_USERNAME" "$SECURE_PASSWORD"
fi
if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
    chown mosquitto:mosquitto $PASSWORD_FILE
    chmod 600 $PASSWORD_FILE
    echo "$TIMESTAMP: Password file created at $PASSWORD_FILE"
fi

# GENERATE MOSQUITTO CONFIGURATION FILE
# Basic persistence settings
cat > $DST_CONFIG_FILE << EOF
persistence true
autosave_interval 60
persistence_location /mosquitto/data

EOF

# Global security settings, before any listener block
if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF
password_file $PASSWORD_FILE

EOF
fi

anonymous_flag(){
    if [ "$1" = "PASSWORD" ]; then echo "false"; else echo "true"; fi
}

# Plain configuration section
if [ "$PLAIN_LISTENER_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF
listener $PLAIN_PORT 0.0.0.0
max_keepalive 0
listener_allow_anonymous $(anonymous_flag "$PLAIN_AUTH_MODE")

EOF
fi

# Secure configuration section
if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    # Ensure the certs directory exists
    mkdir -p $SSL_FOLDER

    printf '%s\n' "$SECURE_CA_CRT" > $SSL_FOLDER/ca.crt
    printf '%s\n' "$SECURE_TLS_CRT" > $SSL_FOLDER/tls.crt
    printf '%s\n' "$SECURE_TLS_KEY" > $SSL_FOLDER/tls.key
    chown -R mosquitto:mosquitto $SSL_FOLDER
    chmod 600 $SSL_FOLDER/tls.key

    cat >> $DST_CONFIG_FILE << EOF
listener $SECURE_PORT 0.0.0.0
max_keepalive 0
listener_allow_anonymous $(anonymous_flag "$SECURE_AUTH_MODE")
cafile $SSL_FOLDER/ca.crt
certfile $SSL_FOLDER/tls.crt
keyfile $SSL_FOLDER/tls.key

EOF
fi

# Start Mosquitto
exec /usr/sbin/mosquitto -c $DST_CONFIG_FILE
