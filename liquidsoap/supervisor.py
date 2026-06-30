#!/usr/bin/env python3
"""Run one Liquidsoap process per enabled station; start/stop individually on manifest changes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LIQUIDSOAP_DIR = DATA_DIR / "liquidsoap"
MANIFEST = LIQUIDSOAP_DIR / "manifest.json"
POLL_SEC = float(os.environ.get("SUPERVISOR_POLL_SEC", "3"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liquidsoap-supervisor")


class StationRunner:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}
        self.script_hashes: dict[str, str] = {}
        self._shutdown = False

    def _hash_script(self, script_rel: str) -> str | None:
        path = LIQUIDSOAP_DIR / script_rel
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _stop(self, slug: str) -> None:
        proc = self.procs.pop(slug, None)
        self.script_hashes.pop(slug, None)
        if proc and proc.poll() is None:
            log.info("Stopping station %s (pid %s)", slug, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _start(self, slug: str, script_rel: str) -> None:
        path = LIQUIDSOAP_DIR / script_rel
        script_hash = self._hash_script(script_rel)
        if script_hash is None:
            log.warning("Station %s: script missing at %s", slug, path)
            return
        log.info("Starting station %s from %s", slug, path)
        proc = subprocess.Popen(["liquidsoap", str(path)], cwd=str(DATA_DIR))
        self.procs[slug] = proc
        self.script_hashes[slug] = script_hash

    def _restart(self, slug: str, script_rel: str) -> None:
        self._stop(slug)
        self._start(slug, script_rel)

    def reconcile(self) -> None:
        if not MANIFEST.is_file():
            return
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Invalid manifest: %s", exc)
            return

        desired: dict[str, str] = {}
        for entry in manifest.get("stations") or []:
            if not entry.get("enabled"):
                continue
            slug = entry.get("slug")
            script = entry.get("script")
            if slug and script:
                desired[slug] = script

        for slug in list(self.procs):
            if slug not in desired:
                self._stop(slug)

        for slug, script_rel in desired.items():
            proc = self.procs.get(slug)
            script_hash = self._hash_script(script_rel)
            if script_hash is None:
                if proc:
                    self._stop(slug)
                continue
            if proc is None or proc.poll() is not None:
                if proc is not None and proc.poll() is not None:
                    log.warning(
                        "Station %s exited with code %s, restarting", slug, proc.returncode
                    )
                    self.procs.pop(slug, None)
                self._start(slug, script_rel)
            elif self.script_hashes.get(slug) != script_hash:
                log.info("Station %s: script changed, restarting", slug)
                self._restart(slug, script_rel)

    def run(self) -> None:
        def handle(sig: int, frame: object) -> None:
            self._shutdown = True

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)

        log.info("Liquidsoap supervisor watching %s", MANIFEST)
        while not self._shutdown:
            self.reconcile()
            deadline = time.monotonic() + POLL_SEC
            while time.monotonic() < deadline and not self._shutdown:
                time.sleep(0.1)

        for slug in list(self.procs):
            self._stop(slug)


if __name__ == "__main__":
    StationRunner().run()
