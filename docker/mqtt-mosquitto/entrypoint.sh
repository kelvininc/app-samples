#!/bin/sh
#
# Generates /mosquitto/config/mosquitto.conf from the Kelvin application
# configuration and starts the broker.
#
# Configuration is mounted at /opt/kelvin/share/config.yaml. The YAML root
# mapping is the configuration; see example-config.yaml and README.md.
#
#   listeners.plain.mode            DISABLED (default) | ANONYMOUS | PASSWORD
#   listeners.plain.port            required unless mode is DISABLED
#   listeners.plain.username        required when mode is PASSWORD
#   listeners.plain.password        required when mode is PASSWORD
#   listeners.secure.mode           DISABLED (default) | ANON_TLS | AUTH_TLS
#   listeners.secure.port           required unless mode is DISABLED
#   listeners.secure.username       required when mode is AUTH_TLS
#   listeners.secure.password       required when mode is AUTH_TLS
#   listeners.secure.tls.ca_crt     PEM CA bundle, required unless mode is DISABLED
#   listeners.secure.tls.tls_crt    PEM server certificate, required unless mode is DISABLED
#   listeners.secure.tls.tls_key    PEM server key, required unless mode is DISABLED
#
# Nothing is merged into the configuration server-side, so every default below
# is applied here: both listeners default to DISABLED, and at least one of them
# must be enabled or the broker exits. Settings the selected mode ignores are an
# error, not a no-op.
#
# The script can be changed to fit your needs.

# Abort on any error
set -e

log() { echo "$(date +%s): $*"; }
die() { log "Error: $*"; exit 1; }

# Certificates and password files are written below. Create them owner-only
# rather than chmod-ing after the fact, which leaves a readable window.
umask 077

# Configuration replaced the MQTT_* environment variables in 2.0.0.
for v in MQTT_PORT MQTT_USER MQTT_PASSWORD MQTT_SSL_PORT MQTT_SSL_USER MQTT_SSL_PASSWORD \
         MQTT_SSL_CA_CRT MQTT_SSL_TLS_CRT MQTT_SSL_TLS_KEY; do
    eval "legacy_value=\${$v:-}"
    if [ -n "$legacy_value" ]; then
        die "$v is set but is no longer supported in 2.0.0. Move this setting to the app configuration (see README.md)."
    fi
done
unset legacy_value

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

CONFIG_FOLDER="/mosquitto/config"
DATA_FOLDER="/mosquitto/data"
SSL_FOLDER="/mosquitto/certs"
DST_CONFIG_FILE="$CONFIG_FOLDER/mosquitto.conf"
PASSWORD_FILE="$CONFIG_FOLDER/passwordfile"

# Read the configuration, applying a default to every setting.
PLAIN_MODE=$(cfg listeners.plain.mode)
PLAIN_MODE=${PLAIN_MODE:-DISABLED}
PLAIN_PORT=$(cfg listeners.plain.port)
PLAIN_USERNAME=$(cfg listeners.plain.username)
PLAIN_PASSWORD=$(cfg listeners.plain.password)

SECURE_MODE=$(cfg listeners.secure.mode)
SECURE_MODE=${SECURE_MODE:-DISABLED}
SECURE_PORT=$(cfg listeners.secure.port)
SECURE_USERNAME=$(cfg listeners.secure.username)
SECURE_PASSWORD=$(cfg listeners.secure.password)
SECURE_CA_CRT=$(cfg listeners.secure.tls.ca_crt)
SECURE_TLS_CRT=$(cfg listeners.secure.tls.tls_crt)
SECURE_TLS_KEY=$(cfg listeners.secure.tls.tls_key)

PLAIN_LISTENER_ENABLED="false"
SECURE_LISTENER_ENABLED="false"

check_port() {  # $1 = setting name, $2 = value
    case "$2" in
        ''|*[!0-9]*) die "$1 is not a valid port number: '$2'." ;;
    esac
    [ "$2" -ge 1 ] && [ "$2" -le 65535 ] || die "$1 must be 1-65535, got '$2'."
}

# Security material the selected mode ignores is a configuration error: starting
# anyway would silently drop the protection the operator asked for.
UNUSED=""
note_unused() {  # $1 = setting name, $2 = value
    [ -n "$2" ] && UNUSED="$UNUSED $1"
    return 0
}

MISSING=""
note_missing() {  # $1 = setting name, $2 = value
    [ -z "$2" ] && MISSING="$MISSING $1"
    return 0
}

