# -*- coding: utf-8 -*-
"""完成扫码登录的两步验证(2FA)。

背景:扫码被接受后账号要求云端密码,Telethon 抛 SessionPasswordNeededError。
此时 auth_key 已经落在 config/hmu_session.session 里,再连一次、带上密码
sign_in 就能收尾。

密码不从命令行传(会出现在进程列表和 shell 历史里),而是由面板写进
config/.tg2fa(chmod 600),这里读完立刻删掉。

结果写 qr_status.txt:OK user=... / FAIL ...
"""
import asyncio
import os
import shutil
import sys

sys.path.insert(0, "/data/workspace/tg_checkin")
import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402

BASE = "/data/workspace/tg_checkin"
NEW = os.path.join(BASE, "config", "hmu_session.session")
OLD = os.path.join(BASE, "config", "tg_session.session")
PWD = os.path.join(BASE, "config", ".tg2fa")
STATUS = os.path.join(BASE, "qr_status.txt")


def w(text):
    with open(STATUS, "w", encoding="utf-8") as f:
        f.write(text)


def drop_pwd():
    try:
        os.remove(PWD)
    except OSError:
        pass


async def main():
    if not os.path.exists(PWD):
        w("FAIL 没收到 2FA 密码")
        return
    pwd = open(PWD, encoding="utf-8").read().strip()
    if not pwd:
        drop_pwd()
        w("FAIL 2FA 密码为空")
        return

    client = TelegramClient(NEW, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.sign_in(password=pwd)
        me = await client.get_me()
    except Exception as e:
        drop_pwd()
        w("FAIL %s: %s" % (type(e).__name__, str(e)[:90]))
        await client.disconnect()
        return

    await client.disconnect()
    drop_pwd()
    if os.path.exists(OLD):
        shutil.copy(OLD, OLD + ".revoked-bak")
    shutil.move(NEW, OLD)
    w("OK user=%s id=%s" % (me.username or me.id, me.id))


if __name__ == "__main__":
    asyncio.run(main())
