# MQTT Mosquitto with optional SSL
This is a Docker application that runs an MQTT Mosquitto broker with SSL (optional).

## Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Docker (optional) for upload the application to a Kelvin Instance.

## Kelvin Cloud Deployment
To deploy this application to a cluster using the Kelvin Cloud you need to setup the environment variables as Secrets.

**Note:** These secrets are not required, but are a safer way to store sensitive information that can later be referenced as environment variables in the deployment process.

```
kelvin secret create mqttssl-user --value "<username>"
kelvin secret create mqttssl-password --value "<password>"
kelvin secret create mqttssl-ssl-user --value "<ssl_username>"
kelvin secret create mqttssl-ssl-password --value "<ssl_password>"
kelvin secret create mqttssl-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create mqttssl-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create mqttssl-ssl-tls-key --value "$(cat tls.key)"
```

## How to setup the MQTT Mosquitto broker using environment variables
You can configure the MQTT Mosquitto broker using the following environment variables:

### Insecure MQTT (without SSL):
- `MQTT_PORT`: The port for the MQTT broker (default: **21883**).
- `MQTT_USER`: The username for the MQTT broker.
- `MQTT_PASSWORD`: The password for the MQTT broker.

**Notes:**
- If `MQTT_PORT` is not defined or invalid, the broker will not start the insecure MQTT listener.
- If you set the `MQTT_USER` and `MQTT_PASSWORD` environment variables, the broker will require authentication for connections. Otherwise, it will allow anonymous connections.

### Secure MQTT (with SSL):
- `MQTT_SSL_PORT`: The SSL port for the MQTT broker (default: **28883**).
- `MQTT_SSL_USER`: The username for the MQTT broker with SSL.
- `MQTT_SSL_PASSWORD`: The password for the MQTT broker with SSL.
- `MQTT_SSL_CA_CRT`: The CA certificate for SSL.
- `MQTT_SSL_TLS_CRT`: The TLS certificate for SSL.
- `MQTT_SSL_TLS_KEY`: The TLS key for SSL.

**Notes:**
- If `MQTT_SSL_PORT` is not defined or invalid, the broker will not start the secure (SSL) MQTT listener.
- If you set the `MQTT_SSL_USER` and `MQTT_SSL_PASSWORD` environment variables, the broker will require authentication for SSL connections. Otherwise, it will allow anonymous SSL connections.
- The `MQTT_SSL_CA_CRT`, `MQTT_SSL_TLS_CRT`, and `MQTT_SSL_TLS_KEY` environment variables are required for SSL to work. Otherwise, the broker will listen without SSL.

### `entrypoint.sh` script
The `entrypoint.sh` script is responsible for generating the Mosquitto configuration file based on the provided environment variables and starting the Mosquitto broker. It checks for the presence of the necessary environment variables and configures the broker accordingly. If neither insecure nor secure MQTT is configured, the script will exit with an error message.

The script can be changed to fit your needs.