# Dockerfile
FROM eclipse-mosquitto:latest

# Add the entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Make it executable
RUN chmod +x /entrypoint.sh

# Copy the mosquitto configuration file
COPY mosquitto.conf /mosquitto/config/mosquitto.conf

# Set default entrypoint
ENTRYPOINT ["/entrypoint.sh"]