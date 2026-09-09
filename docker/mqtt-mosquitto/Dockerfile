# Dockerfile
# Pinned: the generated config uses listener_allow_anonymous, which mosquitto
# only understands from 2.1 onwards, and it errors on unknown config variables.
# The 2.1.x line is published only under the -alpine suffix; plain `2.1.2` and
# the `2`/`2.0` floating tags still point at 2.0.22.
FROM eclipse-mosquitto:2.1.2-alpine

# yq reads the Kelvin application configuration (the base image is Alpine).
RUN apk add --no-cache yq

# Add the entrypoint script
COPY entrypoint.sh /entrypoint.sh

# Make it executable
RUN chmod +x /entrypoint.sh

# Set default entrypoint
ENTRYPOINT ["/entrypoint.sh"]
