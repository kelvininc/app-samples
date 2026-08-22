#!/bin/sh

# Settings for insecure settings (non-SSL):
#    If MQTT_PORT not defined or empty, the insecure listener will be disabled
#    Environment variables:
#      MQTT_PORT
#
#    If both MQTT_USER and MQTT_PASSWORD are not defined or empty,
#    anonymous access will be allowed
#    Environment variables:
#      MQTT_USER, MQTT_PASSWORD

# Settings for secure settings (SSL)
#    If MQTT_SSL_PORT not defined or empty, the secure listener will be disabled
#    Environment variables:
#      MQTT_SSL_PORT
#
#    If both MQTT_SSL_USER and MQTT_SSL_PASSWORD are not defined or empty,
#    anonymous access will be allowed
#    Environment variables:
#     MQTT_SSL_USER, MQTT_SSL_PASSWORD
#
#    SSL certificates must be provided for the secure listener to use SSL
#    If any of the SSL certificate variables are not defined or empty,
#    the secure listener will be started without SSL
#    Environment variables:
#      MQTT_SSL_CA_CRT, MQTT_SSL_TLS_CRT, MQTT_SSL_TLS_KEY

# Abort on any error
set -e

INSECURE_LISTENER_ENABLED="false"
INSECURE_ANONYMOUS="false"

SECURE_LISTENER_ENABLED="false"
SECURE_ANONYMOUS="false"
SECURE_WITH_SSL="true"

CONFIG_FOLDER="/mosquitto/config"
SSL_FOLDER="/mosquitto/certs"
DST_CONFIG_FILE="$CONFIG_FOLDER/mosquitto.conf"

TIMESTAMP=$(date +%s)

# Check if MQTT_PORT is defined
if [ ! -z "$MQTT_PORT" ]; then
    INSECURE_LISTENER_ENABLED="true"
fi

# If MQTT_PORT is defined but not a valid port number, disable insecure listener
if [ "$INSECURE_LISTENER_ENABLED" = "true" ]; then
    case "$MQTT_PORT" in
        *[!0-9]*|'')
            echo "$TIMESTAMP: Warning: MQTT_PORT is not a valid port number. Disabling insecure listener."
            INSECURE_LISTENER_ENABLED="false"
            ;;
    esac
fi

# Check if MQTT_SSL_PORT is defined
if [ ! -z "$MQTT_SSL_PORT" ]; then
    SECURE_LISTENER_ENABLED="true"
    if [ -z "$MQTT_SSL_CA_CRT" ] || [ -z "$MQTT_SSL_TLS_CRT" ] || [ -z "$MQTT_SSL_TLS_KEY" ]; then
        echo "$TIMESTAMP: Warning: SSL listener enabled but SSL certificates are not provided. Starting secure listener without SSL."
        SECURE_WITH_SSL="false"
    fi
fi

# Check if both MQTT_USER and MQTT_PASSWORD are defined. If not, allow anonymous access
if [ -z "$MQTT_USER" ] && [ -z "$MQTT_PASSWORD" ]; then
  INSECURE_ANONYMOUS="true"
fi

# Log configuration
echo "$TIMESTAMP: Insecure listener enabled: $INSECURE_LISTENER_ENABLED"
if [ "$INSECURE_LISTENER_ENABLED" = "true" ]; then
    echo "$TIMESTAMP: Insecure listener on port: $MQTT_PORT"
    echo "$TIMESTAMP: Insecure Anonymous access: $INSECURE_ANONYMOUS"
fi

if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    case "$MQTT_SSL_PORT" in
        *[!0-9]*|'')
            echo "$TIMESTAMP: Warning: MQTT_SSL_PORT is not a valid port number. Disabling secure listener."
            SECURE_LISTENER_ENABLED="false"
            SECURE_WITH_SSL="false"
            ;;
    esac
fi

# Check if both MQTT_SSL_USER and MQTT_SSL_PASSWORD are defined. If not, allow anonymous access
if [ -z "$MQTT_SSL_USER" ] && [ -z "$MQTT_SSL_PASSWORD" ]; then
  SECURE_ANONYMOUS="true"
