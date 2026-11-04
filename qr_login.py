# -*- coding: utf-8 -*-
"""hmu3 扫码登录 TG(独立会话,不用手机号)。

后台跑:二维码 ASCII 写 qr.txt,状态写 qr_status.txt。
  WAITING → 二维码已出(约 60 秒有效),等扫
  OK user=xxx → 登录成功,会话已落盘
  NEED_2FA → 扫了但要云端密码
  EXPIRED → 二维码过期没人扫,重跑本脚本
  FAIL ... → 看 qr.log

用户侧:手机 TG → 设置 → 设备 → 连接桌面设备 → 扫 qr.txt 里的码。
"""
import asyncio
import os
import shutil
import sys

sys.path.insert(0, "/data/workspace/tg_checkin")
import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.errors import SessionPasswordNeededError  # noqa: E402

BASE = "/data/workspace/tg_checkin"
NEW = os.path.join(BASE, "config", "hmu_session.session")
OLD = os.path.join(BASE, "config", "tg_session.session")
QR = os.path.join(BASE, "qr.txt")
STATUS = os.path.join(BASE, "qr_status.txt")


def status(msg):
    with open(STATUS, "w", encoding="utf-8") as f:
        f.write(msg)


def render_qr(url):
    """把 tg://login URL 渲染成 ASCII 二维码写文件。"""
    import qrcode
    q = qrcode.QRCode(border=1)
    q.add_data(url)
    q.make(fit=True)
    with open(QR, "w", encoding="utf-8") as f:
        q.print_ascii(out=f)


async def main():
    status("WAITING")
    client = TelegramClient(NEW, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.connect()
    qr = await client.qr_login()          # 马上返回,带 .url
    render_qr(qr.url)                     # 二维码落盘,等用户扫
    try:
        await asyncio.wait_for(qr.wait(), timeout=110)
    except asyncio.TimeoutError:
        status("EXPIRED 二维码 110 秒没人扫,重跑 qr_login.py")
        await client.disconnect()
        return
    except SessionPasswordNeededError:
        status("NEED_2FA 账号开了两步验证,把云端密码告诉我")
        await client.disconnect()
        return
    except Exception as e:
        status(f"FAIL {type(e).__name__}: {str(e)[:100]}")
        await client.disconnect()
        return
    me = await client.get_me()
    await client.disconnect()
    if os.path.exists(OLD):
        shutil.copy(OLD, OLD + ".revoked-bak")
    shutil.move(NEW, OLD)
    status(f"OK user={me.username or me.id} id={me.id}")


asyncio.run(main())
