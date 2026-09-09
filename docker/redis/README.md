# Redis (single node)
This is a Docker application that runs a single-node Redis server with optional authentication and TLS.

It wraps the official `redis` image. The image itself takes all configuration as command-line arguments, so the entrypoint reads the Kelvin app configuration mounted at `/opt/kelvin/share/config.yaml`, translates it into arguments and hands off to the image's own startup (keeping its privilege drop to the `redis` user).

By default the app deploys with no authentication and no TLS; no secrets or extra configuration required. Hardening is opt-in per deployment.

## Prerequisites
This is a Docker application; it has no Python.
1. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
2. Docker, to build and upload the container image to Kelvin Cloud.

## Connecting
Other workloads on the same cluster reach the server at `<workload-name>:6379` (the cluster service declared in `app.yaml`). Redis has no advertised-address mechanism, so no addressing configuration is needed. The server is not reachable from outside the cluster by default.

### External access (optional)
To accept clients from outside the cluster, add a host-type port on the deployment (or uncomment the example in `app.yaml`):

```yaml
ports:
  - name: redis-external
    type: host
    host:
      port: 6379
```

Clients then connect to `<node-address>:6379`. Enable authentication (and preferably TLS) before exposing the server externally.

## Configuration
Everything is set through the app configuration (`ui_schemas/configuration.json` drives the deploy form; `defaults.configuration` in `app.yaml` holds the defaults). There are no environment variables.

```yaml
port: 6379                         # listener port; keep in sync with the service port
memory:
  maxmemory: 256mb                 # data memory cap
  maxmemory_policy: noeviction     # behaviour at the cap
persistence:
  appendonly: false                # true adds AOF on top of the RDB snapshots
security:
  mode: PLAINTEXT                  # PLAINTEXT | PASSWORD | TLS | PASSWORD_TLS
  password: "<% secrets.redis-password %>"       # PASSWORD, PASSWORD_TLS
  tls:                                           # TLS, PASSWORD_TLS
    ca_crt: "<% secrets.redis-ssl-ca-crt %>"
    tls_crt: "<% secrets.redis-ssl-tls-crt %>"
    tls_key: "<% secrets.redis-ssl-tls-key %>"
extra_args: ""                     # extra redis-server arguments, appended verbatim
```

### Base settings
- `port`: Listener port (default: **6379**). Keep in sync with the service port in `app.yaml`.
- `memory.maxmemory`: Memory cap for data (default: **256mb**). Redis is unbounded by default, which gets the workload OOM-killed under a memory limit; keep this below the workload limit with headroom (Redis needs extra during snapshots).
- `memory.maxmemory_policy`: What happens at the cap (default: **noeviction**; writes fail). Use `allkeys-lru` for cache-style usage. Also accepts `allkeys-lfu`, `volatile-lru` and `volatile-ttl`.

### Security
`security.mode` picks one of four shapes, and the deploy form shows only the fields that mode needs:

- `PLAINTEXT` (default): no authentication, no encryption.
- `PASSWORD`: `security.password` is required; clients must send `AUTH <password>`.
- `TLS`: all three PEMs under `security.tls` are required.
- `PASSWORD_TLS`: password and all three PEMs.

TLS is exclusive: the plaintext listener is disabled and `port` becomes the TLS port. Client certificates are not required by default; add `--tls-auth-clients yes` via `extra_args` for mTLS.

A half-configured mode is a startup error, not a warning: if the mode asks for a password or certificates and any of them is empty, the container exits with a message instead of starting an unauthenticated or unencrypted server.

### Persistence and advanced settings
- `persistence.appendonly`: Set to `true` to enable AOF persistence in addition to the default RDB snapshots (better durability, more disk).
- `extra_args`: Extra `redis-server` arguments appended verbatim, e.g. `--loglevel debug --save ''`. This is the escape hatch for every setting this app doesn't wrap. Don't set `--dir`; it's pinned to the persistent volume.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the container image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it. The default deployment needs nothing else. To deploy with authentication/TLS, store the sensitive values as Secrets and reference them from the configuration (see the commented examples in `app.yaml`):

```
kelvin secret create redis-password --value "<password>"
kelvin secret create redis-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create redis-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create redis-ssl-tls-key --value "$(cat tls.key)"
```

## Persistence
Data lives on the `redis-data` persistent volume (`/data`) via RDB snapshots (default save points, plus a save on clean shutdown), and optionally AOF with `persistence.appendonly: true`. Don't remove the volume from `app.yaml`: without it all data is lost on every restart.

## Local testing
`example-config.yaml` is a working configuration; mount it where the platform mounts the real one.

```sh
docker build -t kelvin-redis .

docker run --rm -p 6379:6379 \
  -v "$PWD/example-config.yaml:/opt/kelvin/share/config.yaml" kelvin-redis
```

Copy it and edit the copy to try other modes, e.g. `security.mode: PASSWORD` with `security.password: secret`.

Verify with the CLI shipped in the image:

```sh
docker exec <container> redis-cli ping
docker exec <container> redis-cli -a secret ping   # with authentication
```

### `entrypoint.sh` script
The `entrypoint.sh` script reads the configuration with `yq` and maps it onto `redis-server` command-line arguments (port, memory cap, authentication, TLS PEM files) and then hands off to the image's own entrypoint. It can be changed to fit your needs.
