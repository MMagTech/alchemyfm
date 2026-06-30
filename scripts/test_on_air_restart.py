"""Poll station on_air while restarting Icecast (run from host)."""
import subprocess
import sys
import time

import httpx

SLUG = sys.argv[1] if len(sys.argv) > 1 else "yacht"
API = "http://localhost:8080"


def station_state() -> tuple[bool | None, int]:
    r = httpx.get(f"{API}/api/stations/{SLUG}", timeout=10)
    r.raise_for_status()
    data = r.json()
    on_air = data.get("on_air")
    return on_air, int(data.get("listeners") or 0)


def main() -> None:
    print("=== before restart ===")
    for _ in range(3):
        on_air, listeners = station_state()
        print(f"  on_air={on_air!r} listeners={listeners}")
        time.sleep(0.5)

    print("=== restarting icecast ===")
    subprocess.run(
        ["docker", "compose", "restart", "icecast"],
        check=True,
        cwd=r"C:\Users\marcu\OneDrive\Desktop\audiomuse-radio",
    )

    print("=== after restart (20s) ===")
    saw_off = False
    saw_on = False
    for i in range(20):
        time.sleep(1)
        try:
            on_air, listeners = station_state()
        except Exception as exc:
            print(f"  t+{i+1}s error: {exc}")
            continue
        print(f"  t+{i+1}s on_air={on_air!r} listeners={listeners}")
        if on_air is False:
            saw_off = True
        if on_air is True:
            saw_on = True

    print("=== summary ===")
    print(f"  saw off air: {saw_off}")
    print(f"  saw back on air: {saw_on}")
    if not saw_off:
        print("  WARN: on_air never went false during restart window")


if __name__ == "__main__":
    main()
