import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import Body, Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

app = FastAPI(title="Skills Repository")

web_dir = Path("web")
web_dir.mkdir(exist_ok=True)
APP_HTML = web_dir / "application.html"
LOGIN_HTML = web_dir / "login.html"

USERS_FILE = Path("users.json")
SESSION_SECRET = secrets.token_hex(32)
SESSION_MAX_AGE = timedelta(hours=24)

CONFIG_FILE = Path("config.json")
SERVER_VERSION = "1.0.0"

DEFAULT_CONFIG = {"host": "0.0.0.0", "port": 8080, "auth_enabled": True, "default_user": "admin",
                   "llm_api_url": "", "llm_api_key": "", "llm_model": "", "skills_dir": "skills"}


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


_config = _load_config()
SKILLS_DIR = Path(os.path.expanduser(_config.get("skills_dir", "skills")))
SKILLS_DIR.mkdir(exist_ok=True)
SKILLS_INDEX_FILE = SKILLS_DIR / "skills_list.json"
_index_lock = threading.RLock()


SUPPORTED_EXTENSIONS = {".md", ".mdc"}


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return base64.b64encode(h).decode(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    h, _ = _hash_password(password, salt)
    return h == stored_hash


def _create_session_token(username: str) -> str:
    expires = (datetime.now(timezone.utc) + SESSION_MAX_AGE).isoformat()
    payload = f"{username}:{expires}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(token.encode()).decode()


def _verify_session_token(token: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
    except Exception:
        return None
    parts = raw.rsplit(":", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    username, expires_str = payload.split(":", 1)
    expires = datetime.fromisoformat(expires_str)
    if datetime.now(timezone.utc) > expires:
        return None
    return username


def _require_auth_api(session: Optional[str] = Cookie(None)) -> str:
    if not session:
        raise HTTPException(status_code=401, detail="Not logged in")
    username = _verify_session_token(session)
    if not username:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return username


def require_admin(session: Optional[str] = Cookie(None)) -> str:
    username = _require_auth_api(session)
    users = _load_users()
    if users.get(username, {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return username


def optional_auth(session: Optional[str] = Cookie(None)) -> str:
    cfg = _load_config()
    if not cfg["auth_enabled"]:
        return cfg["default_user"]
    return _require_auth_api(session)


def optional_admin(session: Optional[str] = Cookie(None)) -> str:
    cfg = _load_config()
    if not cfg["auth_enabled"]:
        return cfg["default_user"]
    return require_admin(session)


@app.get("/api/config")
def api_config():
    cfg = _load_config()
    cfg["server_version"] = SERVER_VERSION
    return cfg


@app.put("/api/config")
async def api_update_config(body: dict, _: str = Depends(optional_admin)):
    cfg = _load_config()
    for key in ("auth_enabled", "llm_api_url", "llm_api_key", "llm_model"):
        if key in body:
            cfg[key] = body[key]
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _check_operation_permission(username: str, permission_key: str):
    """Check if user has permission for an operation. Returns if allowed, raises 403 if not."""
    cfg = _load_config()
    if not cfg["auth_enabled"]:
        return
    users = _load_users()
    user = users.get(username, {})
    if user.get("role") == "admin":
        return
    if not user.get(permission_key, True):
        raise HTTPException(status_code=403, detail="You don't have permission for this operation")


def parse_skill_md(content: str) -> dict:
    """Parse SKILL.md: frontmatter name/desc take priority, body headings as fallback."""
    name = ""
    description = ""
    in_frontmatter = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            if stripped.startswith("name:"):
                name = stripped[5:].strip()
            elif stripped.startswith("description:"):
                description = stripped[12:].strip()
            continue
        if stripped.startswith("# ") and not name:
            name = stripped[2:].strip()
        elif stripped and not stripped.startswith("#") and not description:
            description = stripped
        if name and description:
            break

    if not name:
        name = "Untitled Skill"
    if not description:
        description = ""

    return {"name": name, "description": description}


def _make_skill_id(display_name: str, fallback: str = "skill") -> str:
    ascii_name = display_name.encode("ascii", "ignore").decode("ascii")
    stripped = ascii_name.strip()
    if not stripped:
        ascii_name = fallback.encode("ascii", "ignore").decode("ascii")
        stripped = ascii_name.strip() or "skill"
    slug = re.sub(r"[^a-z0-9\s-]", "", stripped.lower())
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "skill"


def _load_index() -> dict:
    if not SKILLS_INDEX_FILE.exists():
        return {}
    try:
        return json.loads(SKILLS_INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(data: dict):
    SKILLS_INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_skill_meta(skill_id: str) -> dict:
    meta_file = SKILLS_DIR / skill_id / "SKILL.json"
    if not meta_file.exists():
        return {"tags": [], "uploader": "", "downloads": 0, "uploaded_at": ""}
    return json.loads(meta_file.read_text(encoding="utf-8"))


def _save_skill_meta(skill_id: str, meta: dict):
    meta_file = SKILLS_DIR / skill_id / "SKILL.json"
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_index() -> dict:
    index = {}
    if not SKILLS_DIR.exists():
        return index
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text(encoding="utf-8")
        info = parse_skill_md(content)
        stat = skill_md.stat()
        skill_id = skill_dir.name
        meta = _load_skill_meta(skill_id)
        index[skill_id] = {
            "name": info["name"],
            "description": info["description"],
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "tags": meta.get("tags", []),
            "uploader": meta.get("uploader", ""),
            "downloads": meta.get("downloads", 0),
            "uploaded_at": meta.get("uploaded_at", ""),
        }
    return index


def _build_index_entry(skill_id: str) -> dict:
    skill_dir = SKILLS_DIR / skill_id
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found for {skill_id}")
    content = skill_md.read_text(encoding="utf-8")
    info = parse_skill_md(content)
    stat = skill_md.stat()
    meta = _load_skill_meta(skill_id)
    return {
        "name": info["name"],
        "description": info["description"],
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "tags": meta.get("tags", []),
        "uploader": meta.get("uploader", ""),
        "downloads": meta.get("downloads", 0),
        "uploaded_at": meta.get("uploaded_at", ""),
    }


def _update_index_entry_with(skill_id: str, display_name: str, display_desc: str):
    with _index_lock:
        index = _load_index()
        entry = _build_index_entry(skill_id)
        entry["name"] = display_name
        entry["description"] = display_desc
        index[skill_id] = entry
        _save_index(index)


def _update_index_entry(skill_id: str):
    with _index_lock:
        index = _load_index()
        index[skill_id] = _build_index_entry(skill_id)
        _save_index(index)


def _remove_index_entry(skill_id: str):
    with _index_lock:
        index = _load_index()
        index.pop(skill_id, None)
        _save_index(index)


def _is_index_fresh(cached: dict) -> bool:
    actual_dirs = set()
    for d in SKILLS_DIR.iterdir():
        if d.is_dir() and (d / "SKILL.md").exists():
            actual_dirs.add(d.name)
    if set(cached.keys()) != actual_dirs:
        return False
    for skill_id in actual_dirs:
        skill_md = SKILLS_DIR / skill_id / "SKILL.md"
        actual_mtime = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc).isoformat()
        if skill_id not in cached or cached[skill_id].get("modified") != actual_mtime:
            return False
    return True


def list_skills() -> list[dict]:
    if not SKILLS_DIR.exists():
        return []

    cached = _load_index()
    if cached and _is_index_fresh(cached):
        return [{"id": skill_id, **meta} for skill_id, meta in cached.items() if skill_id != "skills_list.json"]

    index = _build_index()
    with _index_lock:
        _save_index(index)
    return [{"id": skill_id, **meta} for skill_id, meta in index.items()]


@app.get("/", response_class=HTMLResponse)
async def index(session: Optional[str] = Cookie(None)):
    cfg = _load_config()
    if cfg["auth_enabled"]:
        if not session or not _verify_session_token(session):
            return RedirectResponse(url="/login")
    if APP_HTML.exists():
        return APP_HTML.read_text(encoding="utf-8")
    return "<h1>Skills Repository</h1><p>index.html not found</p>"


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    cfg = _load_config()
    if not cfg["auth_enabled"]:
        return RedirectResponse(url="/")
    if LOGIN_HTML.exists():
        return LOGIN_HTML.read_text(encoding="utf-8")
    return "<h1>Login</h1>"


@app.post("/api/login")
async def api_login(username: str = Form(...), password: str = Form(...)):
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user = users[username]
    if not _verify_password(password, user["password"], user["salt"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _create_session_token(username)
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        max_age=int(SESSION_MAX_AGE.total_seconds()),
        samesite="strict",
    )
    return response


@app.post("/api/logout")
async def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("session")
    return response


@app.get("/api/me")
async def api_me(username: str = Depends(optional_auth)):
    users = _load_users()
    role = users.get(username, {}).get("role", "user")
    return {"username": username, "role": role}


@app.get("/api/users")
async def api_list_users(_: str = Depends(optional_admin)):
    users = _load_users()
    return [
        {
            "username": username,
            "role": info.get("role", "user"),
            "upload_permission": info.get("upload_permission", True),
            "download_permission": info.get("download_permission", True),
            "delete_permission": info.get("delete_permission", True),
        }
        for username, info in users.items()
    ]


@app.post("/api/users")
async def api_add_user(username: str = Form(...), password: str = Form(...), _: str = Depends(optional_admin)):
    if not username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    users = _load_users()
    if username in users:
        raise HTTPException(status_code=409, detail="User already exists")
    h, salt = _hash_password(password)
    users[username] = {"password": h, "salt": salt, "role": "user",
                        "upload_permission": True, "download_permission": True, "delete_permission": True}
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "username": username}


@app.put("/api/users/{username}/password")
async def api_change_password(
    username: str, password: str = Form(...), _: str = Depends(optional_admin)
):
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    h, salt = _hash_password(password)
    users[username]["password"] = h
    users[username]["salt"] = salt
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "username": username}


@app.put("/api/users/{username}/permissions")
async def api_update_user_permissions(
    username: str, body: dict, _: str = Depends(optional_admin)
):
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    for key in ("upload_permission", "download_permission", "delete_permission"):
        if key in body:
            users[username][key] = bool(body[key])
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "username": username}


@app.delete("/api/users/{username}")
async def api_delete_user(username: str, _: str = Depends(optional_admin)):
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    if len(users) == 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last user")
    del users[username]
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "username": username}


@app.get("/api/tags")
async def api_list_tags(_: str = Depends(optional_auth)):
    skills = list_skills()
    tags = set()
    for s in skills:
        for t in s.get("tags", []):
            tags.add(t)
    return sorted(tags)


CLIENT_DIR = Path(__file__).parent / "skills_repo_client"


@app.get("/api/ai-skill")
async def api_ai_skill(request: Request, username: str = Depends(optional_auth)):
    _check_operation_permission(username, "download_permission")
    server_url = f"{request.url.scheme}://{request.url.netloc}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in CLIENT_DIR.rglob("*"):
            if fpath.is_file():
                arcname = str(fpath.relative_to(CLIENT_DIR.parent))
                if fpath.suffix == ".md":
                    content = fpath.read_text(encoding="utf-8")
                    content = content.replace("{{SKILLS_REPO_URL}}", server_url)
                    zf.writestr(arcname, content.encode("utf-8"))
                else:
                    zf.write(fpath, arcname)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=client.zip"},
    )


SYSTEM_PROMPT = """You are a Skill creation assistant. Guide the user to create a complete OpenCode skill package.

A skill package contains a SKILL.md file and optional helper/script files (Python, shell, etc).

Your workflow:
1. Ask what skill the user wants to create. Understand the use case.
2. Ask clarifying questions: name, specific commands, dependencies, workflow steps.
3. When ready, generate files. Output each file in a separate code block with the filename as the language tag:

```SKILL.md
---
name: skill-name
description: clear one-line summary
---

# Title

## 触发场景

- When the user says "..."

## 执行步骤

### 1. Step one

```bash
command
```
```

```helper.py
#!/usr/bin/env python3
# helper script content
```

Rules:
- Each file goes in a code block with the exact filename as the language tag
- Use the user's language (Chinese if the user writes in Chinese)
- Skill name: lowercase alphanumeric, single hyphens (^[a-z0-9]+(-[a-z0-9]+)*$)
- Description: clear one-line summary
- Write complete, working file contents
- Proactively suggest helper scripts when needed
- After generating all files, summarize what was created
"""


@app.post("/api/ai/generate")
async def api_ai_generate(body: dict, username: str = Depends(optional_auth)):
    cfg = _load_config()
    api_url = cfg.get("llm_api_url", "")
    api_key = cfg.get("llm_api_key", "")
    model = cfg.get("llm_model", "")

    if not api_url:
        raise HTTPException(status_code=400, detail="LLM API not configured")

    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    # if first message, prepend system prompt
    if not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    payload = {"model": model, "messages": messages, "stream": True}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async def generate_sse():
        async with httpx.AsyncClient(timeout=180) as client:
            full_text = ""
            async with client.stream("POST", api_url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    error_msg = f"LLM API error: {resp.status_code}"
                    try:
                        error_msg = json.loads(error_body).get("error", {}).get("message", error_msg)
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'event': 'error', 'message': error_msg})}\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        # Parse file blocks from accumulated text
                        files, parsed_text = _parse_file_blocks(full_text)
                        skill_id = ""
                        description = ""
                        for fn, fcontent in files:
                            if fn == "SKILL.md":
                                skill_id, description = _parse_skill_frontmatter(fcontent)
                        yield f"data: {json.dumps({'event': 'done', 'text': parsed_text, 'files': [{'name': n, 'content': c} for n, c in files], 'skill_id': skill_id, 'description': description})}\n\n"
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            yield f"data: {json.dumps({'event': 'text', 'content': content, 'full': full_text})}\n\n"
                    except json.JSONDecodeError:
                        pass

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_skill_frontmatter(content: str) -> tuple:
    """Extract name and description from SKILL.md frontmatter."""
    name, desc = "", ""
    if content.lstrip().startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 2:
            fm_text = parts[1]
            for line in fm_text.strip().split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line[5:].strip()
                elif line.startswith("description:"):
                    desc = line[12:].strip()
    return name, desc


