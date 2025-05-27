#!/bin/sh

# Abort on any error
set -e

# Ensure required env vars are set
if [ -z "$MQTT_USER" ]; then
  echo "Error: MQTT_USER environment variable is not set." >&2
  exit 1
fi

if [ -z "$MQTT_PASSWORD" ]; then
  echo "Error: MQTT_PASSWORD environment variable is not set." >&2
  exit 1
fi

# Ensure the config directory exists
mkdir -p /mosquitto/config

# Create password file
mosquitto_passwd -b -c /mosquitto/config/passwordfile "$MQTT_USER" "$MQTT_PASSWORD"

#chown the password file
chown mosquitto:mosquitto /mosquitto/config/passwordfile

# Start Mosquitto
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
