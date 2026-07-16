#!/bin/sh
set -e

# /etc/icecast.xml is a symlink into the persistent /data volume (already
# mounted by docker-compose.traefik.yml) so write_icecast_config() (backend)
# and Icecast itself share the exact same file -- admin-saved broadcast
# settings (max listeners, source slots, passwords) actually take effect on
# the next Icecast restart instead of landing in a file Icecast never reads.
#
# A real bind-mounted *file* (rather than this directory+symlink approach)
# would be simpler, but modern Docker refuses to start a container at all
# if that host file doesn't exist yet -- there's no way to seed it from
# inside the container first, since the mount fails before any entrypoint
# code runs. /data itself is a directory mount, which Docker creates fine
# on a fresh volume, so seed the file from here instead, after the
# container is already up.
# Icecast's <changeowner> config drops privileges to icecast:icecast (100:101)
# before it opens its log files -- on a brand-new volume these directories
# are created by this script as root, so without the chown below Icecast
# would fail to start with "could not open error logging: Permission denied".
mkdir -p /data/icecast /data/logs
chown icecast:icecast /data/icecast /data/logs
if [ ! -s /data/icecast/icecast.xml ]; then
    cp /etc/icecast.xml.default /data/icecast/icecast.xml
    chown icecast:icecast /data/icecast/icecast.xml
fi
ln -sf /data/icecast/icecast.xml /etc/icecast.xml

exec /entrypoint.sh "$@"
