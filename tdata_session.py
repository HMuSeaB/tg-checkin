"""从本机已登录的 Telegram Desktop 直接接管会话

为什么不用 api_id / api_hash 重新登录
------------------------------------
重新走一次"发验证码→输入验证码"会让桌面端掉线（TG 同一账号的活跃会话数有限），
而且每台机器都要手动录一次。更省事的做法是：把 tdata 里已经授权好的
auth_key 直接搬到 Telethon 的 session 里 —— 等效于"这台电脑本来就登录着"。

已验证的 tdata 结构
-------------------
tdata/key_datas                     TDF 容器，明文段是 salt / 加密 localKey / 加密账户索引
tdata/<16位HEX>/...                  每个账号一个目录
tdata/<16位HEX>s                     该账号的 MTP 授权数据（user_id、主 DC、各 DC 的 auth_key）

TDF 容器：
  [0:4]   b"TDF$"
  [4:8]   version (int32 LE)
  [8:-16] body
  [-16:]  md5(body + len(body) + version + magic)

key_datas 明文段是三个 Qt QByteArray（大端 uint32 长度 + 数据）：
  1. 32 字节 salt
  2. 加密 localKey（用 rootKey 加密，rootKey 由空密码派生）
  3. 加密账户索引
  4. （可选）trailer，读其 wrapCount 判断是否设了本地密码

rootKey  = PBKDF2-HMAC-SHA512(SHA512(salt + "" + salt), salt, 1, 256)
localKey = DecryptLocal(加密块, rootKey)  → 268 字节，前 256 字节是 MTP 主密钥

MTP 授权数据（解密后）：
  块 id 0x4B → QByteArray(serialized)
  serialized: [0xFFFFFFFF, 0xFFFFFFFF, userId(8), mainDcId(4),
               keyCount(4), (dcId(4), key(256)) × N, keysToDestroy...]
"""

from __future__ import annotations

import glob
import hashlib
import os
import struct
from io import BytesIO
from typing import Dict, List, Optional, Tuple

TDF_MAGIC = b"TDF$"
LOCAL_SALT_SIZE = 32
DBI_MTP_AUTHORIZATION = 0x4B
WIDE_IDS_TAG = 0xFFFFFFFFFFFFFFFF


# --------------------------------------------------------------------------
# AES-256 IGE（纯 Python，免去 pycryptodome / tgcrypto 依赖）
# --------------------------------------------------------------------------

class _AESCore:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("AES-256 需要 32 字节密钥")
        self.nr = 14
        self._sbox = _build_sbox()
        self._inv_sbox = [0] * 256
        for i, v in enumerate(self._sbox):
            self._inv_sbox[v] = i
        self._w = self._expand_key(key)

    def _expand_key(self, key: bytes) -> List[List[int]]:
        nk = 8
        w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
        rcon = 1
        for i in range(nk, 4 * (self.nr + 1)):
            temp = list(w[i - 1])
            if i % nk == 0:
                temp = [self._sbox[b] for b in temp[1:] + temp[:1]]
                temp[0] ^= rcon
                rcon = _xtime(rcon)
            elif i % nk == 4:
                temp = [self._sbox[b] for b in temp]
            w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
        return w

    def decrypt_block(self, block: bytes) -> bytes:
        s = [[block[4 * c + r] for c in range(4)] for r in range(4)]
        self._add_round_key(s, self.nr)
        for rnd in range(self.nr - 1, 0, -1):
            s = [[s[r][(c - r) % 4] for c in range(4)] for r in range(4)]
            s = [[self._inv_sbox[b] for b in row] for row in s]
            self._add_round_key(s, rnd)
            s = self._inv_mix_columns(s)
        s = [[s[r][(c - r) % 4] for c in range(4)] for r in range(4)]
        s = [[self._inv_sbox[b] for b in row] for row in s]
        self._add_round_key(s, 0)
        return bytes(s[r][c] for c in range(4) for r in range(4))

    def _add_round_key(self, s, rnd):
        for c in range(4):
            word = self._w[rnd * 4 + c]
            for r in range(4):
                s[r][c] ^= word[r]

    @staticmethod
    def _inv_mix_columns(s):
        out = [[0] * 4 for _ in range(4)]
        for c in range(4):
            a0, a1, a2, a3 = s[0][c], s[1][c], s[2][c], s[3][c]
            out[0][c] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
            out[1][c] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
            out[2][c] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
            out[3][c] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)
        return out


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        a = _xtime(a)
    return p & 0xFF


