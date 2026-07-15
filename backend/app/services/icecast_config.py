"""Write Icecast server config from broadcast settings."""

from pathlib import Path

from app.config import settings


def icecast_config_paths() -> list[Path]:
    """Paths to write — data volume + optional Docker bind mount for Icecast."""
    paths = [Path(settings.data_dir) / "icecast" / "icecast.xml"]
    host_bind = Path("/host-icecast/icecast.xml")
    if host_bind.parent.is_dir():
        paths.append(host_bind)
    return paths


def write_icecast_config(max_listeners: int, source_slots: int = 10) -> Path:
    password = settings.icecast_source_password.replace("&", "&amp;")
    sources = max(2, source_slots)
    content = f"""<icecast>
    <location>Earth</location>
    <admin>admin@localhost</admin>
    <limits>
        <clients>{max_listeners}</clients>
        <sources>{sources}</sources>
        <queue-size>1048576</queue-size>
        <burst-on-connect>1</burst-on-connect>
        <burst-size>65535</burst-size>
    </limits>
    <authentication>
        <source-password>{password}</source-password>
        <relay-password>{password}</relay-password>
        <admin-user>admin</admin-user>
        <admin-password>{password}</admin-password>
    </authentication>
    <hostname>localhost</hostname>
    <listen-socket>
        <port>8000</port>
    </listen-socket>
    <mount type="normal">
        <mount-name>/stream</mount-name>
        <charset>UTF-8</charset>
    </mount>
    <fileserve>1</fileserve>
    <paths>
        <basedir>/usr/share/icecast</basedir>
        <logdir>/data/logs</logdir>
        <webroot>/usr/share/icecast/web</webroot>
        <adminroot>/usr/share/icecast/admin</adminroot>
        <alias source="/" destination="/status.xsl"/>
    </paths>
    <logging>
        <accesslog>access.log</accesslog>
        <errorlog>error.log</errorlog>
        <loglevel>3</loglevel>
        <logsize>10000</logsize>
    </logging>
    <security>
        <chroot>0</chroot>
        <changeowner>
            <user>icecast</user>
            <group>icecast</group>
        </changeowner>
    </security>
</icecast>
"""
    primary = icecast_config_paths()[0]
    for path in icecast_config_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return primary


def encode_format_liquidsoap(bs) -> str:
    if bs.encode_format == "aac":
        # AAC-LC over ADTS via fdk-aac -- decodes natively on iOS/Safari,
        # unlike Ogg Vorbis which has no decoder anywhere in WebKit.
        return (
            f"%fdkaac(channels=2, samplerate={bs.sample_rate}, "
            f"bitrate={bs.aac_bitrate})"
        )
    return f"%mp3(bitrate={bs.mp3_bitrate}, samplerate={bs.sample_rate})"


def stream_media_type(encode_format: str) -> str:
    if encode_format == "aac":
        return "audio/aac"
    return "audio/mpeg"