def _parse_file_blocks(text: str) -> tuple:
    """Parse file blocks from generated text. Returns (files, cleaned_text)."""
    import re as _re
    files = []
    cleaned = text
    seen = set()

    # Match code blocks with filename as language tag: ```FILENAME.ext \n content \n ```
    block_re = _re.compile(
        r'```([^\s\n][^\n]*?\.\w+)\s*\n(.*?)```',
        _re.DOTALL
    )
    for m in block_re.finditer(text):
        fn = m.group(1).strip()
        fcontent = m.group(2)
        # Skip obviously-not-filename patterns
        if fn.lower() in ('json', 'bash', 'sh', 'python', 'py', 'yaml', 'yml', 'text', 'markdown', 'shell', 'zsh'):
            continue
        if '/' in fn or '\\' in fn:
            continue
        if not _re.match(r'^[\w][\w\-.]*\.\w+$', fn):
            continue
        if fn not in seen:
            files.append((fn, fcontent))
            seen.add(fn)

    return files, cleaned.strip()


@app.get("/api/skills")
async def api_list_skills(q: str = "", tag: str = "", _: str = Depends(optional_auth)):
    skills = list_skills()
    if q:
        q_lower = q.lower()
        skills = [
            s for s in skills
            if q_lower in s["name"].lower()
            or q_lower in s["description"].lower()
            or q_lower in s["id"].lower()
        ]
    if tag:
        tag_lower = tag.lower()
        skills = [s for s in skills if any(tag_lower in t.lower() for t in s.get("tags", []))]
    return skills


