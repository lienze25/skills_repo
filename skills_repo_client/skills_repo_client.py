#!/usr/bin/env python3
"""skills-repo CLI — interact with a Skills Repository server.

Usage:
  python skills_repo_client.py config --url <url> --username <u> --password <p>
  python skills_repo_client.py login
  python skills_repo_client.py list [--tag t] [--q keyword] [--json]
  python skills_repo_client.py get <id> [--json]
  python skills_repo_client.py upload --id <id> --file <path> --desc <d> --tags <a,b>
  python skills_repo_client.py upload --id <id> --dir <path> --desc <d> --tags <a,b>
  python skills_repo_client.py download <id> [--output path.zip]
  python skills_repo_client.py download-all [--output path.zip]
  python skills_repo_client.py delete <id>
  python skills_repo_client.py tags <id> --set "a,b,c"
  python skills_repo_client.py tags --list

Config priority: CLI args > env vars > ~/.skills_repo_config
Env vars: SKILLS_REPO_URL, SKILLS_REPO_USERNAME, SKILLS_REPO_PASSWORD, SKILLS_REPO_TOKEN
"""

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path.home() / ".skills_repo_config"
EPILOG = "Config file: ~/.skills_repo_config      Priority: CLI > env > config file"
CLIENT_VERSION = "1.0.0"
_version_checked = False
_version_compatible = False


def resolve_config(args) -> dict:
    """Load config: defaults → file → env → CLI args."""
    cfg = {"url": "", "username": "", "password": "", "token": ""}

    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            pass

    for key, env in [("url", "SKILLS_REPO_URL"), ("username", "SKILLS_REPO_USERNAME"),
                     ("password", "SKILLS_REPO_PASSWORD"), ("token", "SKILLS_REPO_TOKEN")]:
        if os.environ.get(env):
            cfg[key] = os.environ[env]

    for key in ("url", "username", "password", "token"):
        val = getattr(args, key, None)
        if val is not None:
            cfg[key] = val

    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def build_request(cfg: dict, method: str, path: str, data=None, headers=None) -> urllib.request.Request:
    url = cfg["url"].rstrip("/") + path
    hdrs = headers or {}
    if cfg.get("token"):
        hdrs["Cookie"] = f"session={cfg['token']}"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    return req


def _check_server_version(cfg: dict):
    """Fetch server config and verify client/server version compatibility."""
    global _version_checked, _version_compatible
    if _version_checked:
        return _version_compatible
    _version_checked = True
    try:
        req = urllib.request.Request(cfg["url"].rstrip("/") + "/api/config")
        with urllib.request.urlopen(req, timeout=5) as resp:
            server_cfg = json.loads(resp.read())
    except Exception:
        print("Warning: Could not verify server version", file=sys.stderr)
        _version_compatible = True
        return True
    server_ver = server_cfg.get("server_version", "")
    if not server_ver:
        _version_compatible = True
        return True
    client_major = CLIENT_VERSION.split(".")[0]
    server_major = server_ver.split(".")[0]
    if client_major != server_major:
        print(f"Version mismatch: client={CLIENT_VERSION} server={server_ver}", file=sys.stderr)
        print("Please update the client or use a compatible server.", file=sys.stderr)
        sys.exit(1)
    _version_compatible = True
    return True


