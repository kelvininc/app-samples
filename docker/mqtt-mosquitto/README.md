# MQTT Mosquitto with optional SSL
This is a Docker application that runs an MQTT Mosquitto broker with SSL (optional).

By default the app deploys with a single insecure listener on port **21883** and anonymous access; no secrets or extra configuration required. Authentication and the SSL listener are opt-in: add the corresponding environment variables on the deployment (commented examples in `app.yaml`, reference below).

## Connecting
Other workloads on the same cluster reach the broker at `<workload-name>:21883` (the cluster service declared in `app.yaml`). The broker is not reachable from outside the cluster by default.

### External access (optional)
To accept clients from outside the cluster, add a host-type port on the deployment (or uncomment the example in `app.yaml`):

```yaml
ports:
  - name: mqtt-insecure
    type: host
    host:
      port: 21883
```

Clients then connect to `<node-address>:21883`. Add the equivalent for `28883` if the SSL listener is enabled. Enable authentication (and preferably SSL) before exposing the broker externally.

## Prerequisites
This is a Docker application (a Mosquitto broker plus an `entrypoint.sh`); it has no Python.
1. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
2. Docker, to build and upload the container image to Kelvin Cloud.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the container image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it. The default deployment needs nothing else. To enable authentication or SSL, store the sensitive values as Secrets and reference them as environment variables on the deployment.

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
- `MQTT_SSL_PORT`: The SSL port for the MQTT broker (not set by default; use **28883** to match the port exposed in `app.yaml`).
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