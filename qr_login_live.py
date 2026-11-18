# -*- coding: utf-8 -*-
"""扫码登录 TG:持续刷新二维码,每次换码都重写 qr.txt / qr_meta.txt。

与 qr_login.py 的差别
--------------------
Telegram 的登录 token 大约 30 秒就过期,原脚本只把二维码写一次 ——
外部(比如本机把它渲染成图片给人扫)拿到的很快就成了废码。
这里每次 recreate 都重写文件,并写一个 gen 计数 + 时间戳,
取文件的一侧据此判断要不要重新渲染。

不改 qr_login.py:它还在用,两套互不干扰。成功后同样把
config/hmu_session.session 顶替成 config/tg_session.session。
"""
import asyncio
import io
import os
import shutil
import sys
import time

sys.path.insert(0, "/data/workspace/tg_checkin")
import tdata_session  # noqa: E402
from telethon import TelegramClient  # noqa: E402
from telethon.errors import SessionPasswordNeededError  # noqa: E402

BASE = "/data/workspace/tg_checkin"
NEW = os.path.join(BASE, "config", "hmu_session.session")
OLD = os.path.join(BASE, "config", "tg_session.session")
QR = os.path.join(BASE, "qr.txt")
META = os.path.join(BASE, "qr_meta.txt")
STATUS = os.path.join(BASE, "qr_status.txt")

_gen = 0


def w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_qr(url):
    """把当前 token 画成 ASCII 二维码落盘,并记下 gen。"""
    global _gen
    _gen += 1
    import qrcode
    q = qrcode.QRCode(border=1)
    q.add_data(url)
    q.make(fit=True)
    buf = io.StringIO()
    q.print_ascii(out=buf)
    w(QR, buf.getvalue())
    w(META, "gen=%d\nts=%s\nurl=%s\n" % (_gen, time.strftime("%H:%M:%S"), url))


async def main(total=600, slice_s=25):
    w(STATUS, "WAITING")
    client = TelegramClient(NEW, tdata_session.TDESKTOP_API_ID,
                            tdata_session.TDESKTOP_API_HASH)
    await client.connect()
    qr = await client.qr_login()
    write_qr(qr.url)

    deadline = time.time() + total
    done = False
    while time.time() < deadline:
        try:
            await qr.wait(timeout=slice_s)
            done = True
            break
        except asyncio.TimeoutError:
            try:
                await qr.recreate()
                write_qr(qr.url)
            except Exception as e:
                w(STATUS, "FAIL recreate %s: %s" % (type(e).__name__, str(e)[:80]))
                await client.disconnect()
                return
        except SessionPasswordNeededError:
            w(STATUS, "NEED_2FA 账号开了两步验证,要云端密码")
            await client.disconnect()
            return
        except Exception as e:
            w(STATUS, "FAIL %s: %s" % (type(e).__name__, str(e)[:100]))
            await client.disconnect()
            return

    if not done:
        w(STATUS, "EXPIRED 窗口内没人扫")
        await client.disconnect()
        return

    me = await client.get_me()
    await client.disconnect()
    if os.path.exists(OLD):
        shutil.copy(OLD, OLD + ".revoked-bak")
    shutil.move(NEW, OLD)
    w(STATUS, "OK user=%s id=%s" % (me.username or me.id, me.id))


if __name__ == "__main__":
    asyncio.run(main())
