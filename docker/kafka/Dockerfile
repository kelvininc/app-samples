# Dockerfile
# JVM image (not apache/kafka-native): SASL is unsupported on the native image.
FROM apache/kafka:4.3.1

# Add the entrypoint script
COPY --chown=appuser:appuser entrypoint.sh /entrypoint.sh

USER root
RUN chmod +x /entrypoint.sh
USER appuser

# Translate BROKER_* env vars into Kafka settings, then start the broker
ENTRYPOINT ["/entrypoint.sh"]