@app.get("/api/skills/download/all")
async def api_download_all_skills(username: str = Depends(optional_auth)):
    _check_operation_permission(username, "download_permission")
    skills = list_skills()
    if not skills:
        raise HTTPException(status_code=404, detail="No skills found")

    for s in skills:
        with _index_lock:
            meta = _load_skill_meta(s["id"])
            meta["downloads"] = meta.get("downloads", 0) + 1
            _save_skill_meta(s["id"], meta)
            _update_index_entry(s["id"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue
            for fpath in skill_dir.rglob("*"):
                if fpath.is_file() and fpath.name != "SKILL.json":
                    arcname = str(fpath.relative_to(SKILLS_DIR))
                    zf.write(fpath, arcname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=skills_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        },
    )


@app.get("/api/skills/{skill_id}")
async def api_get_skill(skill_id: str, _: str = Depends(optional_auth)):
    skill_dir = SKILLS_DIR / skill_id
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail="Skill not found")
    content = skill_md.read_text(encoding="utf-8")
    display_content = content
    if content.lstrip().startswith("---"):
        parts = content.split("---", 2)
        display_content = parts[2] if len(parts) >= 3 else content
    info = parse_skill_md(content)
    meta = _load_skill_meta(skill_id)
    return {"id": skill_id, "name": info["name"], "description": info["description"],
            "content": display_content, "tags": meta.get("tags", []),
            "uploader": meta.get("uploader", ""), "downloads": meta.get("downloads", 0),
            "uploaded_at": meta.get("uploaded_at", "")}


