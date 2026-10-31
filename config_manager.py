"""配置管理：任务定义与运行偏好持久化"""

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("TGCheckin.Config")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "tg_session")


def _default_tasks() -> List[Dict[str, Any]]:
    """首次启动的示例任务

    只是格式演示，默认全部禁用。实际用面板上的「添加签到机器人」探测后添加，
    避免把别人的私人 bot 写死在仓库里。

    mode:
      "button"  发指令唤出菜单后点按钮
      "command" 直接发命令完成签到
    """
    return [
        {
            "id": "demo_button",
            "name": "示例：点按钮签到",
            "target_bot": "@YourEmbyBot",
            "mode": "button",
            "command": "/start",
            "button_match": r"签到",
            "wait_seconds": 4,
            "cron_time": "08:30",
            "success_keywords": ["签到成功", "成功", "已经签过", "已签到"],
            # 该 bot 点签到后会不会把菜单消息本身改成结果，不确定时保持 True
            "fresh_menu": True,
            "cleanup_command": True,
            "enabled": False,
            "last_run": None,
            "last_status": "未运行",
            "last_ok": None,
        },
        {
            "id": "demo_command",
            "name": "示例：发命令签到",
            "target_bot": "@YourCheckinBot",
            "mode": "command",
            "command": "/checkin",
            "button_match": r"签到",
            "wait_seconds": 4,
            "cron_time": "08:32",
            "success_keywords": ["签到成功", "已完成", "已经签过", "已签到"],
            "fresh_menu": True,
            "cleanup_command": False,
            "enabled": False,
            "last_run": None,
            "last_status": "未运行",
            "last_ok": None,
        },
    ]


DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {
        "tdata_dir": "",          # 留空自动探测
        "active_user_id": None,   # 留空自动取第一个账号
        "proxy": {
            "enabled": True,
            "type": "socks5",
            "addr": "127.0.0.1",
            "port": 7890,
        },
    },
    "settings": {
        "auto_start_scheduler": True,
        "random_jitter_max_seconds": 30,
        "notify_saved_messages": False,
    },
    "tasks": _default_tasks(),
}

_lock = threading.Lock()


class ConfigManager:
    def __init__(self, filepath: str = CONFIG_FILE):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.config_data = self.load()

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.filepath):
            self._write(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("读取配置失败，使用默认值: %s", e)
            return json.loads(json.dumps(DEFAULT_CONFIG))

        # 补齐缺失结构
        for key, val in DEFAULT_CONFIG.items():
            data.setdefault(key, json.loads(json.dumps(val)))

        # 兼容旧版遗留的占位任务
        tasks = [t for t in data.get("tasks", [])
                 if t.get("target_bot") != "@example_emby_bot"]
        for t in tasks:
            t.setdefault("mode", "button")
            t.setdefault("success_keywords", ["签到成功", "成功"])
            t.setdefault("fresh_menu", True)
            t.setdefault("cleanup_command", True)
        # 注意：tasks 为空时**不要**回填示例任务。
        # 用户删光任务是一个正常的操作结果，塞回去会让人很困惑。
        data["tasks"] = tasks
        return data

    def _write(self, data: Dict[str, Any]) -> bool:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error("写入配置失败: %s", e)
            return False

    def save(self, data: Optional[Dict[str, Any]] = None) -> bool:
        with _lock:
            if data is not None:
                self.config_data = data
            return self._write(self.config_data)

    # ---------------- 读取便捷方法 ----------------

    def get_telegram_config(self) -> Dict[str, Any]:
        return self.config_data.get("telegram", {})

    def get_settings(self) -> Dict[str, Any]:
        return self.config_data.get("settings", {})

    def get_tasks(self) -> List[Dict[str, Any]]:
        return self.config_data.get("tasks", [])

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.get_tasks() if t.get("id") == task_id), None)

    def get_proxy(self):
        p = self.get_telegram_config().get("proxy", {})
        if not p.get("enabled"):
            return None
        return (p.get("type", "socks5"), p.get("addr", "127.0.0.1"), int(p.get("port", 7890)))

    def update_task_status(self, task_id: str, status: str, run_time: str,
                           ok: Optional[bool] = None) -> None:
        for t in self.config_data.get("tasks", []):
            if t.get("id") == task_id:
                t["last_status"] = status
                t["last_run"] = run_time
                t["last_ok"] = ok
                break
        self.save()


config_mgr = ConfigManager()
