# MQTT Mosquitto with optional SSL
This is a Docker application that runs an MQTT Mosquitto broker with SSL (optional).

By default the app deploys with a single plain listener on port **21883** and anonymous access; no secrets or extra configuration required. Authentication and the SSL listener are opt-in through the application configuration (commented examples in `app.yaml`, reference below).

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
2. **Deploy** it. The default deployment needs nothing else. To enable authentication or SSL, store the sensitive values as Secrets and reference them from the configuration.

```
kelvin secret create mqtt-user --value "<username>"
kelvin secret create mqtt-password --value "<password>"
kelvin secret create mqtt-ssl-user --value "<ssl_username>"
kelvin secret create mqtt-ssl-password --value "<ssl_password>"
kelvin secret create mqtt-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create mqtt-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create mqtt-ssl-tls-key --value "$(cat tls.key)"
```

## Configuration
Kelvin mounts the deployment configuration at `/opt/kelvin/share/config.yaml`; the YAML root mapping is the configuration. The deploy UI renders it from `ui_schemas/configuration.json`.

```yaml
listeners:
  plain:                                        # plain (unencrypted) listener
    port: 21883                                 # omit to disable this listener
    auth:
      mode: PASSWORD                            # ANONYMOUS (default) | PASSWORD
      username: "<% secrets.mqtt-user %>"       # required when mode is PASSWORD
      password: "<% secrets.mqtt-password %>"   # required when mode is PASSWORD
  secure:                                       # TLS listener
    port: 28883                                 # omit to disable this listener
    auth:
      mode: PASSWORD                            # ANONYMOUS (default) | PASSWORD
      username: "<% secrets.mqtt-ssl-user %>"
      password: "<% secrets.mqtt-ssl-password %>"
    tls:                                        # all three required with secure.port
      ca_crt: "<% secrets.mqtt-ssl-ca-crt %>"
      tls_crt: "<% secrets.mqtt-ssl-tls-crt %>"
      tls_key: "<% secrets.mqtt-ssl-tls-key %>"
```

**Notes:**
- Enable at least one listener. With no port on either, the broker exits with an error.
- A listener with no `port` is disabled. A `port` that is not a number is an error, not a silently dropped listener.
- `auth.mode` defaults to `ANONYMOUS`. With `PASSWORD`, both `username` and `password` are required.
- `listeners.secure.mode` is `DISABLED` (default), `ANON_TLS` or `AUTH_TLS`. Either enabled mode requires the port and all three certificates. The broker refuses to start if any is missing; it never falls back to an unencrypted secure listener.
- Credentials are broker-wide. Mosquitto's `per_listener_settings` is deprecated in 2.1 and removed in 3.0, so accounts from both listeners share one password file and each listener only decides whether anonymous clients are accepted. A user configured on one listener can authenticate on the other.
- Keep the ports in sync with the `service` (and any `host`) ports declared under `system.ports`.

## Local testing
```
docker build -t mqtt-mosquitto:local .
docker run --rm -p 21883:21883 \
  -v "$PWD/example-config.yaml:/opt/kelvin/share/config.yaml" \
  mqtt-mosquitto:local
```

`example-config.yaml` starts an anonymous plain listener on 21883 and carries commented blocks for password auth and TLS.

## Upgrading from 1.x
Version 2.0.0 replaces the `MQTT_*` environment variables with the application configuration. There is no fallback: if any of `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_SSL_PORT`, `MQTT_SSL_USER`, `MQTT_SSL_PASSWORD`, `MQTT_SSL_CA_CRT`, `MQTT_SSL_TLS_CRT` or `MQTT_SSL_TLS_KEY` is still set, the container names it and exits non-zero rather than starting a broker that quietly lost its authentication.

To upgrade, drop `environment_vars` from the deployment and move each value into `configuration`:

| 1.x environment variable | 2.0.0 configuration key |
| --- | --- |
| `MQTT_PORT` | `listeners.plain.port` |
| `MQTT_USER` | `listeners.plain.auth.username` (with `auth.mode: PASSWORD`) |
| `MQTT_PASSWORD` | `listeners.plain.auth.password` (with `auth.mode: PASSWORD`) |
| `MQTT_SSL_PORT` | `listeners.secure.port` (with `mode: ANON_TLS` or `AUTH_TLS`) |
| `MQTT_SSL_USER` | `listeners.secure.username` (with `mode: AUTH_TLS`) |
| `MQTT_SSL_PASSWORD` | `listeners.secure.password` (with `mode: AUTH_TLS`) |
| `MQTT_SSL_CA_CRT` | `listeners.secure.tls.ca_crt` |
| `MQTT_SSL_TLS_CRT` | `listeners.secure.tls.tls_crt` |
| `MQTT_SSL_TLS_KEY` | `listeners.secure.tls.tls_key` |

Also in 2.0.0:
- The example secret names lose the `mqttssl-` prefix: `mqttssl-user` becomes `mqtt-user`, `mqttssl-ssl-ca-crt` becomes `mqtt-ssl-ca-crt`, and so on. Existing secrets keep working; only the names in `app.yaml` and this README changed.
- A secure listener with incomplete certificates is an error. In 1.x it started unencrypted.
- The generated `mosquitto.conf` uses `listener_allow_anonymous` instead of `per_listener_settings` plus `allow_anonymous`, so the deprecation warnings on every start are gone.

### `entrypoint.sh` script
The `entrypoint.sh` script reads the configuration with `yq`, generates the Mosquitto configuration file, and starts the broker. It validates the configuration first and exits with an error message when a listener is misconfigured or when no listener is enabled.

The script can be changed to fit your needs.
