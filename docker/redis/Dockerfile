# Dockerfile
FROM redis:8.8.0

# yq reads the Kelvin app configuration YAML in the entrypoint
RUN apt-get update \
    && apt-get install -y --no-install-recommends yq \
    && rm -rf /var/lib/apt/lists/*

# Add the entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Make it executable
RUN chmod +x /entrypoint.sh

# Translate the Kelvin configuration into redis-server arguments, then start the server
ENTRYPOINT ["/entrypoint.sh"]
