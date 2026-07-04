#!/usr/bin/env python3
"""Skills Repository Backup Client - fetches backups from the Skills Repo server."""

import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

CONFIG_FILE = Path(__file__).parent / "backup_config.json"
DEFAULT_CONFIG = {
    "server_url": "http://localhost:8080",
    "username": "xxx",
    "password": "xxx",
    "backup_dir": "./backups",
    "retry_count": 3,
    "retain_count": 5,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def md5_file(filepath: Path) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def login(client: httpx.Client, cfg: dict) -> bool:
    url = cfg["server_url"].rstrip("/") + "/api/login"
    try:
        resp = client.post(url, data={"username": cfg["username"], "password": cfg["password"]})
        if resp.status_code == 200 and resp.json().get("ok"):
            log(f"Logged in as '{cfg['username']}'")
            return True
        log(f"Login failed: HTTP {resp.status_code}")
        return False
    except Exception as e:
        log(f"Login error: {e}")
        return False


def download_backup(client: httpx.Client, server_url: str, dest: Path) -> str:
    url = server_url.rstrip("/") + "/api/backup/skills"
    try:
        with client.stream("GET", url, timeout=300) as resp:
            if resp.status_code != 200:
                log(f"Backup download failed: HTTP {resp.status_code} - {resp.text}")
                return ""
            server_md5 = resp.headers.get("x-md5-checksum", "")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
            log(f"Downloaded {dest.stat().st_size} bytes to {dest.name}")
            return server_md5
    except Exception as e:
        log(f"Download error: {e}")
        return ""


def cleanup_old_backups(backup_dir: Path, retain_count: int):
    zips = sorted(
        (f for f in backup_dir.iterdir() if f.suffix == ".zip"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old in zips[retain_count:]:
        old.unlink()
        log(f"Removed old backup: {old.name}")


def main():
    cfg = load_config()
    backup_dir = Path(cfg["backup_dir"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    retry_count = cfg.get("retry_count", 3)
    retain_count = cfg.get("retain_count", 5)

    log("Backup client started")

    with httpx.Client() as client:
        if not login(client, cfg):
            sys.exit(1)

        for attempt in range(1, retry_count + 1):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = backup_dir / f"skills_backup_{timestamp}.zip"

            server_md5 = download_backup(client, cfg["server_url"], dest)
            if not server_md5:
                log(f"Attempt {attempt}/{retry_count} failed")
                dest.unlink(missing_ok=True)
                if attempt < retry_count:
                    time.sleep(2)
                continue

            local_md5 = md5_file(dest)
            if local_md5 == server_md5:
                log(f"Verified OK  MD5: {local_md5}")
                cleanup_old_backups(backup_dir, retain_count)
                log("Backup complete")
                return
            else:
                log(f"MD5 mismatch  server: {server_md5}  local: {local_md5}")
                dest.unlink()
                if attempt < retry_count:
                    log(f"Retrying ({attempt}/{retry_count})...")
                    time.sleep(2)

        log(f"Backup failed after {retry_count} attempts")
        sys.exit(1)


if __name__ == "__main__":
    main()
