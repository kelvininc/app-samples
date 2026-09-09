# Kafka Broker (single node)
This is a Docker application that runs a single-node Apache Kafka broker in KRaft mode (no ZooKeeper), with optional SASL/PLAIN authentication and TLS.

It wraps the official `apache/kafka` image: the entrypoint reads the Kelvin application configuration and translates it into the `KAFKA_*` variables the image maps onto `server.properties`. The image falls back to its own `server.properties` only when it receives no configuration at all, so the entrypoint declares the whole single-node baseline itself: combined broker+controller, one KRaft voter on `localhost:9093`, replication factor 1 for the internal topics, and the log directory pinned to the persistent volume. The cluster id is the one constant the image still supplies.

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

## Configuration
Everything an operator sets lives in the application configuration, which Kelvin mounts at `/opt/kelvin/share/config.yaml`. Edit it in the Deploy Workload form, or write it under `runtime.configuration` in a runtime file:

```yaml
runtime:
  configuration:
    listener:
      port: 9092
    security:
      protocol: PLAINTEXT
```

Keys the deployment omits fall back to the entrypoint's defaults. `defaults.configuration` in `app.yaml` documents the full structure.

### `listener` (always enabled)
- `listener.port`: Client listener port (default: **9092**). Keep in sync with the service port in `app.yaml`.
- `listener.advertised_host`: Address advertised to in-cluster clients. Empty (the default) uses the workload name; only override for non-standard setups.

### `external` (optional)
The broker is not reachable from outside the cluster by default. Kafka clients bootstrap and then reconnect to the address the broker advertises, so external access needs its own listener with its own advertised address. On the deployment:

