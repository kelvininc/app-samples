# Kafka Broker Check

Supporting Docker Workload for a broker deployed on the same Cluster. Build this directory and deploy separately after the broker is available at kafka-broker.app:9092. check-broker.sh creates a unique topic, sends and reads 24.5, and deletes that topic after success. It leaves the container running for log inspection.

This does not test TLS or authentication. Use an isolated test broker; the checker creates and deletes its own topic. Failed checks can leave their unique topic for diagnosis.