_SBOX: Optional[List[int]] = None


def _build_sbox() -> List[int]:
    global _SBOX
    if _SBOX is not None:
        return _SBOX
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ \
            ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    _SBOX = sbox
    return sbox


def aes_ige_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(iv) != 32:
        raise ValueError("IGE 的 iv 必须是 32 字节")
    if len(data) % 16:
        raise ValueError("IGE 数据长度必须是 16 的倍数")
    core = _AESCore(key)
    out = bytearray(len(data))
    prev_c, prev_p = iv[:16], iv[16:]
    for off in range(0, len(data), 16):
        c = data[off:off + 16]
        x = bytes(a ^ b for a, b in zip(c, prev_p))
        p = core.decrypt_block(x)
        p = bytes(a ^ b for a, b in zip(p, prev_c))
        out[off:off + 16] = p
        prev_c, prev_p = c, p
    return bytes(out)


# --------------------------------------------------------------------------
# Telegram 本地加密封装
# --------------------------------------------------------------------------

def aes_decrypt_local(src: bytes, auth_key: bytes, key128: bytes) -> bytes:
    x = 8
    sha1_a = hashlib.sha1(key128 + auth_key[x:x + 32]).digest()
    sha1_b = hashlib.sha1(auth_key[x + 32:x + 48] + key128 + auth_key[x + 48:x + 64]).digest()
    sha1_c = hashlib.sha1(auth_key[x + 64:x + 96] + key128).digest()
    sha1_d = hashlib.sha1(key128 + auth_key[x + 96:x + 128]).digest()
    aes_key = sha1_a[:8] + sha1_b[8:20] + sha1_c[4:16]
    aes_iv = sha1_a[8:20] + sha1_b[:8] + sha1_c[16:20] + sha1_d[:8]
    return aes_ige_decrypt(src, aes_key, aes_iv)


def decrypt_local(encrypted: bytes, auth_key: bytes) -> bytes:
    """
    DecryptLocal：前 16 字节校验，其后 int32 长度 + 正文。

    返回【未截断】的完整明文（含 4 字节长度头）。
    注：早先的实现误以为要截到 data_len，会让后续解析整体错位。
    """
    if len(encrypted) <= 16 or len(encrypted) % 16:
        raise ValueError("加密块长度异常")
    if len(auth_key) < 256:
        auth_key = auth_key + bytes(256 - len(auth_key))
    decrypted = aes_decrypt_local(encrypted[16:], auth_key, encrypted[:16])
    data_len = struct.unpack("<i", decrypted[:4])[0]
    if not (0 < data_len <= len(decrypted) - 4):
        raise ValueError("长度字段异常，密钥可能不匹配")
    return decrypted


def create_local_key(salt: bytes, passcode: bytes = b"") -> bytes:
    """
    CreateLocalKey：输出 256 字节（不是 32）。
    输出长度会影响首块的 PBKDF2 末段处理，所以必须和客户端一致。
    """
    pre = hashlib.sha512(salt + passcode + salt).digest()
    iters = 1 if not passcode else 100_000
    return hashlib.pbkdf2_hmac("sha512", pre, salt, iters, 256)


# --------------------------------------------------------------------------
# TDF 容器
# --------------------------------------------------------------------------

def read_tdf(path: str) -> Tuple[bytes, int]:
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 24 or raw[:4] != TDF_MAGIC:
        raise ValueError(f"{os.path.basename(path)} 不是 TDF 文件")
    version = struct.unpack("<i", raw[4:8])[0]
    body = raw[8:-16]
    digest = hashlib.md5(
        body + struct.pack("<i", len(body)) + struct.pack("<i", version) + TDF_MAGIC
    ).digest()
    if digest != raw[-16:]:
        raise ValueError(f"{os.path.basename(path)} 校验失败（可能已损坏）")
    return body, version


def _read_qbytearray(stream: BytesIO) -> bytes:
    head = stream.read(4)
    if len(head) < 4:
        return b""
    n = struct.unpack(">I", head)[0]
    if n == 0xFFFFFFFF:
        return b""
    return stream.read(n)


# --------------------------------------------------------------------------
# 目录探测
# --------------------------------------------------------------------------

def find_running_telegram_exe() -> Optional[str]:
    """从运行中的 Telegram.exe 反推便携版路径"""
    if os.name != "nt":
        return None
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process Telegram -ErrorAction SilentlyContinue | "
             "Select-Object -First 1).Path"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        p = out.stdout.strip()
        return p if p and os.path.isfile(p) else None
    except Exception:
        return None