case "$PLAIN_MODE" in
    DISABLED)
        UNUSED=""
        note_unused listeners.plain.port "$PLAIN_PORT"
        note_unused listeners.plain.username "$PLAIN_USERNAME"
        note_unused listeners.plain.password "$PLAIN_PASSWORD"
        [ -z "$UNUSED" ] || die "listeners.plain.mode is DISABLED but these are set:$UNUSED. Remove them or enable the listener."
        ;;
    ANONYMOUS)
        check_port "listeners.plain.port" "$PLAIN_PORT"
        UNUSED=""
        note_unused listeners.plain.username "$PLAIN_USERNAME"
        note_unused listeners.plain.password "$PLAIN_PASSWORD"
        [ -z "$UNUSED" ] || die "listeners.plain.mode is ANONYMOUS but these are set:$UNUSED. Set listeners.plain.mode to PASSWORD to require them."
        PLAIN_LISTENER_ENABLED="true"
        ;;
    PASSWORD)
        check_port "listeners.plain.port" "$PLAIN_PORT"
        MISSING=""
        note_missing listeners.plain.username "$PLAIN_USERNAME"
        note_missing listeners.plain.password "$PLAIN_PASSWORD"
        [ -z "$MISSING" ] || die "listeners.plain.mode is PASSWORD but these are empty:$MISSING."
        PLAIN_LISTENER_ENABLED="true"
        ;;
    *)
        die "listeners.plain.mode must be DISABLED, ANONYMOUS or PASSWORD, got '$PLAIN_MODE'."
        ;;
esac

case "$SECURE_MODE" in
    DISABLED)
        UNUSED=""
        note_unused listeners.secure.port "$SECURE_PORT"
        note_unused listeners.secure.username "$SECURE_USERNAME"
        note_unused listeners.secure.password "$SECURE_PASSWORD"
        note_unused listeners.secure.tls.ca_crt "$SECURE_CA_CRT"
        note_unused listeners.secure.tls.tls_crt "$SECURE_TLS_CRT"
        note_unused listeners.secure.tls.tls_key "$SECURE_TLS_KEY"
        [ -z "$UNUSED" ] || die "listeners.secure.mode is DISABLED but these are set:$UNUSED. Remove them or set listeners.secure.mode to ANON_TLS or AUTH_TLS."
        ;;
    ANON_TLS|AUTH_TLS)
        check_port "listeners.secure.port" "$SECURE_PORT"
        if [ "$SECURE_MODE" = "AUTH_TLS" ]; then
            MISSING=""
            note_missing listeners.secure.username "$SECURE_USERNAME"
            note_missing listeners.secure.password "$SECURE_PASSWORD"
            [ -z "$MISSING" ] || die "listeners.secure.mode is AUTH_TLS but these are empty:$MISSING."
        else
            UNUSED=""
            note_unused listeners.secure.username "$SECURE_USERNAME"
            note_unused listeners.secure.password "$SECURE_PASSWORD"
            [ -z "$UNUSED" ] || die "listeners.secure.mode is ANON_TLS but these are set:$UNUSED. Set listeners.secure.mode to AUTH_TLS to require them."
        fi
        # Refuse to start rather than expose the secure listener unencrypted.
        MISSING=""
        note_missing listeners.secure.tls.ca_crt "$SECURE_CA_CRT"
        note_missing listeners.secure.tls.tls_crt "$SECURE_TLS_CRT"
        note_missing listeners.secure.tls.tls_key "$SECURE_TLS_KEY"
        [ -z "$MISSING" ] || die "listeners.secure.mode is $SECURE_MODE but these are empty:$MISSING. All of ca_crt, tls_crt and tls_key are required."
        SECURE_LISTENER_ENABLED="true"
        ;;
    *)
        die "listeners.secure.mode must be DISABLED, ANON_TLS or AUTH_TLS, got '$SECURE_MODE'."
        ;;
esac

# Two listeners on one port fail at bind time with no hint at the cause.
if [ "$PLAIN_LISTENER_ENABLED" = "true" ] && [ "$SECURE_LISTENER_ENABLED" = "true" ] \
   && [ "$PLAIN_PORT" = "$SECURE_PORT" ]; then
    die "listeners.plain.port and listeners.secure.port are both $PLAIN_PORT. Give each listener its own port."
fi

