# Kafka Broker (single node)
This is a Docker application that runs a single-node Apache Kafka broker in KRaft mode (no ZooKeeper), with optional SASL/PLAIN authentication and TLS.

It wraps the official `apache/kafka` image: the entrypoint translates a small set of `BROKER_*` environment variables into broker settings, and everything else keeps the image's sensible single-node defaults (combined broker+controller, replication factor 1, fixed cluster id).

## Prerequisites
This is a Docker application; it has no Python.
1. Install the Kelvin CLI (needed for `kelvin app upload`): `pip3 install kelvin-sdk`.
2. Docker, to build and upload the container image to Kelvin Cloud.

## Connecting from other workloads
The `app.yaml` declares a cluster service on port **9092**. Other workloads on the same cluster reach the broker at:

```
<workload-name>:9092
```

The broker advertises this address automatically: the entrypoint defaults its advertised host to the injected `KELVIN_WORKLOAD_NAME`, which equals the service DNS name. No addressing configuration is needed for in-cluster clients.

## Environment variables

### Internal listener (always enabled)
- `BROKER_PORT`: Client listener port (default: **9092**). Keep in sync with the service port in `app.yaml`.
- `BROKER_ADVERTISED_HOST`: Address advertised to in-cluster clients. Defaults to the workload name; only override for non-standard setups.

### External listener (optional)
The broker is not reachable from outside the cluster by default. Kafka clients bootstrap and then reconnect to the address the broker advertises, so external access needs its own listener with its own advertised address. On the deployment:

1. Add a host-type port (there's a commented example in `app.yaml`):

   ```yaml
   ports:
     - name: kafka-external
       type: host
       host:
         port: 9094
   ```

2. Set the environment variables:
   - `BROKER_EXTERNAL_PORT`: Enables the external listener; must match the host port (**9094**).
   - `BROKER_EXTERNAL_ADVERTISED_HOST`: Address external clients can reach the node on. Required when the external port is set; the listener is disabled with a warning otherwise.

External clients then connect to `<node-address>:9094`. Enable authentication (and preferably TLS) before exposing the broker externally.

### Authentication (optional)
- `BROKER_USER` / `BROKER_PASSWORD`: When both are set, all client listeners require SASL/PLAIN authentication with these credentials. Otherwise the broker accepts unauthenticated connections.

### TLS (optional)
- `BROKER_SSL_CA_CRT`: CA certificate (PEM content).
- `BROKER_SSL_TLS_CRT`: Server certificate (PEM content).
- `BROKER_SSL_TLS_KEY`: Server private key (PEM content, **PKCS#8 format**).

When all three are set, the client listeners switch to TLS (`SSL`, or `SASL_SSL` when combined with authentication). If only some are set, the broker starts without TLS and logs a warning.

**Note:** Kafka only accepts PEM private keys in PKCS#8 format (`-----BEGIN PRIVATE KEY-----`). Convert a PKCS#1 key (`-----BEGIN RSA PRIVATE KEY-----`) with:

```
openssl pkcs8 -topk8 -nocrypt -in tls-pkcs1.key -out tls.key
```

### Sizing and advanced settings
- `KAFKA_HEAP_OPTS`: JVM heap (default: `-Xmx512m -Xms512m`). Size the workload's memory limit above the heap (1 GB is a comfortable floor).
- Any `KAFKA_<PROPERTY>` environment variable maps directly to a `server.properties` entry (dots become underscores, uppercased). For example `KAFKA_LOG_RETENTION_HOURS=48` sets `log.retention.hours=48`. This is the escape hatch for every broker setting this app doesn't wrap. Don't set `KAFKA_LOG_DIRS`; it's pinned to the persistent volume.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the container image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it. The default deployment needs nothing else. To deploy with authentication/TLS, store the sensitive values as Secrets and reference them as environment variables on the deployment:

```
kelvin secret create kafka-user --value "<username>"
kelvin secret create kafka-password --value "<password>"
kelvin secret create kafka-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create kafka-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create kafka-ssl-tls-key --value "$(cat tls.key)"
```

Then add the corresponding variables (see the commented examples in `app.yaml`), e.g. `BROKER_USER` = `<% secrets.kafka-user %>`.

## Persistence
Topic data and KRaft metadata live on the `kafka-data` persistent volume (`/var/lib/kafka/data`). Don't remove the volume from `app.yaml`: without it all topics and consumer offsets are lost on every restart.

## Local testing

```sh
docker build -t kelvin-kafka .

# Plaintext
docker run --rm -p 9092:9092 -e BROKER_ADVERTISED_HOST=localhost kelvin-kafka

# With authentication
docker run --rm -p 9092:9092 \
    -e BROKER_ADVERTISED_HOST=localhost \
    -e BROKER_USER=admin -e BROKER_PASSWORD=secret \
    kelvin-kafka
```

Verify with the client tools shipped in the image:

```sh
docker exec <container> /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --topic smoke
docker exec <container> /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

### `entrypoint.sh` script
The `entrypoint.sh` script maps the `BROKER_*` environment variables onto the `KAFKA_*` variables the official image understands (listeners, advertised listeners, security protocol, SASL/JAAS, PEM TLS files) and then hands off to the image's own startup script. It can be changed to fit your needs.
