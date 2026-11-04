# -*- coding: utf-8 -*-
"""hmu2 独立 TG 会话登录(与桌面端彻底解绑,不再共用一个授权密钥)。

两步走:
  1. python relogin.py send  +8613xxxxxxxxx   →  TG App/短信收验证码
  2. python relogin.py verify 12345           →  登录成功,新会话落盘

新会话先写 config/hmu_session.session,成功后才替换 config/tg_session.session
(旧的留着当备份,验证码错误/2FA 都不丢)。
"""
import asyncio
import os
import shutil
import sys

sys.path.insert(0, "/data/workspace/tg_checkin")
import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.errors import (PhoneCodeExpiredError, PhoneCodeInvalidError,  # noqa: E402
                             SessionPasswordNeededError)

BASE = "/data/workspace/tg_checkin"
NEW = os.path.join(BASE, "config", "hmu_session.session")
OLD = os.path.join(BASE, "config", "tg_session.session")
STATE = os.path.join(BASE, "config", "relogin_state.json")


async def _send(phone):
    client = TelegramClient(NEW, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.connect()
    sent = await client.send_code_request(phone)
    import json
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"phone": phone, "phone_code_hash": sent.phone_code_hash}, f)
    print(f"CODE_SENT hash={sent.phone_code_hash[:12]}… 请到 TG App/短信收验证码")
    await client.disconnect()


async def _verify(code):
    import json
    with open(STATE, encoding="utf-8") as f:
        st = json.load(f)
    client = TelegramClient(NEW, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.connect()
    try:
        await client.sign_in(phone=st["phone"], code=code,
                             phone_code_hash=st["phone_code_hash"])
    except SessionPasswordNeededError:
        print("NEED_2FA 账号开了两步验证,把云端密码给我再 complete")
        await client.disconnect()
        return
    me = await client.get_me()
    await client.disconnect()
    # 成功:备份旧的,新的上位
    if os.path.exists(OLD):
        shutil.copy(OLD, OLD + ".revoked-bak")
    shutil.move(NEW, OLD)
    os.remove(STATE)
    print(f"LOGIN_OK user={me.username or me.id} id={me.id}")
    print("新会话已就位(独立密钥),桌面端之后随便开,不会再冲突")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("send", "verify"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "send":
        asyncio.run(_send(sys.argv[2].strip()))
    else:
        asyncio.run(_verify(sys.argv[2].strip()))