@app.put("/api/skills/{skill_id}/tags")
async def api_update_tags(skill_id: str, tags: list[str] = Body(...), _: str = Depends(optional_auth)):
    skill_dir = SKILLS_DIR / skill_id
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="Skill not found")
    meta = _load_skill_meta(skill_id)
    meta["tags"] = tags
    _save_skill_meta(skill_id, meta)
    _update_index_entry(skill_id)
    return {"ok": True, "tags": tags}


@app.post("/api/skills/upload")
async def api_upload_skill(
    files: List[UploadFile] = File(...),
    skill_id: str = Form(""),
    description: str = Form(...),
    tags: str = Form(...),
    username: str = Depends(optional_auth),
):
    _check_operation_permission(username, "upload_permission")
    if not description.strip():
        raise HTTPException(status_code=400, detail="Description is required")
    if not tags.strip():
        raise HTTPException(status_code=400, detail="Tags is required")

    if len(files) == 1:
        f = files[0]
        filename = f.filename or "untitled"
        ext = Path(filename).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            return await _handle_md_upload(username, skill_id, description, tags, f)
        raise HTTPException(status_code=400, detail="Single file upload must be a .md or .mdc file")

    return await _handle_folder_upload(username, skill_id, description, tags, files)


async def _handle_folder_upload(uploader: str, skill_id: str, description: str, tags: str, files: List[UploadFile]) -> dict:
    has_skill_md = any(Path(f.filename or "").name == "SKILL.md" for f in files)
    if not has_skill_md:
        raise HTTPException(status_code=400, detail="Folder upload must contain a SKILL.md file")

    first_path = files[0].filename or ""
    parts = Path(first_path).parts

    if not skill_id.strip():
        folder_name = parts[0] if parts else "skill"
        skill_id = _make_skill_id(folder_name.replace(" ", "-"), "skill")
    else:
        skill_id = skill_id.strip().lower()
        if not SKILL_ID_RE.match(skill_id):
            raise HTTPException(status_code=400, detail=f"Invalid skill ID: '{skill_id}'")

    skill_dir = SKILLS_DIR / skill_id
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_id}' already exists")
    skill_dir.mkdir(parents=True)
    resolved_skill_dir = skill_dir.resolve()

    for f in files:
        fname = f.filename or ""
        rel = Path(fname)
        if ".." in rel.parts:
            raise HTTPException(status_code=400, detail=f"Invalid file path: {fname}")
        if len(rel.parts) > 1 and rel.parts[0] == parts[0]:
            rel = Path(*rel.parts[1:])
        target = (skill_dir / rel).resolve()
        if not (str(target).startswith(str(resolved_skill_dir) + os.sep) or target == resolved_skill_dir):
            raise HTTPException(status_code=400, detail=f"Invalid file path: {fname}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await f.read())

    display_desc = description.strip()
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        if not display_desc:
            info = parse_skill_md(content)
            display_desc = info["description"]
        if not content.lstrip().startswith("---"):
            content = f"---\nname: {skill_id}\ndescription: {display_desc}\n---\n\n{content}"
            skill_md.write_text(content, encoding="utf-8")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags.strip() else []

    now = datetime.now(timezone.utc).isoformat()
    _save_skill_meta(skill_id, {"tags": tag_list, "uploader": uploader, "downloads": 0, "uploaded_at": now})
    _update_index_entry_with(skill_id, skill_id, display_desc)
    return {"id": skill_id, "name": skill_id, "description": display_desc, "tags": tag_list}


SKILL_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


async def _handle_md_upload(uploader: str, skill_id: str, description: str, tags: str, file: UploadFile) -> dict:
    content_bytes = await file.read()
    content = content_bytes.decode("utf-8")

    auto_info = parse_skill_md(content)
    if not skill_id.strip():
        skill_id = _make_skill_id(auto_info["name"], Path(file.filename or "untitled").stem)
    else:
        skill_id = skill_id.strip().lower()
        if not SKILL_ID_RE.match(skill_id):
            raise HTTPException(status_code=400, detail=f"Invalid skill ID: '{skill_id}'. Must match: a-z0-9 with single hyphens (e.g. my-skill)")
    display_desc = description.strip() if description.strip() else auto_info["description"]
    display_name = skill_id

    if not content.lstrip().startswith("---"):
        content = f"---\nname: {skill_id}\ndescription: {display_desc}\n---\n\n{content}"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags.strip() else []

    skill_dir = SKILLS_DIR / skill_id
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_id}' already exists")
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    _save_skill_meta(skill_id, {"tags": tag_list, "uploader": uploader, "downloads": 0, "uploaded_at": now})

    _update_index_entry_with(skill_id, display_name, display_desc)
    return {"id": skill_id, "name": display_name, "description": display_desc, "tags": tag_list}


