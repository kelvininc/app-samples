# Redis (single node)
This is a Docker application that runs a single-node Redis server with optional authentication and TLS.

It wraps the official `redis` image. The image itself takes all configuration as command-line arguments, so the entrypoint translates a small set of `REDIS_*` environment variables into arguments and hands off to the image's own startup (keeping its privilege drop to the `redis` user).

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

## Environment variables

### Base settings
- `REDIS_PORT`: Listener port (default: **6379**). Keep in sync with the service port in `app.yaml`.
- `REDIS_MAXMEMORY`: Memory cap for data (default: **256mb**). Redis is unbounded by default, which gets the workload OOM-killed under a memory limit; keep this below the workload limit with headroom (Redis needs extra during snapshots).
- `REDIS_MAXMEMORY_POLICY`: What happens at the cap (default: **noeviction**; writes fail). Use `allkeys-lru` for cache-style usage.

### Authentication (optional)
- `REDIS_PASSWORD`: When set, clients must authenticate (`AUTH <password>`). Otherwise the server accepts unauthenticated connections.

### TLS (optional)
- `REDIS_SSL_CA_CRT`: CA certificate (PEM content).
- `REDIS_SSL_TLS_CRT`: Server certificate (PEM content).
- `REDIS_SSL_TLS_KEY`: Server private key (PEM content).

When all three are set, the listener switches to TLS on `REDIS_PORT` and the plaintext listener is disabled. If only some are set, the server starts without TLS and logs a warning. Client certificates are not required by default; add `--tls-auth-clients yes` via `REDIS_EXTRA_ARGS` for mTLS.

### Persistence and advanced settings
- `REDIS_APPENDONLY`: Set to `yes` to enable AOF persistence in addition to the default RDB snapshots (better durability, more disk).
- `REDIS_EXTRA_ARGS`: Extra `redis-server` arguments appended verbatim, e.g. `--loglevel debug --save ''`. This is the escape hatch for every setting this app doesn't wrap. Don't set `--dir`; it's pinned to the persistent volume.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the container image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it. The default deployment needs nothing else. To deploy with authentication/TLS, store the sensitive values as Secrets and reference them as environment variables on the deployment (see the commented examples in `app.yaml`):

```
kelvin secret create redis-password --value "<password>"
kelvin secret create redis-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create redis-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create redis-ssl-tls-key --value "$(cat tls.key)"
```

## Persistence
Data lives on the `redis-data` persistent volume (`/data`) via RDB snapshots (default save points, plus a save on clean shutdown), and optionally AOF with `REDIS_APPENDONLY=yes`. Don't remove the volume from `app.yaml`: without it all data is lost on every restart.

## Local testing

```sh
docker build -t kelvin-redis .

# Default (no auth)
docker run --rm -p 6379:6379 kelvin-redis

# With authentication
docker run --rm -p 6379:6379 -e REDIS_PASSWORD=secret kelvin-redis
```

Verify with the CLI shipped in the image:

```sh
docker exec <container> redis-cli ping
docker exec <container> redis-cli -a secret ping   # with authentication
```

### `entrypoint.sh` script
The `entrypoint.sh` script maps the `REDIS_*` environment variables onto `redis-server` command-line arguments (port, memory cap, authentication, TLS PEM files) and then hands off to the image's own entrypoint. It can be changed to fit your needs.
