"""定时调度：每个任务按 cron_time 每日自动签到"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from checkin_engine import engine
from config_manager import config_mgr

logger = logging.getLogger("TGCheckin.Scheduler")


class CheckinScheduler:
    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.is_running = False

    def start(self) -> None:
        if self.scheduler and self.scheduler.running:
            return
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        self.is_running = True
        engine.add_log("定时调度已启动")
        self.reload_jobs()

    def reload_jobs(self) -> None:
        if not self.scheduler:
            return
        self.scheduler.remove_all_jobs()
        count = 0
        for task in config_mgr.get_tasks():
            if not task.get("enabled", True):
                continue
            try:
                hh, mm = _parse_time(task.get("cron_time", "08:30"))
            except Exception as e:
                engine.add_log(f"任务 [{task.get('name')}] 时间格式错误：{e}", "warn")
                continue
            self.scheduler.add_job(
                self._fire,
                trigger=CronTrigger(hour=hh, minute=mm),
                args=[task["id"]],
                id=f"job_{task['id']}",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            count += 1
            engine.add_log(f"已登记定时：{task.get('name')} 每天 {hh:02d}:{mm:02d}")
        engine.add_log(f"定时任务生效 {count} 个")

    async def _fire(self, task_id: str) -> None:
        jitter = int(config_mgr.get_settings().get("random_jitter_max_seconds", 0) or 0)
        if jitter > 0:
            wait = random.randint(0, jitter)
            engine.add_log(f"定时触发，随机等待 {wait}s 后执行（防风控）")
            await asyncio.sleep(wait)
        await engine.run_one(task_id)


def _parse_time(s: str):
    parts = (s or "").replace("：", ":").strip().split(":")
    hh = int(parts[0])
    mm = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("超出 00:00~23:59")
    return hh, mm


scheduler_mgr = CheckinScheduler()