1. Add a host-type port (there's a commented example in `app.yaml`):

   ```yaml
   ports:
     - name: kafka-external
       type: host
       host:
         port: 9094
   ```

2. Set both external keys:
   - `external.port`: Enables the external listener; must match the host port (**9094**).
   - `external.advertised_host`: Address external clients can reach the node on.

Both or neither: the schema enforces it at deploy time and the entrypoint exits with an error if only one is set, rather than starting a broker whose external listener nobody can use. `external.port` also has to differ from `listener.port`; sharing one would otherwise surface as a bare bind failure.

To find the node address, open **Cluster -> Services** in the Kelvin UI after the host port is deployed; the service entry for `kafka-external` lists the node address the host port is published on.

External clients then connect to `<node-address>:9094`. Enable authentication (and preferably TLS) before exposing the broker externally.

### `security`
`security.protocol` picks one of four modes, and each mode requires its own credentials:

| `security.protocol` | Also required |
| --- | --- |
| `PLAINTEXT` (default) | nothing |
| `SSL` | `security.tls.ca_crt`, `security.tls.tls_crt`, `security.tls.tls_key` |
| `SASL_PLAINTEXT` | `security.sasl.username`, `security.sasl.password` |
| `SASL_SSL` | both blocks |

The protocol applies to every client listener; the controller listener stays plaintext on localhost, where it never leaves the container.

`SSL` and `SASL_SSL` encrypt the connection; only the `SASL_*` protocols authenticate the client. Client certificates are not required by default, so a plain `SSL` broker accepts any client that trusts the CA chain. For mTLS, add `extra_properties: {ssl.client.auth: required}` (`requested` makes the certificate optional).

- `security.sasl.username` / `security.sasl.password`: the single SASL/PLAIN credential pair the broker accepts. Both end up in the broker's JAAS login string, which is quoted and line-oriented, so the password can't contain `"`, `\` or a newline and the username is limited to letters, digits, `.`, `_`, `@` and `-` (it is also used verbatim as the `user_<name>` JAAS option). The schema rejects anything else at deploy time and the entrypoint refuses to start on it.
- `security.tls.ca_crt`: CA certificate (PEM content).
- `security.tls.tls_crt`: Server certificate (PEM content).
- `security.tls.tls_key`: Server private key (PEM content, **PKCS#8 format**).

A `*_SSL` protocol with a missing or empty certificate field makes the broker exit instead of falling back to an unencrypted listener, so a typo can't quietly leave the broker open. The reverse is an error too: certificates under a non-TLS protocol, or credentials under a non-SASL one, stop the broker rather than starting it with the material silently ignored.

**Note:** Kafka only accepts PEM private keys in PKCS#8 format (`-----BEGIN PRIVATE KEY-----`). Convert a PKCS#1 key (`-----BEGIN RSA PRIVATE KEY-----`) with:

```
openssl pkcs8 -topk8 -nocrypt -in tls-pkcs1.key -out tls.key
```

### `jvm` and `extra_properties`
- `jvm.heap_opts`: JVM heap (default: `-Xmx512m -Xms512m`). Size the workload's memory limit above the heap (1 GB is a comfortable floor).
- `extra_properties`: a map of `server.properties` entries applied verbatim, for example:

  ```yaml
  extra_properties:
    log.retention.hours: "48"
    num.partitions: "3"
  ```

  This is the escape hatch for every broker setting this app doesn't wrap; `ssl.client.auth: required` for mTLS goes here. The settings the entrypoint manages (listeners, protocol map, SASL, TLS, log dirs, the KRaft baseline) win on conflict; don't set `log.dirs`, it's pinned to the persistent volume.

  Property names take letters, digits, `.` and `_` (they become `KAFKA_*` environment variables), and values are single-line: `server.properties` is line-oriented. The entrypoint exits with the offending key named rather than passing either through.

## Kelvin Cloud Deployment
1. **Upload** the application (builds and registers the container image; needs Docker):
    ```
    kelvin app upload
    ```
2. **Deploy** it. The default deployment needs nothing else. To deploy with authentication/TLS, store the sensitive values as Secrets and reference them from the configuration:

```
kelvin secret create kafka-user --value "<username>"
kelvin secret create kafka-password --value "<password>"
kelvin secret create kafka-ssl-ca-crt --value "$(cat ca.crt)"
kelvin secret create kafka-ssl-tls-crt --value "$(cat tls.crt)"
kelvin secret create kafka-ssl-tls-key --value "$(cat tls.key)"
```

`<% secrets.<name> %>` references resolve inside the configuration, so a SASL_SSL deployment looks like:

```yaml
runtime:
  configuration:
    security:
      protocol: SASL_SSL
      sasl:
        username: "<% secrets.kafka-user %>"
        password: "<% secrets.kafka-password %>"
      tls:
        ca_crt: "<% secrets.kafka-ssl-ca-crt %>"
        tls_crt: "<% secrets.kafka-ssl-tls-crt %>"
        tls_key: "<% secrets.kafka-ssl-tls-key %>"
```

The credential and certificate fields already default to these secret names in the Deploy Workload form.

## Persistence
Topic data and KRaft metadata live on the `kafka-data` persistent volume (`/var/lib/kafka/data`). Don't remove the volume from `app.yaml`: without it all topics and consumer offsets are lost on every restart.

## Local testing
Mount a config file where the platform mounts it. `example-config.yaml` is a working starting point:

```sh
docker build -t kelvin-kafka .

docker run --rm -p 9092:9092 \
    -v "$PWD/example-config.yaml:/opt/kelvin/share/config.yaml" \
    kelvin-kafka
```

With no `KELVIN_WORKLOAD_NAME` in the environment, the empty `listener.advertised_host` falls through to `localhost`, so the broker advertises an address your host can reach. To test authentication or TLS, uncomment the `sasl` or `tls` block in `example-config.yaml` and set `security.protocol` to match.

Verify with the client tools shipped in the image:

```sh
docker exec <container> /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --topic smoke
docker exec <container> /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

### `entrypoint.sh` script
The `entrypoint.sh` script reads `/opt/kelvin/share/config.yaml` with `yq` and maps it onto the `KAFKA_*` variables the official image understands (listeners, advertised listeners, security protocol, SASL/JAAS, PEM TLS files) and then hands off to the image's own startup script. It can be changed to fit your needs.
