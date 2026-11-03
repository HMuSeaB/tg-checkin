# -*- coding: utf-8 -*-
"""tg_checkin 服务器无头 runner:跑完所有启用任务 + MINIAPP 双签到。

用法: python runner.py [--force]
每日唤醒编排(外部 cron)先 wake 再 exec 本脚本;重复触发当天自动跳过。

2026-11-03 起增加 miniapp_checkin:网页版签到是独立流,与 bot 签到
双倍奖励(实测 bot +20、miniapp +20 都入账)。
"""

import asyncio
import json
import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

from checkin_engine import engine  # noqa: E402

STATE = os.path.join(BASE, "server_state.json")


def _state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run_miniapp():
    """网页版签到(独立进程跑,失败不影响 bot 签到结果)。"""
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, os.path.join(BASE, "miniapp_checkin.py")],
            capture_output=True, text=True, timeout=180, cwd=BASE)
        out = (r.stdout or "").strip()
        line = [l for l in out.splitlines() if l.startswith("RESULT:")]
        msg = line[-1][len("RESULT: "):] if line else out[-120:]
        return msg.startswith("OK"), msg
    except Exception as e:
        return False, f"miniapp 执行异常: {str(e)[:80]}"


async def main():
    force = "--force" in sys.argv
    st = _state()
    today = date.today().isoformat()
    if st.get("date") == today and not force:
        print(json.dumps({"ok": True, "skipped": True,
                          "reason": "今天已跑过(" + str(st.get("summary")) + ")"},
                         ensure_ascii=False))
        return
    # MINIAPP 双签到必须**先**跑:它是独立进程,要开同一个 tg session 文件;
    # engine 的 client 一旦连上会占着 session 的 SQLite 锁,子进程就打不开
    # 了(2026-11-03 实测:后跑则静默失败、stdout 为空)
    m_ok, m_msg = run_miniapp()

    results = await engine.run_all(only_enabled=True)
    out = [{"id": r.get("id"), "name": r.get("name"), "ok": bool(r.get("ok")),
            "detail": str(r.get("detail"))[:140]} for r in results]
    ok_n = sum(1 for r in out if r["ok"])

    out.append({"id": "miniapp", "name": "网页版签到", "ok": m_ok,
                "detail": m_msg[:140]})
    if m_ok:
        ok_n += 1

    total = len(out)
    json.dump({"date": today,
               "summary": f"{ok_n}/{total}"},
              open(STATE, "w", encoding="utf-8"))
    print(json.dumps({"ok": ok_n == total and total > 0,
                      "summary": f"{ok_n}/{total} 成功",
                      "results": out,
                      "logs": [e["text"] for e in engine.logs[-25:]]},
                     ensure_ascii=False))


asyncio.run(main())
