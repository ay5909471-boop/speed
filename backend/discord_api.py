import requests, time
from typing import List, Dict

API = "https://discord.com/api/v10"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def _headers(token: str, json_body: bool = False):
    h = {"Authorization": token, "User-Agent": UA}
    if json_body: h["Content-Type"] = "application/json"
    return h

def get_me(token: str) -> Dict:
    r = requests.get(f"{API}/users/@me", headers=_headers(token))
    if r.status_code != 200: raise ValueError("Invalid token")
    return r.json()

def get_dms(token: str) -> List[Dict]:
    r = requests.get(f"{API}/users/@me/channels", headers=_headers(token))
    out = []
    if r.status_code == 200:
        for ch in r.json():
            if ch.get("type") == 1 and ch.get("recipients"):
                u = ch["recipients"][0]
                out.append({"channel_id": ch["id"], "recipient_id": u["id"], "username": u.get("username", "Unknown"), "avatar": u.get("avatar")})
    return out

def get_friends(token: str) -> List[Dict]:
    r = requests.get(f"{API}/users/@me/relationships", headers=_headers(token))
    out = []
    if r.status_code == 200:
        for rel in r.json():
            if rel.get("type") == 1 and rel.get("user"):
                u = rel["user"]
                out.append({"recipient_id": str(u["id"]), "user_id": str(u["id"]), "username": u.get("username", "Unknown"), "avatar": u.get("avatar")})
    return out

def get_guilds(token: str) -> List[Dict]:
    r = requests.get(f"{API}/users/@me/guilds", headers=_headers(token))
    return r.json() if r.status_code == 200 else []

def get_channel_info(token: str, channel_id: str) -> Dict:
    r = requests.get(f"{API}/channels/{channel_id}", headers=_headers(token))
    if r.status_code != 200: raise ValueError("Channel not found")
    return r.json()

def delete_messages(token, channel_id, delay, should_stop, log) -> Dict:
    h = _headers(token)
    my_id = get_me(token)["id"]
    before, deleted = None, 0
    while not should_stop():
        url = f"{API}/channels/{channel_id}/messages?limit=100"
        if before: url += f"&before={before}"
        r = requests.get(url, headers=h)
        if r.status_code != 200 or not r.json(): break
        msgs = r.json()
        before = msgs[-1]["id"]
        for m in msgs:
            if should_stop(): break
            if m.get("author", {}).get("id") == my_id:
                d = requests.delete(f"{API}/channels/{channel_id}/messages/{m['id']}", headers=h)
                if d.status_code in (200, 204):
                    deleted += 1
                    log(f"🗑️ [{deleted}] {m['id']}")
                elif d.status_code == 429:
                    retry = d.json().get("retry_after", 2)
                    log(f"⏳ Rate limit! انتظر {retry}s")
                    time.sleep(retry)
                if delay > 0: time.sleep(delay)
    return {"deleted": deleted}

def clear_dms(token, channel_ids, delay, should_stop, log) -> Dict:
    deleted = 0
    for cid in channel_ids:
        if should_stop(): break
        res = delete_messages(token, cid, delay, should_stop, log)
        deleted += res["deleted"]
    return {"deleted": deleted}

def remove_friends(token, user_ids, delay, should_stop, log) -> Dict:
    h = _headers(token)
    removed = 0
    for uid in user_ids:
        if should_stop(): break
        r = requests.delete(f"{API}/users/@me/relationships/{uid}", headers=h)
        if r.status_code in (200, 204):
            removed += 1
            log(f"✅ Removed {uid}")
        time.sleep(max(delay, 1))
    return {"removed": removed}

def leave_servers(token, guild_ids, delay, should_stop, log) -> Dict:
    h = _headers(token)
    left = 0
    for gid in guild_ids:
        if should_stop(): break
        r = requests.delete(f"{API}/users/@me/guilds/{gid}", headers=h)
        if r.status_code in (200, 204):
            left += 1
            log(f"✅ Left {gid}")
        time.sleep(max(delay, 1))
    return {"left": left}

def mass_dm(token, targets, message, delay, should_stop, log) -> Dict:
    h = _headers(token, json_body=True)
    sent = 0
    for t in targets:
        if should_stop(): break
        r = requests.post(f"{API}/channels/{t['channel_id']}/messages", headers=h, json={"content": message})
        if r.status_code in (200, 201):
            sent += 1
            log(f"✅ Sent to @{t['username']}")
        elif r.status_code == 429:
            retry = r.json().get("retry_after", 5)
            log(f"⏳ Rate limit! انتظر {retry}s")
            time.sleep(retry)
        time.sleep(max(delay, 1))
    return {"sent": sent}

def clone_server(token, src_id, dst_id, opts, should_stop, log) -> Dict:
    h = _headers(token, json_body=True)
    total = 0
    
    def safe_sleep(seconds):
        # بنقسم الوقت لأجزاء صغيرة عشان نعرف نوقف فوراً
        chunks = int(seconds * 10)
        for _ in range(chunks):
            if should_stop(): return
            time.sleep(0.1)

    # 1. مسح السيرفر الهدف
    log("🧹 جاري مسح السيرفر الهدف...")
    try:
        dst_channels = requests.get(f"{API}/guilds/{dst_id}/channels", headers=h).json()
        for c in dst_channels:
            if should_stop(): break
            requests.delete(f"{API}/channels/{c['id']}", headers=h)
            safe_sleep(2)
        log("✅ تم مسح الرومات.")
    except Exception as e: log(f"⚠️ {e}")

    try:
        dst_roles = requests.get(f"{API}/guilds/{dst_id}/roles", headers=h).json()
        for r in dst_roles:
            if should_stop(): break
            if r["name"] == "@everyone" or r.get("managed"): continue
            requests.delete(f"{API}/guilds/{dst_id}/roles/{r['id']}", headers=h)
            safe_sleep(2)
        log("✅ تم مسح الرتب.")
    except Exception as e: log(f"⚠️ {e}")

    # 2. بدء النسخ
    log("🚀 بدء نسخ العناصر الجديدة...")

    if opts.get("roles"):
        rs = requests.get(f"{API}/guilds/{src_id}/roles", headers=h).json()
        for r in rs:
            if should_stop(): break
            if r.get("name") == "@everyone" or r.get("managed"): continue
            requests.post(f"{API}/guilds/{dst_id}/roles", headers=h, json={
                "name": r["name"], "permissions": str(r.get("permissions", "0")), "color": r.get("color", 0)
            })
            total += 1
            log(f"✅ Role: {r['name']}")
            safe_sleep(2)

    if opts.get("channels"):
        chs = requests.get(f"{API}/guilds/{src_id}/channels", headers=h).json()
        for c in chs:
            if should_stop(): break
            if c.get("type") == 4:
                requests.post(f"{API}/guilds/{dst_id}/channels", headers=h, json={"name": c["name"], "type": 4, "position": c.get("position", 0)})
                total += 1
                safe_sleep(2)
        for c in chs:
            if should_stop(): break
            if c.get("type") != 4:
                requests.post(f"{API}/guilds/{dst_id}/channels", headers=h, json={
                    "name": c["name"], "type": c["type"], "topic": c.get("topic", ""), "nsfw": c.get("nsfw", False)
                })
                total += 1
                log(f"📁 Channel: {c['name']}")
                safe_sleep(2)

    return {"total": total}

def set_hypesquad(token, house_id) -> bool:
    r = requests.post("https://discord.com/api/v9/hypesquad/online", headers=_headers(token, json_body=True), json={"house_id": house_id})
    return r.status_code in (200, 204)