def call_api(cfg: dict, method: str, path: str, data=None, headers=None, raw=False):
    """Return parsed JSON or raw bytes. Auto-login if 401."""
    _check_server_version(cfg)
    req = build_request(cfg, method, path, data, headers)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            if raw:
                return body
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and cfg.get("username") and cfg.get("password"):
            do_login(cfg)
            return call_api(cfg, method, path, data, headers, raw)
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        print(f"Error {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def do_login(cfg: dict):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/api/login",
        data=urllib.parse.urlencode({"username": cfg["username"], "password": cfg["password"]}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    try:
        with opener.open(req):
            pass
        for cookie in cj:
            if cookie.name == "session":
                cfg["token"] = cookie.value
                save_config(cfg)
                return
    except urllib.error.HTTPError as e:
        detail = "Unknown error"
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        print(f"Login failed ({e.code}): {detail}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_config(args):
    cfg = resolve_config(args)
    for key in ("url", "username", "password"):
        val = getattr(args, key, None)
        if val is not None:
            cfg[key] = val
    save_config(cfg)
    print(f"Config saved to {CONFIG_PATH}")
    for k, v in cfg.items():
        if k == "password":
            v = "***" if v else ""
        print(f"  {k}: {v}")


def cmd_login(args):
    cfg = resolve_config(args)
    if not cfg["url"]:
        print("Error: URL not configured. Run 'config --url ...' first.", file=sys.stderr)
        sys.exit(1)
    if not cfg["username"] or not cfg["password"]:
        print("Error: username/password not configured.", file=sys.stderr)
        sys.exit(1)
    do_login(cfg)
    print(f"Logged in. Token cached in {CONFIG_PATH}")


def cmd_list(args):
    cfg = resolve_config(args)
    params = []
    if getattr(args, "q", None):
        params.append(f"q={urllib.parse.quote(args.q)}")
    if getattr(args, "tag", None):
        params.append(f"tag={urllib.parse.quote(args.tag)}")
    path = "/api/skills"
    if params:
        path += "?" + "&".join(params)
    result = call_api(cfg, "GET", path)
    if getattr(args, "json", None):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'ID':<30s} {'Name':<25s} {'Downloads':>9s}  Tags")
        print("-" * 90)
        for s in result:
            tags = ", ".join(s.get("tags", []))
            print(f"{s['id']:<30s} {s.get('name','')[:25]:<25s} {s.get('downloads',0):>8d}  {tags}")
        print(f"\n{len(result)} skill(s)")


def cmd_get(args):
    cfg = resolve_config(args)
    result = call_api(cfg, "GET", f"/api/skills/{args.id}")
    if getattr(args, "json", None):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ID:          {result['id']}")
        print(f"Name:        {result.get('name','')}")
        print(f"Description: {result.get('description','')}")
        print(f"Uploader:    {result.get('uploader','')}")
        print(f"Downloads:   {result.get('downloads',0)}")
        print(f"Tags:        {', '.join(result.get('tags',[])) or '—'}")
        print(f"Uploaded:    {result.get('uploaded_at','')}")
        print(f"\n--- Content ---")
        print(result.get('content', ''))


def cmd_upload(args):
    cfg = resolve_config(args)
    import mimetypes

    boundary = "----FormBoundary" + os.urandom(16).hex()
    body_parts = []

    # Files
    if args.file:
        fpath = Path(args.file)
        if not fpath.exists():
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        body_parts.append(_make_form_part(boundary, "files", fpath.read_bytes(), fpath.name))
    elif args.dir:
        dpath = Path(args.dir)
        if not dpath.is_dir():
            print(f"Directory not found: {args.dir}", file=sys.stderr)
            sys.exit(1)
        for fp in sorted(dpath.rglob("*")):
            if fp.is_file():
                rel = str(fp.relative_to(dpath))
                body_parts.append(_make_form_part(boundary, "files", fp.read_bytes(), rel))
        if not body_parts:
            print("Empty directory", file=sys.stderr)
            sys.exit(1)
    else:
        print("Must specify --file or --dir", file=sys.stderr)
        sys.exit(1)

    # Form fields
    body_parts.append(_make_form_part(boundary, "skill_id", args.id.encode(), None))
    body_parts.append(_make_form_part(boundary, "description", args.desc.encode(), None))
    body_parts.append(_make_form_part(boundary, "tags", args.tags.encode(), None))

    body = b"".join(body_parts) + f"--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    result = call_api(cfg, "POST", "/api/skills/upload", data=body, headers=headers)
    print(f"Uploaded: {result['id']} ({result.get('name','')})")


def _make_form_part(boundary: str, name: str, data: bytes, filename: str | None) -> bytes:
    header = f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
    if filename:
        header += f"; filename=\"{filename}\""
    header += "\r\n"
    content_type = ""
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in (".md", ".mdc"):
            content_type = "Content-Type: text/markdown\r\n"
        elif ext in (".py",):
            content_type = "Content-Type: text/x-python\r\n"
    return (header + content_type + "\r\n").encode() + data + b"\r\n"


def cmd_download(args):
    cfg = resolve_config(args)
    data = call_api(cfg, "GET", f"/api/skills/{args.id}/download", raw=True)
    assert isinstance(data, bytes)
    output = args.output or f"{args.id}.zip"
    Path(output).write_bytes(data)
    print(f"Downloaded to {output} ({len(data)} bytes)")


def cmd_download_all(args):
    cfg = resolve_config(args)
    data = call_api(cfg, "GET", "/api/skills/download/all", raw=True)
    output = args.output or "skills_all.zip"
    Path(output).write_bytes(data)
    print(f"Downloaded to {output} ({len(data)} bytes)")


def cmd_delete(args):
    cfg = resolve_config(args)
    result = call_api(cfg, "DELETE", f"/api/skills/{args.id}")
    print(f"Deleted: {result.get('deleted', args.id)}")


def cmd_tags(args):
    cfg = resolve_config(args)
    if getattr(args, "list", None):
        result = call_api(cfg, "GET", "/api/tags")
        print("\n".join(result))
    elif getattr(args, "set", None):
        tags = [t.strip() for t in args.set.split(",") if t.strip()]
        body = json.dumps(tags).encode()
        result = call_api(cfg, "PUT", f"/api/skills/{args.id}/tags",
                          data=body, headers={"Content-Type": "application/json"})
        print(f"Tags updated: {', '.join(result.get('tags', tags))}")
    else:
        print("Use --set or --list", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="Skills Repository CLI", epilog=EPILOG)
    sp = p.add_subparsers(dest="command")

    # config
    c = sp.add_parser("config", help="Set server URL, username, password")
    c.add_argument("--url", help="Server URL")
    c.add_argument("--username", help="Username")
    c.add_argument("--password", help="Password")

    # login
    sp.add_parser("login", help="Login and cache session token")

    # list
    lst = sp.add_parser("list", help="List/search skills")
    lst.add_argument("--tag", help="Filter by tag")
    lst.add_argument("--q", help="Search keyword")
    lst.add_argument("--json", action="store_true", help="JSON output")

    # get
    g = sp.add_parser("get", help="Get skill detail")
    g.add_argument("id", help="Skill ID")
    g.add_argument("--json", action="store_true", help="JSON output")

    # upload
    up = sp.add_parser("upload", help="Upload a skill")
    up.add_argument("--id", required=True, help="Skill ID")
    up.add_argument("--file", help="Single .md/.mdc file")
    up.add_argument("--dir", help="Folder containing SKILL.md")
    up.add_argument("--desc", required=True, help="Description")
    up.add_argument("--tags", required=True, help="Comma-separated tags")

    # download
    dl = sp.add_parser("download", help="Download a skill")
    dl.add_argument("id", help="Skill ID")
    dl.add_argument("--output", help="Output file path")

    # download-all
    da = sp.add_parser("download-all", help="Download all skills")
    da.add_argument("--output", help="Output file path")

    # delete
    d = sp.add_parser("delete", help="Delete a skill")
    d.add_argument("id", help="Skill ID")

    # tags
    tags = sp.add_parser("tags", help="Manage tags")
    tags.add_argument("id", nargs="?", help="Skill ID (ignored with --list)")
    tags.add_argument("--set", help="Set tags (comma-separated)")
    tags.add_argument("--list", action="store_true", help="List all used tags")

    # Global flags
    p.add_argument("--url", help="Server URL")
    p.add_argument("--username", help="Username")
    p.add_argument("--password", help="Password")
    p.add_argument("--token", help="Session token (skip login)")

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(0)

    import urllib.parse  # needed for cmd_list

    cmds = {
        "config": cmd_config, "login": cmd_login, "list": cmd_list,
        "get": cmd_get, "upload": cmd_upload, "download": cmd_download,
        "download-all": cmd_download_all, "delete": cmd_delete, "tags": cmd_tags,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