def find_tdata_dirs() -> List[str]:
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "Telegram Desktop", "tdata"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Telegram Desktop", "tdata"),
    ]
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key)
        if base:
            candidates.append(os.path.join(base, "Telegram Desktop", "tdata"))
    exe = find_running_telegram_exe()
    if exe:
        candidates.insert(0, os.path.join(os.path.dirname(exe), "tdata"))
    seen, out = set(), []
    for c in candidates:
        if c and os.path.isdir(c):
            real = os.path.realpath(c)
            if real not in seen:
                seen.add(real)
                out.append(c)
    return out


# --------------------------------------------------------------------------
# key_datas → localKey
# --------------------------------------------------------------------------

def extract_local_key(tdata: str) -> bytes:
    key_file = None
    for cand in ("key_datas", "key_data"):
        p = os.path.join(tdata, cand)
        if os.path.isfile(p):
            key_file = p
            break
    if not key_file:
        raise FileNotFoundError("tdata 下找不到 key_datas，本机 Telegram Desktop 可能未登录")

    body, _ver = read_tdf(key_file)
    stream = BytesIO(body)
    salt = _read_qbytearray(stream)
    if len(salt) != LOCAL_SALT_SIZE:
        raise ValueError(f"key_datas 中的 salt 长度异常: {len(salt)}")
    key_encrypted = _read_qbytearray(stream)
    _info_encrypted = _read_qbytearray(stream)
    trailer = _read_qbytearray(stream)

    # trailer: magic(0x4B443200) + formatVersion + committed + wrapCount
    # wrapCount > 0 才代表设了本地密码
    if len(trailer) >= 16:
        _magic, _fmt, _committed, wraps = struct.unpack(">IIII", trailer[:16])
        if wraps > 0:
            raise ValueError(
                "该 Telegram Desktop 设置了本地密码（Passcode），"
                "无法自动接管会话，请关闭本地密码或手动登录"
            )

    root_key = create_local_key(salt)
    full = decrypt_local(key_encrypted, root_key)
    n = struct.unpack("<i", full[:4])[0]
    local_key = full[4:4 + n]
    if len(local_key) < 256:
        raise ValueError("localKey 长度异常")
    return local_key


# --------------------------------------------------------------------------
# 账号 MTP 授权数据
# --------------------------------------------------------------------------

def _account_dirs(tdata: str) -> List[str]:
    out = []
    for sub in sorted(glob.glob(os.path.join(tdata, "*"))):
        if not os.path.isdir(sub):
            continue
        name = os.path.basename(sub)
        if len(name) == 16 and all(c in "0123456789ABCDEF" for c in name.upper()):
            out.append(sub)
    return out


def parse_mtp_authorization(local_key: bytes, path: str) -> Optional[Dict]:
    """解密 <acc>s 并解析出 user_id / main_dc / 各 DC 的 auth_key"""
    body, _v = read_tdf(path)
    enc = _read_qbytearray(BytesIO(body))
    if not enc:
        return None
    plain = decrypt_local(enc, local_key)
    data = plain[4:4 + struct.unpack("<i", plain[:4])[0]]

    if len(data) < 8:
        return None
    block_id = struct.unpack(">I", data[:4])[0]
    if block_id != DBI_MTP_AUTHORIZATION:
        return None
    n = struct.unpack(">I", data[4:8])[0]
    ser = data[8:8 + n]
    if len(ser) < 16:
        return None

    p = 0
    tag = ser[0:8]
    if tag == b"\xff" * 8:
        p = 8
        user_id = struct.unpack_from(">Q", ser, p)[0]
        p += 8
    else:
        user_id = struct.unpack_from(">I", ser, p)[0]
        p += 4
    main_dc = struct.unpack_from(">i", ser, p)[0]
    p += 4
    key_count = struct.unpack_from(">i", ser, p)[0]
    p += 4

    keys: Dict[int, bytes] = {}
    for _ in range(max(0, min(key_count, 32))):
        if p + 260 > len(ser):
            break
        dc_id = struct.unpack_from(">i", ser, p)[0]
        p += 4
        keys[dc_id] = ser[p:p + 256]
        p += 256

    if not keys:
        return None
    return {"user_id": user_id, "main_dc": main_dc, "keys": keys}


