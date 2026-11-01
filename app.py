"""本地签到面板后端（FastAPI）

启动后自动接管本机 Telegram Desktop 的授权会话，
前端提供一个「一键签到」按钮 + 每个任务单独的按钮。
"""

import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import tdata_session
from checkin_engine import engine
from config_manager import config_mgr
from scheduler import scheduler_mgr

# 固定端口，只监听回环地址：面板里的 bot 清单和会话状态不对局域网暴露
PORT = 8899

# pythonw.exe 启动时 sys.stdout / sys.stderr 都是 None，
# uvicorn 和 logging 往里写就抛异常，进程直接无声退出——
# 表现就是"双击快捷方式没反应"。这里先兜住，写到 logs/panel.log。
if sys.stdout is None or sys.stderr is None:
    _root = os.path.dirname(os.path.abspath(__file__))
    _log_dir = os.path.join(_root, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _fh = open(os.path.join(_log_dir, "panel.log"), "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _fh
    if sys.stderr is None:
        sys.stderr = _fh

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TGCheckin.App")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine.add_log("面板已启动，正在接管桌面端 Telegram 会话…")
    asyncio.create_task(_boot())
    yield
    await engine.disconnect()


async def _boot():
    try:
        client = await engine.ensure_session()
        if client:
            engine.add_log("准备就绪，可以点「一键签到」了", "success")
            if config_mgr.get_settings().get("auto_start_scheduler", True):
                scheduler_mgr.start()
    except Exception as e:
        engine.add_log(f"启动异常：{type(e).__name__}: {e}", "error")


app = FastAPI(title="TG 签到面板", lifespan=lifespan)


# ---------------- 数据模型 ----------------

class TaskBody(BaseModel):
    id: str
    name: str
    target_bot: str
    mode: str = "button"
    command: str = "/start"
    button_match: str = "签到"
    wait_seconds: int = 4
    cron_time: str = "08:30"
    success_keywords: List[str] = []
    fresh_menu: bool = True
    cleanup_command: bool = True
    enabled: bool = True
    last_run: Optional[str] = None
    last_status: Optional[str] = "未运行"
    last_ok: Optional[bool] = None


class ProxyBody(BaseModel):
    enabled: bool = True
    type: str = "socks5"
    addr: str = "127.0.0.1"
    port: int = 7890


class ConfigBody(BaseModel):
    proxy: Optional[ProxyBody] = None


# ---------------- 接口 ----------------

@app.get("/api/status")
async def status():
    info = await engine.account_info()
    return {
        **info,
        "local_ip": get_local_ip(),
        "port": PORT,
        "scheduler_running": scheduler_mgr.is_running,
        "server_ok": True,
    }


@app.post("/api/relogin")
async def relogin():
    await engine.ensure_session(force=True)
    info = await engine.account_info()
    return info


@app.get("/api/tasks")
async def get_tasks():
    return config_mgr.get_tasks()


@app.post("/api/tasks")
async def save_tasks(tasks: List[TaskBody]):
    cfg = config_mgr.config_data
    cfg["tasks"] = [t.model_dump() for t in tasks]
    if not config_mgr.save(cfg):
        raise HTTPException(500, "保存失败")
    scheduler_mgr.reload_jobs()
    return {"ok": True}


@app.post("/api/run/{task_id}")
async def run_one(task_id: str, force: bool = False):
    res = await engine.run_one(task_id, force=force)
    return res


@app.post("/api/run_all")
async def run_all():
    results = await engine.run_all()
    ok_n = sum(1 for r in results if r["ok"])
    return {
        "ok": True,
        "total": len(results),
        "success": ok_n,
        "results": results,
    }


@app.get("/api/logs")
async def get_logs():
    return {"logs": engine.logs}


@app.post("/api/logs/clear")
async def clear_logs():
    engine.clear_logs()
    return {"ok": True}


@app.get("/api/config")
async def get_config():
    tg = config_mgr.get_telegram_config()
    return {"telegram": tg, "settings": config_mgr.get_settings()}


@app.post("/api/config")
async def save_config(body: ConfigBody):
    cfg = config_mgr.config_data
    if body.proxy is not None:
        cfg["telegram"]["proxy"] = body.proxy.model_dump()
    config_mgr.save(cfg)
    return {"ok": True}


@app.get("/api/probe")
async def probe(target: str, command: str = "/start", send: bool = False):
    """
    探测机器人签到入口。

    默认 send=false：只读历史消息，**不发任何消息**。
    给 bot 乱发 /start 会触发对方的频率限制，甚至被判定为滥用。
    """
    return await engine.probe_bot(target, command, send=send)


@app.get("/api/bots/probe")
async def probe_bots():
    """扫描近期会话里的机器人，方便添加新任务"""
    client = await engine.ensure_session()
    if not client:
        return JSONResponse({"ok": False, "error": "未连接"}, status_code=503)
    from telethon.tl.types import User
    out = []
    try:
        async for d in client.iter_dialogs(limit=80):
            e = d.entity
            if isinstance(e, User) and e.bot:
                out.append({"name": d.name, "username": e.username, "id": e.id})
    except Exception as e:
        return {"ok": False, "error": str(e), "bots": out}
    return {"ok": True, "bots": out}


@app.get("/api/peek")
async def peek(bot: str, limit: int = 5):
    """调试用：看某个机器人最近几条消息与按钮"""
    client = await engine.ensure_session()
    if not client:
        return {"ok": False, "error": "未连接"}
    try:
        ent = await client.get_entity(bot)
        msgs = await client.get_messages(ent, limit=max(1, min(limit, 20)))
        return {
            "ok": True,
            "bot": bot,
            "messages": [
                {
                    "id": m.id,
                    "out": m.out,
                    "text": (m.message or "")[:200],
                    "buttons": [b.text for row in (m.buttons or []) for b in row],
                }
                for m in msgs
            ],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(static_dir, exist_ok=True)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """浏览器标签页图标，复用快捷方式那个 app.ico。"""
    path = os.path.join(BASE_DIR, "app.ico")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/x-icon")
    raise HTTPException(status_code=404)


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import threading
    import webbrowser

    url = f"http://127.0.0.1:{PORT}"

    # 重复双击快捷方式时不要再起一个实例（第二个进程抢端口会失败），
    # 直接把已经跑着的那个页面调出来。
    if _port_in_use(PORT):
        print(f"面板已在运行：{url}")
        webbrowser.open(url)
        raise SystemExit(0)

    # 默认自己开浏览器；从命令行调试时可设 TG_NO_BROWSER=1 关掉
    if os.environ.get("TG_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
