# Dockerfile
FROM redis:8.8.0

# Add the entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Make it executable
RUN chmod +x /entrypoint.sh

# Translate REDIS_* env vars into redis-server arguments, then start the server
ENTRYPOINT ["/entrypoint.sh"]
