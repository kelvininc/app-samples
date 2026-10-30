#!/bin/bash
set -euo pipefail

bootstrap=kafka-broker.app:9092
topic="motor-temperature-check-$(cat /proc/sys/kernel/random/uuid)"
kafka_bin=/opt/kafka/bin

"$kafka_bin/kafka-topics.sh" --bootstrap-server "$bootstrap" \
    --create --topic "$topic" --partitions 1 --replication-factor 1

printf '24.5\n' | "$kafka_bin/kafka-console-producer.sh" \
    --bootstrap-server "$bootstrap" --topic "$topic" \
    --command-property max.block.ms=10000 \
    --command-property request.timeout.ms=10000 \
    --command-property delivery.timeout.ms=20000

received=$("$kafka_bin/kafka-console-consumer.sh" \
    --bootstrap-server "$bootstrap" --topic "$topic" \
    --from-beginning --max-messages 1 --timeout-ms 15000)

if [ "$received" != '24.5' ]; then
    printf 'Kafka check failed: expected 24.5, received %s\n' "$received" >&2
    exit 1
fi

"$kafka_bin/kafka-topics.sh" --bootstrap-server "$bootstrap" --delete --topic "$topic"
printf 'Kafka check passed: published and received 24.5 through %s\n' "$bootstrap"
exec sleep infinity
