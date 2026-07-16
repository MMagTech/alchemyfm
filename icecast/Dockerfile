FROM infiniteproject/icecast:latest

# The image's entrypoint.sh loads /etc/icecast.xml (and sed-patches
# ICECAST_* env vars into it), not /etc/icecast2/icecast.xml -- copying to
# the old path meant every custom setting here was silently ignored in
# favor of the base image's own default config.
#
# Baked in as .default rather than the live path: seed-config.sh symlinks
# /etc/icecast.xml into the persistent /data volume (already mounted by
# docker-compose.traefik.yml) so the backend's write_icecast_config() writes
# the exact file Icecast reads, seeding it from this default on first boot
# when the volume is empty and the backend hasn't run write_icecast_config()
# yet. A direct bind mount of /etc/icecast.xml itself won't work here: Docker
# refuses to start the container at all if that host file doesn't already
# exist, so there'd be no way to seed it from inside the container first.
COPY icecast.xml /etc/icecast.xml.default
COPY seed-config.sh /seed-config.sh
RUN chmod +x /seed-config.sh

ENTRYPOINT ["/seed-config.sh"]
CMD ["icecast", "-c", "/etc/icecast.xml"]
