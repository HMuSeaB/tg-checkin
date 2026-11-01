#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 app.ico —— 深色圆角底 + 绿色对勾，配色取自 static/style.css。
不依赖 Pillow，纯标准库（zlib + struct）直接拼 PNG 再包成 ICO。
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.ico")

BG = (0x15, 0x1A, 0x21)
BORDER = (0x2A, 0x33, 0x40)
ACCENT = (0x34, 0xD3, 0x99)

# 对勾三点（相对左上角，0..1）
P1 = (0.26, 0.52)
P2 = (0.44, 0.70)
P3 = (0.76, 0.33)


def _dist_to_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return math.hypot(dx, dy)


def _rounded_rect_alpha(x, y, size, radius):
    """圆角矩形内部返回 1，外部 0，边缘做抗锯齿。"""
    cx, cy = size / 2.0, size / 2.0
    hx, hy = size / 2.0 - radius, size / 2.0 - radius
    dx = max(abs(x - cx) - hx, 0.0)
    dy = max(abs(y - cy) - hy, 0.0)
    d = math.hypot(dx, dy) - radius
    return _smooth(1.0 - (d + 0.5))


def _smooth(v):
    return max(0.0, min(1.0, v))


def render(size):
    """返回 RGBA bytes（size*size*4），3x3 超采样抗锯齿。"""
    ss = 3
    radius = size * 0.22
    lw = max(1.2, size * 0.105) / 2.0
    p = [(v[0] * size, v[1] * size) for v in (P1, P2, P3)]

    px = bytearray(size * size * 4)
    inv = 1.0 / (ss * ss)
    for y in range(size):
        for x in range(size):
            acc_bg = acc_bd = acc_fg = 0.0
            for sy in range(ss):
                for sx in range(ss):
                    fx = x + (sx + 0.5) / ss
                    fy = y + (sy + 0.5) / ss
                    a = _rounded_rect_alpha(fx, fy, size, radius)
                    if a <= 0:
                        continue
                    acc_bg += a
                    # 边框：靠近边缘一圈
                    edge = _rounded_rect_alpha(fx, fy, size, radius) - _rounded_rect_alpha(
                        fx, fy, size - max(1.0, size * 0.02), max(0.0, radius - size * 0.01))
                    if edge > 0.35:
                        acc_bd += a
                    d = min(_dist_to_seg(fx, fy, *p[0], *p[1]),
                            _dist_to_seg(fx, fy, *p[1], *p[2]))
                    if d <= lw:
                        acc_fg += a

            a_bg = acc_bg * inv
            if a_bg <= 0:
                continue
            a_bd = min(acc_bd * inv, a_bg)
            a_fg = min(acc_fg * inv, a_bg)

            r = BORDER[0] * (a_bd / max(a_bg, 1e-6)) + BG[0] * (1 - a_bd / max(a_bg, 1e-6))
            g = BORDER[1] * (a_bd / max(a_bg, 1e-6)) + BG[1] * (1 - a_bd / max(a_bg, 1e-6))
            b = BORDER[2] * (a_bd / max(a_bg, 1e-6)) + BG[2] * (1 - a_bd / max(a_bg, 1e-6))

            t = a_fg / max(a_bg, 1e-6)
            r = ACCENT[0] * t + r * (1 - t)
            g = ACCENT[1] * t + g * (1 - t)
            b = ACCENT[2] * t + b * (1 - t)

            i = (y * size + x) * 4
            px[i] = int(round(r))
            px[i + 1] = int(round(g))
            px[i + 2] = int(round(b))
            px[i + 3] = int(round(a_bg * 255))
    return bytes(px)


def png_bytes(size, rgba):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [(s, png_bytes(s, render(s))) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)

    with open(OUT, "wb") as fh:
        fh.write(header + entries + blobs)
    print("已生成", OUT, os.path.getsize(OUT), "字节，尺寸:", sizes)


if __name__ == "__main__":
    main()
