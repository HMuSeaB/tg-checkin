"""签到引擎：直接复用本机 Telegram Desktop 的授权会话执行签到

不重复登录的好处：不占额外活跃会话、不需要验证码、不打扰桌面端。
会话从 tdata 里导出后落到本地 config/tg_session.session。
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from config_manager import config_mgr, SESSION_FILE
import tdata_session

logger = logging.getLogger("TGCheckin.Engine")

API_ID = tdata_session.TDESKTOP_API_ID
API_HASH = tdata_session.TDESKTOP_API_HASH

# 机器人表达"今天的名额已经用掉了"的说法。
# 这类回执代表今日任务已完成，但**不是本次签到成功**——
# 面板要显示成"今日已签"（黄），不能显示成"已完成"（绿），
# 否则用户会以为刚才是自己签上的。
ALREADY_WORDS = (
    "已经签到", "已签到", "已经签过", "已签过", "签过到了",
    "明天再来", "今日已签", "今日已领取", "已领取过", "已经领取",
)
ALREADY_PREFIX = "今日已签到 · "


class CheckinEngine:
    def __init__(self):
        self.client = None
        self.logs: List[Dict[str, str]] = []
        self._max_logs = 400
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        #: task_id -> {"date": "YYYY-MM-DD", "count": n}，用于限制单日真跑次数
        self._run_history: Dict[str, Dict[str, Any]] = {}
        self.running = False
        self.account: Optional[Dict] = None
        self.on_log: Optional[Callable[[Dict[str, str]], None]] = None

    # ---------------- 日志 ----------------

    def add_log(self, text: str, level: str = "info") -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "text": text,
            "level": level,
        }
        logger.info(text)
        self.logs.append(entry)
        if len(self.logs) > self._max_logs:
            del self.logs[: len(self.logs) - self._max_logs]
        if self.on_log:
            try:
                self.on_log(entry)
            except Exception:
                pass

    def clear_logs(self) -> None:
        self.logs.clear()

    # ---------------- 会话 ----------------

    async def ensure_session(self, force: bool = False) -> Optional[Any]:
        """确保已连上 Telegram；失败返回 None

        加独立锁：启动任务与前端请求可能同时进来，两个协程同时打开
        同一个 SQLite session 会直接 "database is locked"。
        """
        async with self._connect_lock:
            return await self._ensure_session_locked(force)

    async def _ensure_session_locked(self, force: bool) -> Optional[Any]:
        if self.client is not None and not force:
            try:
                if self.client.is_connected():
                    return self.client
            except Exception:
                pass
            self.client = None

        from telethon import TelegramClient

        keep_session = os.path.exists(SESSION_FILE + ".session")
        if force or not keep_session:
            self.add_log("正在从本机 Telegram Desktop 读取已授权会话…")
            built = tdata_session.build_session_from_desktop(SESSION_FILE)
            if not built.get("ok"):
                self.add_log(f"读取桌面端会话失败：{built.get('error')}", "error")
                return None
            self.account = built
            self.add_log(
                f"已接管桌面端账号 user_id={built['user_id']}（DC{built['dc_id']}），无需验证码"
            )

        proxy = config_mgr.get_proxy()
        if proxy:
            self.add_log(f"使用代理 {proxy[0]}://{proxy[1]}:{proxy[2]}")

        client = TelegramClient(
            SESSION_FILE, API_ID, API_HASH,
            proxy=proxy if proxy else None,
            connection_retries=3,
            timeout=20,
            auto_reconnect=True,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                self.add_log("已有会话文件但未授权，重新从桌面端导出…", "warn")
                await client.disconnect()
                built = tdata_session.build_session_from_desktop(SESSION_FILE)
                if not built.get("ok"):
                    self.add_log(f"重新导出失败：{built.get('error')}", "error")
                    return None
                client = TelegramClient(
                    SESSION_FILE, API_ID, API_HASH,
                    proxy=proxy if proxy else None,
                    connection_retries=3, timeout=20, auto_reconnect=True,
                )
                await client.connect()
                if not await client.is_user_authorized():
                    self.add_log("仍然无法授权，请检查账号是否在桌面端已登录", "error")
                    return None
        except Exception as e:
            self.add_log(f"连接 Telegram 失败：{type(e).__name__}: {e}", "error")
            return None

        me = await client.get_me()
        self.account = {
            "user_id": me.id,
            "name": me.first_name,
            "username": me.username,
            "dc_id": self.account.get("dc_id") if self.account else None,
        }
        self.add_log(f"已连接：{me.first_name}" + (f" (@{me.username})" if me.username else ""))
        self.client = client
        return client

    async def disconnect(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def account_info(self) -> Dict[str, Any]:
        res = {"connected": False, "user": None, "error": None,
               "tdata": "", "desktop_accounts": []}
        try:
            info = tdata_session.read_accounts()
            if info.get("ok"):
                res["tdata"] = info["tdata"]
                res["desktop_accounts"] = [
                    {"user_id": a["user_id"], "main_dc": a["main_dc"], "dir": a["dir"]}
                    for a in info["accounts"]
                ]
            else:
                res["error"] = info.get("error")
        except Exception as e:
            res["error"] = str(e)

        client = await self.ensure_session()
        if client:
            try:
                me = await client.get_me()
                res["connected"] = True
                res["user"] = {
                    "id": me.id,
                    "name": me.first_name,
                    "username": me.username,
                }
            except Exception as e:
                res["error"] = f"{type(e).__name__}: {e}"
        return res

    # ---------------- 任务执行 ----------------

    @staticmethod
    def _extract_feedback(msg) -> str:
        if not msg:
            return ""
        txt = (msg.message or "").replace("\n", " · ")
        return txt[:120]

    async def _find_clickable(self, client, entity, pattern: str,
                              min_id: int = 0, limit: int = 6):
        """
        找匹配正则的按钮。

        min_id 是硬性约束：只认 id 大于它的消息。
        必须带上这个，否则会点到历史菜单上的按钮 —— 那些按钮所在的
        消息往往已经被机器人原地编辑成"签到成功"记录，
        再点一次会把整个面板覆盖掉（部分 bot 就是这样被搞坏的）。
        """
        msgs = await client.get_messages(entity, limit=limit)
        for m in msgs:
            if (m.id or 0) <= min_id:
                continue
            for row in (m.buttons or []):
                for btn in row:
                    if re.search(pattern, btn.text or "", re.IGNORECASE):
                        return btn
        return None

    async def _latest_msg_id(self, client, entity) -> int:
        """当前会话里最新的消息 id（含自己发的）"""
        try:
            msgs = await client.get_messages(entity, limit=1)
            return msgs[0].id if msgs else 0
        except Exception:
            return 0

    async def _collect_feedback(self, client, entity, baseline: Dict[int, str],
                                timeout: float = 14.0) -> str:
        """
        轮询等待机器人回执。

        两个坑都要处理：
          1. 点按钮后机器人可能过几秒才发新消息 → 必须轮询，不能只读一次
          2. 机器人常把已发的那条面板消息【原地编辑】成"签到成功"
             → 消息 id 不变，只比较 id 会漏掉
        所以同时比对「新消息」和「老消息内容发生变化」。
        """
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            try:
                msgs = await client.get_messages(entity, limit=8)
            except Exception:
                await asyncio.sleep(1.5)
                continue

            # 新的入站消息优先
            for m in msgs:
                if m.out:
                    continue
                text = (m.message or "").strip()
                if not text:
                    continue
                if (m.id or 0) not in baseline:
                    return self._extract_feedback(m)

            # 其次看老消息是否被编辑过
            for m in msgs:
                if m.out:
                    continue
                text = (m.message or "").strip()
                old = baseline.get(m.id or 0)
                if text and old is not None and text != old:
                    return self._extract_feedback(m)

            await asyncio.sleep(1.5)
        return ""

    @staticmethod
    async def _snapshot(client, entity) -> Dict[int, str]:
        """记录当前最近消息的 id → 文本，作为回执比对基线"""
        try:
            msgs = await client.get_messages(entity, limit=8)
        except Exception:
            return {}
        return {(m.id or 0): (m.message or "") for m in msgs if not m.out}

    @staticmethod
    def _already_done_today(msgs, keywords) -> Optional[str]:
        """
        检查机器人最近消息里是否已有"今天已签到"的证据。

        判据：消息文本命中成功关键词，且出现今天的日期（YYYY-MM-DD / YYYY年MM月DD日）。
        命中就跳过点击——重复点按钮机器人通常不回消息，反而让人误以为失败。
        """
        today = datetime.now()
        stamps = [
            today.strftime("%Y-%m-%d"),
            today.strftime("%Y年%m月%d日"),
            today.strftime("%m-%d"),
        ]
        for m in msgs:
            if m.out:
                continue
            text = m.message or ""
            if not text:
                continue
            if any(k in text for k in keywords) and any(s in text for s in stamps):
                return text.replace("\n", " · ")[:120]
        return None

    @staticmethod
    async def _click(btn) -> Optional[str]:
        """
        点击内联按钮，返回机器人弹窗文本（可能为空）。

        这是最容易漏的一环：很多签到机器人的结果**只**通过
        BotCallbackAnswer.message 以"今天已经签到"这类小提示返回，
        聊天记录里什么都不留。不读这个字段就只能看到"已点击按钮"，
        然后误判为成功。
        """
        try:
            ans = await btn.click()
        except Exception:
            raise
        if ans is None:
            return None
        text = getattr(ans, "message", None)
        return (text or "").strip() or None

    def _judge_popup(self, popup: str, keywords: List[str],
                     done_words: Optional[List[str]] = None):
        """
        根据弹窗文本判断结果。

        返回 (ok, detail, already)：
          ok=True, already=False  本次签到成功
          ok=True, already=True   今日已签（名额之前就用掉了，本次没签上）
          ok=False                明确的失败（如"未注册""请先加入频道"）
          ok=None                 弹窗内容无法判定，交给聊天消息判断
        """
        text = (popup or "").strip()
        if not text:
            return None, "", False

        done_words = done_words or ALREADY_WORDS
        fail_words = ["未注册", "未绑定", "请先", "失败", "无效", "不存在", "过期",
                      "积分不足", "权限", "禁止", "冷却", "稍后"]

        short = text.replace("\n", " · ")[:120]

        if any(w in text for w in done_words):
            return True, f"{ALREADY_PREFIX}{short}", True
        # 失败词必须在成功关键词之前判：有些 bot 的成功词宽到只写"签到"，
        # 先判关键词的话"签到失败"会被当成成功。
        if any(w in text for w in fail_words):
            return False, short, False
        if any(k in text for k in keywords):
            return True, short, False
        # 有些机器人弹"签到成功，+2 猫币"这类不含固定词的文案，
        # 只要弹窗不是失败语气、也不是"已签"，就当作成功
        return True, short, False

    async def _cleanup(self, client, sent_msgs: List[Any]) -> None:
        """
        删掉本次自己发的指令消息，减少历史污染。

        只删本次发出去的，绝不碰历史；删不掉也不影响签到结果，
        所以异常一律吞掉，只在日志里留一句。
        """
        if not sent_msgs:
            return
        try:
            await client.delete_messages(sent_msgs[0].chat_id,
                                         [m.id for m in sent_msgs])
            return
        except Exception as e:
            logger.debug("按 id 删除失败，改用实体删除: %s", e)
        for m in sent_msgs:
            try:
                await m.delete()
            except Exception:
                pass

    # ---------------- 探测（添加 bot 用） ----------------

    #: 按钮文案里出现这些词，就认为它是签到入口
    CHECKIN_HINTS = [
        "签到", "打卡", "checkin", "check-in", "check_in", "check in",
        "每日", "daily", "领积分", "领取", "sign in", "signin",
    ]

    #: 明显不是签到入口的按钮，探测时降权
    NOISE_HINTS = [
        "取消", "返回", "关闭", "帮助", "关于", "设定", "设置", "language",
        "english", "中文", "说明", "教程", "客服", "联系",
    ]

    @classmethod
    def score_button(cls, text: str) -> int:
        """给按钮文案打分，越高越可能是签到入口"""
        t = (text or "").lower()
        score = 0
        if any(h in t for h in cls.CHECKIN_HINTS):
            score += 10
        # "签到"两个字直接出现最可信
        if "签到" in t or "打卡" in t:
            score += 5
        if any(h in t for h in cls.NOISE_HINTS):
            score -= 20
        return score

    async def probe_bot(self, target: str, command: str = "/start",
                        wait: float = 4.0, send: bool = False) -> Dict[str, Any]:
        """
        探测一个 bot 的签到入口，供面板"添加任务"使用。

        ⚠️ 默认**不发任何消息**，只读最近的历史消息和按钮。
        原因：给 bot 乱发 /start 可能触发对面的频率限制甚至封禁，
        所以只有当历史里完全找不到按钮、且调用方明确传 send=true 时，
        才允许发一次命令（用于建任务前的最后确认）。

        绝不会连续发、也不会重复发。
        """
        client = await self.ensure_session()
        if not client:
            return {"ok": False, "error": "未连接 Telegram"}

        try:
            entity = await client.get_entity(target)
        except Exception as e:
            return {"ok": False, "error": f"找不到该机器人：{e}"}

        is_bot = getattr(entity, "bot", False)
        title = getattr(entity, "first_name", "") or target
        username = getattr(entity, "username", None)

        sent: List[Any] = []
        try:
            msgs = await client.get_messages(entity, limit=10)
            buttons = self._collect_buttons(msgs)

            # 历史里没按钮，且调用方明确允许发消息，才发一次
            if not buttons and send:
                self.add_log(f"探测 {target}：历史无菜单，发送一次 {command}", "warn")
                sent.append(await client.send_message(entity, command))
                await asyncio.sleep(wait)
                msgs = await client.get_messages(entity, limit=6)
                buttons = self._collect_buttons(msgs)

            uniq = self._rank_buttons(buttons)
            best = uniq[0] if uniq and uniq[0]["score"] > 0 else None

            tail = ""
            for m in msgs:
                if not m.out and (m.message or "").strip():
                    tail = (m.message or "").replace("\n", " · ")[:100]
                    break

            return {
                "ok": True,
                "target": target,
                "title": title,
                "username": username,
                "is_bot": is_bot,
                "buttons": uniq[:12],
                "suggest_button": best["text"] if best else None,
                "suggest_mode": "button" if uniq else "command",
                "last_message": tail,
                "sent_command": bool(sent),
                "note": "" if uniq else "历史消息里没有可用按钮，可能只支持命令签到",
            }
        finally:
            # 只清理"探测时自己发的那一条"，绝不碰历史
            if sent:
                await self._cleanup(client, sent)

    @staticmethod
    def _collect_buttons(msgs) -> List[Dict[str, Any]]:
        """从消息列表里收集按钮（越新的消息优先级越高，取第一个有按钮的）"""
        uniq: List[Dict[str, Any]] = []
        for m in sorted(msgs, key=lambda x: x.id or 0, reverse=True):
            rows = m.buttons or []
            if not rows:
                continue
            for row in rows:
                for b in row:
                    txt = (b.text or "").strip()
                    if txt:
                        uniq.append({
                            "text": txt,
                            "score": CheckinEngine.score_button(txt),
                            "msg_id": m.id,
                        })
            if uniq:
                break
        return uniq

    @staticmethod
    def _rank_buttons(buttons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen, out = set(), []
        for b in sorted(buttons, key=lambda x: -x["score"]):
            if b["text"] in seen:
                continue
            seen.add(b["text"])
            out.append(b)
        return out

    def _run_guard(self, task_id: str, limit: int = 3) -> Optional[int]:
        """
        同一天内对同一任务的真跑次数守卫。

        返回今日已真跑的次数；超过 limit 时调用方应跳过。
        目的是防止手滑连点、反复测试把 bot 惹毛（真实存在被限流/封禁的情况）。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        hist = self._run_history.setdefault(task_id, {"date": today, "count": 0})
        if hist["date"] != today:
            hist["date"], hist["count"] = today, 0
        if hist["count"] >= limit:
            return hist["count"]
        return None

    def _mark_run(self, task_id: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        hist = self._run_history.setdefault(task_id, {"date": today, "count": 0})
        if hist["date"] != today:
            hist["date"], hist["count"] = today, 0
        hist["count"] += 1

    async def run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个任务，返回 {id,name,ok,detail}"""
        task_id = task.get("id", "unknown")
        name = task.get("name", "未命名")
        target = (task.get("target_bot") or "").strip()
        mode = task.get("mode", "button")
        command = (task.get("command") or "/start").strip()
        pattern = (task.get("button_match") or "签到").strip()
        wait = int(task.get("wait_seconds", 4) or 4)
        keywords = task.get("success_keywords") or ["签到成功"]

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client = await self.ensure_session()
        if not client:
            detail = "未连接 Telegram，请先在设置里点「接管桌面端会话」"
            self.add_log(f"[{name}] {detail}", "error")
            config_mgr.update_task_status(task_id, detail, now, False)
            return {"id": task_id, "name": name, "ok": False, "detail": detail}

        self.add_log(f"▶ 开始 [{name}] → {target}（{mode}）")

        try:
            entity = await client.get_entity(target)
            baseline = await self._snapshot(client, entity)

            # 先判断今天是否已经签过。命中就**完全不发消息、不点按钮**，
            # 既省事也避免频繁打扰 bot 被对面限流或封禁。
            try:
                recent = await client.get_messages(entity, limit=8)
            except Exception:
                recent = []
            done_today = self._already_done_today(recent, keywords)
            if done_today and not task.get("force"):
                detail = f"今日已签到 · {done_today}"
                self.add_log(f"[{name}] ✓ {detail}（未发送任何消息）", "success")
                config_mgr.update_task_status(task_id, detail, now, True)
                return {"id": task_id, "name": name, "ok": True,
                        "detail": detail, "cached": True, "already": True}

            # 同一天内对同一 bot 最多真跑 N 次，防止手滑连点被对面判定为滥用
            guard = self._run_guard(task_id)
            if guard and not task.get("force"):
                detail = f"今日已执行 {guard} 次，为避免打扰 bot 已跳过"
                self.add_log(f"[{name}] ⏸ {detail}", "warn")
                return {"id": task_id, "name": name, "ok": False, "detail": detail}

            clicked_text = None
            popup: Optional[str] = None
            sent_msgs: List[Any] = []   # 本次自己发出去的指令，成功后可清理
            self._mark_run(task_id)     # 计入当日真跑次数

            if mode == "command":
                sent_msgs.append(await client.send_message(entity, command))
                self.add_log(f"[{name}] 已发送 {command}，等待 {wait}s")
                await asyncio.sleep(wait)
            else:
                # fresh_menu=True      每次都要先发指令刷新菜单（默认）
                # fresh_menu=False     允许复用现有菜单（该 bot 的菜单不会被覆盖时才安全）
                fresh_menu = task.get("fresh_menu", True)
                min_id = 0

                if fresh_menu:
                    min_id = await self._latest_msg_id(client, entity)
                    sent_msgs.append(await client.send_message(entity, command))
                    self.add_log(f"[{name}] 先发 {command} 刷新菜单，等待 {wait}s")
                    await asyncio.sleep(wait)
                else:
                    btn = await self._find_clickable(client, entity, pattern)
                    if btn is not None:
                        self.add_log(f"[{name}] 复用现有菜单（fresh_menu=false）")
                    else:
                        min_id = await self._latest_msg_id(client, entity)
                        sent_msgs.append(await client.send_message(entity, command))
                        self.add_log(f"[{name}] 没有可用菜单，发 {command} 唤出，等待 {wait}s")
                        await asyncio.sleep(wait)

                btn = await self._find_clickable(client, entity, pattern, min_id=min_id)

                if btn is None and not sent_msgs:
                    # 复用失败兜底：发指令再找一次
                    min_id = await self._latest_msg_id(client, entity)
                    sent_msgs.append(await client.send_message(entity, command))
                    self.add_log(f"[{name}] 现有菜单里没找到，重发 {command} 再找", "warn")
                    await asyncio.sleep(wait)
                    btn = await self._find_clickable(client, entity, pattern, min_id=min_id)

                if btn is None:
                    detail = f"没找到匹配「{pattern}」的按钮"
                    self.add_log(f"[{name}] {detail}", "warn")
                    config_mgr.update_task_status(task_id, detail, now, False)
                    return {"id": task_id, "name": name, "ok": False, "detail": detail}

                clicked_text = btn.text
                self.add_log(f"[{name}] 点击按钮「{clicked_text}」")
                try:
                    popup = await self._click(btn)
                except Exception as click_err:
                    # 按钮失效：刷新菜单再来一次
                    self.add_log(f"[{name}] 按钮点击失败（{click_err}），刷新菜单重试", "warn")
                    min_id = await self._latest_msg_id(client, entity)
                    sent_msgs.append(await client.send_message(entity, command))
                    await asyncio.sleep(wait)
                    btn = await self._find_clickable(client, entity, pattern, min_id=min_id)
                    if btn is None:
                        raise
                    clicked_text = btn.text
                    popup = await self._click(btn)
                await asyncio.sleep(3)

            cleanup = bool(task.get("cleanup_command", True))

            # ① 弹窗回执优先：很多机器人的结果只在 callback 弹窗里，不进聊天记录
            if popup:
                ok_popup, detail_popup, already_popup = self._judge_popup(popup, keywords)
                if ok_popup is not None:
                    mark = "✓ " if already_popup else ("✅ " if ok_popup else "⚠️ ")
                    self.add_log(
                        f"[{name}] {mark}{detail_popup}",
                        "success" if ok_popup else "warn",
                    )
                    config_mgr.update_task_status(task_id, detail_popup, now, ok_popup)
                    if cleanup:
                        await self._cleanup(client, sent_msgs)
                    return {"id": task_id, "name": name, "ok": ok_popup,
                            "detail": detail_popup, "source": "popup",
                            "already": already_popup}

            # ② 聊天消息回执
            feedback = await self._collect_feedback(client, entity, baseline)

            ok = any(k in feedback for k in keywords) if feedback else bool(clicked_text)
            detail = feedback or (f"已点击「{clicked_text}」" if clicked_text else f"已发送 {command}")
            # 机器人说"今天已经签过了"时，detail 是它自己的原文，
            # 不带"今日已签"字样，前端就分不出黄绿，必须在这里统一打标。
            already = bool(feedback) and any(w in feedback for w in ALREADY_WORDS)
            if already and not detail.startswith(ALREADY_PREFIX):
                detail = f"{ALREADY_PREFIX}{detail}"
            mark = "✓ " if already else ("✅ " if ok else "⚠️ ")
            self.add_log(
                f"[{name}] {mark}{(detail or '无回执')}"
                + ("" if feedback else "（机器人未回消息）"),
                "success" if ok else "warn",
            )
            config_mgr.update_task_status(task_id, detail, now, ok)
            if cleanup and ok:
                await self._cleanup(client, sent_msgs)
            return {"id": task_id, "name": name, "ok": ok, "detail": detail,
                    "already": already}

        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            self.add_log(f"[{name}] ❌ {detail}", "error")
            config_mgr.update_task_status(task_id, detail, now, False)
            return {"id": task_id, "name": name, "ok": False, "detail": detail}

    async def run_all(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        async with self._lock:
            tasks = [t for t in config_mgr.get_tasks()
                     if t.get("enabled", True) or not only_enabled]
            if not tasks:
                self.add_log("没有启用的任务", "warn")
                return []
            self.add_log(f"开始执行 {len(tasks)} 个签到任务")
            results = []
            for i, t in enumerate(tasks):
                results.append(await self.run_task(t))
                if i < len(tasks) - 1:
                    await asyncio.sleep(3)
            ok_n = sum(1 for r in results if r["ok"])
            self.add_log(f"全部完成：{ok_n}/{len(results)} 成功",
                         "success" if ok_n == len(results) else "warn")
            return results

    async def run_one(self, task_id: str, force: bool = False) -> Dict[str, Any]:
        async with self._lock:
            task = config_mgr.get_task(task_id)
            if not task:
                return {"id": task_id, "name": task_id, "ok": False, "detail": "任务不存在"}
            if force:
                task = {**task, "force": True}
            return await self.run_task(task)


engine = CheckinEngine()
