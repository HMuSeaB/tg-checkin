# -*- coding: utf-8 -*-
"""MINIAPP 双签到自动化(在 hmu2 上跑)。

流程:
  1. telethon 用现有 session 向 @zzmeb_bot 申请 WebView(URL=miniapp 按钮
     背后的 https://user.090911.xyz/mini),Telegram 签发带 tgWebAppData 的 URL
  2. 取出 initData → POST https://user.090911.xyz/api/auth/telegram 换会话 cookie
  3. POST /api/user/checkin 完成网页版签到(与 bot 签到是两个独立流,双倍奖励)
用法: .venv/bin/python miniapp_checkin.py [--dry-run]
"""
import argparse
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/data/workspace/tg_checkin")
import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.tl import functions, types  # noqa: E402

WEB = "https://user.090911.xyz"
BOT = "@zzmeb_bot"
MINI_URL = "https://user.090911.xyz/mini"


async def get_init_data(client):
    """申请 WebView,拿 Telegram 签发的 initData。

    注意:telethon 的方法名是 RequestWebViewRequest 这种全名(不是
    requestWebView 短名),2026-11-03 实测踩过。
    """
    # 先拿 bot 实体
    ent = await client.get_entity(BOT)
    for cls_name in ("RequestWebViewRequest", "RequestSimpleWebViewRequest",
                     "RequestMainWebViewRequest"):
        cls = getattr(functions.messages, cls_name, None)
        if cls is None:
            continue
        try:
            kw = dict(peer=ent, bot=ent, platform="web", url=MINI_URL)
            res = await client(cls(**kw))
            url = getattr(res, "url", None)
            if url:
                # 实测:tgWebAppData 在 URL fragment(# 后面),不在 query。
                # ⚠ 只取到第一个 & 为止——fragment 里 tgWebAppData 后面
                # 还跟着 &tgWebAppVersion=&tgWebAppPlatform= 等参数,
                # 一起吞进去 hash 校验必失败(2026-11-03 实测 401)
                frag = urllib.parse.urlparse(url).fragment
                if "tgWebAppData=" in frag:
                    data = frag.split("tgWebAppData=", 1)[1].split("&")[0]
                    return urllib.parse.unquote(data), url
        except Exception as e:
            print(f"[webview] {cls_name} 失败: {type(e).__name__} {str(e)[:120]}")
    return None, None


def web_checkin(init_data, dry_run=False):
    """initData 换会话 → 签到。返回 (ok, 说明)。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj))
    # 全套浏览器头:缺 Origin/Accept 会被 Cloudflare WAF 403(实测)
    h = {"Content-Type": "application/json",
         "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
         "Referer": WEB + "/", "Origin": WEB,
         "Accept": "application/json",
         "Accept-Language": "zh-CN,zh;q=0.9",
         "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
         "Sec-Fetch-Dest": "empty"}

    def post(path, body):
        req = urllib.request.Request(WEB + path,
                                     data=json.dumps(body).encode(),
                                     method="POST", headers=h)
        try:
            with opener.open(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"ok": False, "message": raw[:150]}

    st, resp = post("/api/auth/telegram", {"initData": init_data})
    print(f"[auth] HTTP {st}: {json.dumps(resp, ensure_ascii=False)[:200]}")
    if not resp.get("ok"):
        return False, f"登录失败: {resp.get('message')}"
    user = (resp.get("data") or {}).get("user") or {}
    who = user.get("username") or user.get("name") or user.get("tg") or "?"
    if dry_run:
        return True, f"[dry-run] 网页会话 ok({who}),未真签到"
    try:
        st, resp = post("/api/user/checkin", {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            resp = json.loads(body)
        except Exception:
            return False, f"签到 HTTP {e.code}: {body[:100]}"
    if resp.get("ok"):
        return True, f"网页版签到成功({who}): {str(resp.get('message'))[:80]}"
    return False, f"签到未成: {resp.get('message')}"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    client = TelegramClient("/data/workspace/tg_checkin/config/tg_session",
                            tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.start()
    init, url = await get_init_data(client)
    await client.disconnect()
    if not init:
        print("RESULT: FAIL 拿不到 initData(WebView 申请被拒)")
        return
    ok, msg = web_checkin(init, dry_run=args.dry_run)
    print(("RESULT: OK " if ok else "RESULT: FAIL ") + msg)


import asyncio  # noqa: E402
asyncio.run(main())
