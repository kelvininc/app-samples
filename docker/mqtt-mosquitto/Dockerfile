# Dockerfile
FROM eclipse-mosquitto:latest

# yq reads the Kelvin application configuration (the base image is Alpine).
RUN apk add --no-cache yq

# Add the entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Make it executable
RUN chmod +x /entrypoint.sh

# Set default entrypoint
ENTRYPOINT ["/entrypoint.sh"]
