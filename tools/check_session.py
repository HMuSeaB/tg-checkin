# -*- coding: utf-8 -*-
"""检查某个 session 文件的登录态，不发送任何消息。

用法:
    python tools/check_session.py config/tg_session.session

输出一行:
    PATH=... AUTH=True USER=HMuSeaB ID=5351091022
    PATH=... ERROR=AuthKeyDuplicatedError ...
    PATH=... AUTH=False USER=- ID=-        # 文件在但授权已失效

放在 tools/ 下是为了本机和服务器共用同一份——部署脚本会把它一起传上去。
"""
import asyncio
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402


async def main():
    rel = sys.argv[1] if len(sys.argv) > 1 else "config/tg_session.session"
    path = rel if os.path.isabs(rel) else os.path.join(BASE, rel)
    if not os.path.exists(path):
        print("PATH=%s ERROR=file_not_found" % rel)
        return

    client = TelegramClient(path, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH, timeout=25)
    try:
        await client.connect()
        ok = await client.is_user_authorized()
        me = await client.get_me() if ok else None
        print("PATH=%s AUTH=%s USER=%s ID=%s" % (
            rel, ok, (me.username if me else "-"), (me.id if me else "-")))
    except Exception as e:
        print("PATH=%s ERROR=%s %s" % (rel, type(e).__name__, str(e)[:140]))
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