# One broker-wide password file means mosquitto_passwd replaces, rather than
# adds, a second account with the same name: the plain listener's password would
# stop working with no error.
if [ "$PLAIN_MODE" = "PASSWORD" ] && [ "$SECURE_MODE" = "AUTH_TLS" ] \
   && [ "$PLAIN_LISTENER_ENABLED" = "true" ] && [ "$SECURE_LISTENER_ENABLED" = "true" ] \
   && [ "$PLAIN_USERNAME" = "$SECURE_USERNAME" ]; then
    die "listeners.plain.username and listeners.secure.username are both '$PLAIN_USERNAME'. Accounts are broker-wide, so the two listeners need distinct usernames."
fi

# Log configuration
log "Plain listener enabled: $PLAIN_LISTENER_ENABLED"
if [ "$PLAIN_LISTENER_ENABLED" = "true" ]; then
    log "Plain listener on port: $PLAIN_PORT"
    log "Plain listener mode: $PLAIN_MODE"
fi

log "Secure listener enabled: $SECURE_LISTENER_ENABLED"
if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    log "Secure listener on port: $SECURE_PORT"
    log "Secure listener with SSL: true"
    log "Secure listener mode: $SECURE_MODE"
fi

# If neither plain nor secure listener is enabled, exit with error. No schema can
# express "at least one of two sibling objects is enabled", so it is checked here.
if [ "$PLAIN_LISTENER_ENABLED" = "false" ] && [ "$SECURE_LISTENER_ENABLED" = "false" ]; then
    die "No MQTT listener enabled. Set listeners.plain.mode to ANONYMOUS/PASSWORD, or listeners.secure.mode to ANON_TLS/AUTH_TLS, or both."
fi

# Ensure the config directory exists
mkdir -p $CONFIG_FOLDER
rm -f $PASSWORD_FILE

# Overriding ENTRYPOINT skips the stock image's `chown -R mosquitto /mosquitto`,
# and the broker drops to the mosquitto user before writing the persistence DB.
mkdir -p $DATA_FOLDER
chown -R mosquitto:mosquitto $DATA_FOLDER

# Credentials are broker-wide: per_listener_settings is deprecated in mosquitto
# 2.1 and removed in 3.0, so every account lives in one password file and each
# listener only decides whether anonymous clients are accepted.
PASSWORD_FILE_ENABLED="false"
add_user() {
    if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
        mosquitto_passwd -b $PASSWORD_FILE "$1" "$2"
    else
        mosquitto_passwd -b -c $PASSWORD_FILE "$1" "$2"
        PASSWORD_FILE_ENABLED="true"
    fi
}

if [ "$PLAIN_LISTENER_ENABLED" = "true" ] && [ "$PLAIN_MODE" = "PASSWORD" ]; then
    add_user "$PLAIN_USERNAME" "$PLAIN_PASSWORD"
fi
if [ "$SECURE_LISTENER_ENABLED" = "true" ] && [ "$SECURE_MODE" = "AUTH_TLS" ]; then
    add_user "$SECURE_USERNAME" "$SECURE_PASSWORD"
fi
if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
    chown mosquitto:mosquitto $PASSWORD_FILE
    log "Password file created at $PASSWORD_FILE"
fi

# GENERATE MOSQUITTO CONFIGURATION FILE
# Basic persistence settings
cat > $DST_CONFIG_FILE << EOF
persistence true
autosave_interval 60
persistence_location $DATA_FOLDER

EOF

# Global security settings, before any listener block
if [ "$PASSWORD_FILE_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF
password_file $PASSWORD_FILE

EOF
fi

# Plain uses PASSWORD; secure uses AUTH_TLS. Both mean "credentials required".
anonymous_flag() {
    case "$1" in
        PASSWORD|AUTH_TLS) echo "false" ;;
        *)                 echo "true"  ;;
    esac
}

# Plain configuration section
if [ "$PLAIN_LISTENER_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF
listener $PLAIN_PORT 0.0.0.0
max_keepalive 0
listener_allow_anonymous $(anonymous_flag "$PLAIN_MODE")

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

    cat >> $DST_CONFIG_FILE << EOF
listener $SECURE_PORT 0.0.0.0
max_keepalive 0
listener_allow_anonymous $(anonymous_flag "$SECURE_MODE")
cafile $SSL_FOLDER/ca.crt
certfile $SSL_FOLDER/tls.crt
keyfile $SSL_FOLDER/tls.key

EOF
fi

# Start Mosquitto
exec /usr/sbin/mosquitto -c $DST_CONFIG_FILE