def read_accounts(tdata: Optional[str] = None) -> Dict:
    """
    返回：
      {"ok": bool, "tdata": str, "accounts": [...], "error": str|None}
    每个 account：{"dir", "slot", "user_id", "main_dc", "keys": {dc: bytes}}
    """
    result: Dict = {"ok": False, "tdata": tdata or "", "accounts": [], "error": None}
    dirs = [tdata] if tdata else find_tdata_dirs()
    if not dirs:
        result["error"] = "没有找到 Telegram Desktop 的 tdata 目录"
        return result

    last_err = None
    for base in dirs:
        try:
            local_key = extract_local_key(base)
        except Exception as e:
            last_err = str(e)
            continue

        accounts = []
        for acc_dir in _account_dirs(base):
            name = os.path.basename(acc_dir)
            # 同一账号的数据文件是 <name>s / <name>0 / <name>1，取第一个能解析的
            for slot, suffix in enumerate(("s", "0", "1")):
                path = os.path.join(base, name + suffix)
                if not os.path.isfile(path):
                    continue
                try:
                    info = parse_mtp_authorization(local_key, path)
                except Exception:
                    continue
                if info:
                    info.update(dir=name, slot=slot, path=path)
                    accounts.append(info)
                    break

        if accounts:
            accounts.sort(key=lambda a: a["user_id"])
            result.update(ok=True, tdata=base, accounts=accounts)
            return result
        if last_err is None:
            last_err = "tdata 中没有可用的已授权账号"

    result["error"] = last_err or "提取失败"
    return result


# --------------------------------------------------------------------------
# 写入 Telethon session
# --------------------------------------------------------------------------

def write_telethon_session(
    session_path: str,
    auth_key: bytes,
    dc_id: int,
    api_id: int,
    user_id: Optional[int] = None,
    server_address: str = "",
    port: int = 0,
) -> None:
    """
    生成一个 Telethon 可直接打开的 SQLite session。

    直接调用 Telethon 的 SQLiteSession 接口，避免猜表结构；
    拿到实例后写入 dc_id / auth_key / user_id / 服务器地址。
    """
    from telethon.crypto import AuthKey
    from telethon.sessions import SQLiteSession

    # 覆盖同名旧文件，避免残留的旧授权干扰
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = session_path + suffix
        if os.path.exists(p):
            os.remove(p)

    sess = SQLiteSession(session_path)
    sess.set_dc(dc_id, server_address, port)
    sess.auth_key = AuthKey(auth_key)
    if user_id:
        sess.process_entities = sess.process_entities  # 保持属性存在
        try:
            sess._update_session_table()  # type: ignore[attr-defined]
        except Exception:
            pass
    sess.save()
    sess.close()


# Telegram 官方 DC 地址（生产环境）
DC_OPTIONS = {
    1: ("149.154.175.53", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.130", 443),
}

# Telegram Desktop 官方客户端的公开凭据（开源仓库里的常量）
TDESKTOP_API_ID = 17349
TDESKTOP_API_HASH = "344583e45741c457fe1862106095a5eb"


def build_session_from_desktop(
    session_path: str,
    account: Optional[Dict] = None,
    tdata: Optional[str] = None,
) -> Dict:
    """把本机桌面端的授权写成 Telethon session，返回结果描述"""
    info = read_accounts(tdata)
    if not info["ok"]:
        return {"ok": False, "error": info.get("error") or "未找到账号"}

    acc = account or info["accounts"][0]
    dc = acc["main_dc"]
    key = acc["keys"].get(dc)
    if not key:
        # 主 DC 的 key 缺失时退到任意一个可用的
        dc, key = next(iter(acc["keys"].items()))
    addr, port = DC_OPTIONS.get(dc, ("149.154.167.51", 443))
    write_telethon_session(
        session_path, key, dc, TDESKTOP_API_ID,
        user_id=acc["user_id"], server_address=addr, port=port,
    )
    return {
        "ok": True,
        "session": session_path,
        "user_id": acc["user_id"],
        "dc_id": dc,
        "api_id": TDESKTOP_API_ID,
        "api_hash": TDESKTOP_API_HASH,
    }


if __name__ == "__main__":
    import json
    res = read_accounts()
    if res["ok"]:
        res["accounts"] = [
            {k: (v if k != "keys" else sorted(v.keys())) for k, v in a.items()}
            for a in res["accounts"]
        ]
    print(json.dumps(res, ensure_ascii=False, indent=2))