fi

# Log configuration
echo "$TIMESTAMP: Secure listener enabled: $SECURE_LISTENER_ENABLED"
if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    echo "$TIMESTAMP: Secure listener on port: $MQTT_SSL_PORT"
    echo "$TIMESTAMP: Secure listener with SSL: $SECURE_WITH_SSL"
    echo "$TIMESTAMP: Secure anonymous access: $SECURE_ANONYMOUS"
fi


# If neither insecure nor secure listener is enabled, exit with error
if [ "$INSECURE_LISTENER_ENABLED" = "false" ] && [ "$SECURE_LISTENER_ENABLED" = "false" ]; then
    echo "$TIMESTAMP: Warning: No MQTT listener enabled."
    exit 1
fi

# Ensure the config directory exists
mkdir -p $CONFIG_FOLDER

# GENERATE MOSQUITTO CONFIGURATION FILE
# Basic persistence settings
cat > $DST_CONFIG_FILE << EOF
persistence true
autosave_interval 60
persistence_location /mosquitto/data

EOF

# Enable per listener settings
cat >> $DST_CONFIG_FILE << EOF
per_listener_settings true

EOF

# Insecure configuration section
if [ "$INSECURE_LISTENER_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF
listener $MQTT_PORT 0.0.0.0
max_keepalive 0
EOF

    if [ "$INSECURE_ANONYMOUS" = "true" ]; then
        cat >> $DST_CONFIG_FILE << EOF
allow_anonymous true
EOF
    else
        mosquitto_passwd -b -c $CONFIG_FOLDER/passwordfile "$MQTT_USER" "$MQTT_PASSWORD"
        chown mosquitto:mosquitto $CONFIG_FOLDER/passwordfile
        cat >> $DST_CONFIG_FILE << EOF
allow_anonymous false
password_file $CONFIG_FOLDER/passwordfile
EOF
    fi
fi

# Secure configuration section
if [ "$SECURE_LISTENER_ENABLED" = "true" ]; then
    cat >> $DST_CONFIG_FILE << EOF

listener $MQTT_SSL_PORT 0.0.0.0
max_keepalive 0
EOF

    if [ "$SECURE_ANONYMOUS" = "true" ]; then
        cat >> $DST_CONFIG_FILE << EOF
allow_anonymous true
EOF
    else
        mosquitto_passwd -b -c $CONFIG_FOLDER/sslpasswordfile "$MQTT_SSL_USER" "$MQTT_SSL_PASSWORD"
        chown mosquitto:mosquitto $CONFIG_FOLDER/sslpasswordfile
        cat >> $DST_CONFIG_FILE << EOF
allow_anonymous false
password_file $CONFIG_FOLDER/sslpasswordfile
EOF
    fi

    if [ "$SECURE_WITH_SSL" = "true" ]; then
        # Ensure the certs directory exists
        mkdir -p $SSL_FOLDER

        echo "$MQTT_SSL_CA_CRT" | sed 's/\\n/\'$'\n''/g' > $SSL_FOLDER/ca.crt
        echo "$MQTT_SSL_TLS_CRT" | sed 's/\\n/\'$'\n''/g' > $SSL_FOLDER/tls.crt
        echo "$MQTT_SSL_TLS_KEY" | sed 's/\\n/\'$'\n''/g' > $SSL_FOLDER/tls.key
        chown -R mosquitto:mosquitto $SSL_FOLDER
        chmod 600 $SSL_FOLDER/tls.key

        echo "" >> $DST_CONFIG_FILE
        echo "cafile $SSL_FOLDER/ca.crt" >> $DST_CONFIG_FILE
        echo "certfile $SSL_FOLDER/tls.crt" >> $DST_CONFIG_FILE
        echo "keyfile $SSL_FOLDER/tls.key" >> $DST_CONFIG_FILE
    fi
fi

# Start Mosquitto
exec /usr/sbin/mosquitto -c $DST_CONFIG_FILE