FROM infiniteproject/icecast:latest

# The image's entrypoint.sh loads /etc/icecast.xml (and sed-patches
# ICECAST_* env vars into it), not /etc/icecast2/icecast.xml -- copying to
# the old path meant every custom setting here was silently ignored in
# favor of the base image's own default config.
COPY icecast.xml /etc/icecast.xml