@app.get("/api/skills/{skill_id}/download")
async def api_download_skill(skill_id: str, username: str = Depends(optional_auth)):
    _check_operation_permission(username, "download_permission")
    skill_dir = SKILLS_DIR / skill_id
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    with _index_lock:
        meta = _load_skill_meta(skill_id)
        meta["downloads"] = meta.get("downloads", 0) + 1
        _save_skill_meta(skill_id, meta)
        _update_index_entry(skill_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in skill_dir.rglob("*"):
            if fpath.is_file() and fpath.name != "SKILL.json":
                arcname = str(fpath.relative_to(SKILLS_DIR))
                zf.write(fpath, arcname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={skill_id}.zip"},
    )


@app.delete("/api/skills/{skill_id}")
async def api_delete_skill(skill_id: str, username: str = Depends(optional_auth)):
    skill_dir = SKILLS_DIR / skill_id
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    _check_operation_permission(username, "delete_permission")

    cfg = _load_config()
    if cfg["auth_enabled"] and cfg.get("delete_permission") != "admin":
        meta = _load_skill_meta(skill_id)
        uploader = meta.get("uploader", "")
        if username != uploader:
            users = _load_users()
            if users.get(username, {}).get("role") != "admin":
                raise HTTPException(status_code=403, detail="You can only delete your own skills")

    shutil.rmtree(skill_dir)
    _remove_index_entry(skill_id)
    return {"deleted": skill_id}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--add-user", "--change-password"):
        import getpass

        username = input("Username: ").strip()
        if not username:
            print("Username cannot be empty")
            sys.exit(1)

        password = getpass.getpass("Password: ")
        if not password:
            print("Password cannot be empty")
            sys.exit(1)

        h, salt = _hash_password(password)
        users = _load_users()
        is_new = username not in users
        role = "admin" if not users else "user"
        users[username] = {"password": h, "salt": salt, "role": role}
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
        action = "added" if is_new else "password updated for"
        print(f"User '{username}' {action}.")
        sys.exit(0)

    import uvicorn
    cfg = _load_config()
    uvicorn.run(app, host=cfg["host"], port=cfg["port"])
