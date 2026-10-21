#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简 SVG -> PNG 预览工具（仅覆盖本项目图纸用到的图元子集）
==========================================================
用途：本机没有 cairo/rsvg 等原生 SVG 渲染库时，用 Pillow 把规划图渲成 PNG 做视觉校验。
支持：viewBox 平移、g[transform=translate/rotate[about point]/scale]（矩阵累加）、
      rect(含 rx，旋转时按多边形绘制)、line(含 stroke-dasharray)、circle、text
      (font-size/fill/text-anchor/font-weight，支持随 transform 旋转)。图元按文档顺序绘制。

用法：
    python tools/svg_to_png.py docs/source/breadboard-z.svg out.png [scale]

注意：文字度量是近似值（仅用于校验布局，不作为交付物）。
      旋转文字用"标记像素"法确定旋转枢轴，任意角度均可用（当前图只用到 90/180/-90）。
"""

import math
import sys
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

NS = "{http://www.w3.org/2000/svg}"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]
ANCHOR_MAP = {"start": "l", "middle": "m", "end": "r"}


def load_font(px):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------- 仿射矩阵 (a,b,c,d,e,f)
def mat_identity():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_compose(m1, m2):
    """先应用 m1 再应用 m2（SVG transform 列表顺序）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def mat_angle(m):
    """矩阵的旋转角（度，屏幕系顺时针为正）；无旋转返回 0。"""
    if abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9:
        return 0.0
    return math.degrees(math.atan2(m[1], m[0]))


def parse_transform(s):
    m = mat_identity()
    for name, args in _iter_fns(s):
        v = [float(t) for t in args.replace(",", " ").split()]
        if name == "translate":
            mm = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            mm = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate":
            ang = math.radians(v[0])
            ca, sa = math.cos(ang), math.sin(ang)   # 屏幕系 y 向下，顺时针为正
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                mm = mat_compose(
                    mat_compose((1.0, 0.0, 0.0, 1.0, cx, cy), (ca, sa, -sa, ca, 0.0, 0.0)),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                mm = (ca, sa, -sa, ca, 0.0, 0.0)
        else:
            continue
        m = mat_compose(m, mm)
    return m


def _iter_fns(s):
    import re
    return re.findall(r"(translate|rotate|scale)\(([^)]*)\)", s or "")


def parse_floats(s):
    return [float(v) for v in s.replace(",", " ").split()]


def _col(c, default=None):
    if c is None or c in ("none", "transparent"):
        return default
    return c


def render(svg_path, png_path, scale=4.0):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    vb = [float(v) for v in root.get("viewBox").split()]
    x0, y0, w, h = vb
    W, H = int(round(w * scale)), int(round(h * scale))
    img = Image.new("RGB", (W, H), "#ffffff")
    dr = ImageDraw.Draw(img)

    def T(x, y):
        return ((x - x0) * scale, (y - y0) * scale)

    def draw_poly(pts, fill, stroke, sw):
        """pts: 已换算到像素的多边形顶点。旋转矩形等非轴对齐场景用它。"""
        if fill:
            dr.polygon(pts, fill=fill)
        if stroke:
            sw = max(1, int(round(sw)))
            loop = list(pts) + [pts[0]]
            dr.line(loop, fill=stroke, width=sw, joint="curve")

    def draw_rotated_text(s, font, fill, anchor, base_m, x, y, bold):
        """文字随自身 transform 旋转：画 tile -> 旋转 -> 用标记像素定位枢轴后粘贴。

        坑（2026-10-17 修）：tile 宽度必须按**推进宽度**（advance = font.getlength）开，
        不能按墨迹宽（getbbox）。否则 middle/end 锚点的文字右半溢出 tile 被裁
        （实测标题 "INMP441" 只剩 "INMP"），且枢轴按墨迹左缘/中点算会让整段文字偏移。
        SVG 锚点语义：start=推进左缘 / middle=推进中点 / end=推进右缘。
        文字统一从 tile 左缘 pad 起画（anchor="ls"），枢轴 pivot_x 按锚点语义取。
        """
        size_px = font.size
        pad = 4
        adv = font.getlength(s)
        bb = font.getbbox(s)
        tw = int(math.ceil(adv)) + 2 * pad + 2      # 推进宽 + 左右余量（防旋转膨胀/描边溢出）
        th = int(math.ceil(bb[3] - bb[1])) + 2 * pad + size_px  # 基线上/下各留
        anchor_x = pad                              # 文字左缘固定画这里
        anchor_y = pad + size_px                    # 基线
        pivot_x = {"l": pad, "m": pad + adv / 2.0, "r": pad + adv}[anchor]
        tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        td.text((anchor_x, anchor_y), s, font=font, fill=fill, anchor="ls",
                stroke_width=max(1, size_px // 14) if bold else 0, stroke_fill=fill)
        angle = mat_angle(base_m)
        # 文字锚点也要先过自身/组的 transform 矩阵再映射像素（与 rect 分支同理：
        # 漏了 mat_apply，带 translate 的组里的文字会整体错位一个平移量）
        tx, ty = T(*mat_apply(base_m, x, y))
        if abs(angle) < 1e-9:
            img.paste(tile, (int(tx - pivot_x), int(ty - anchor_y)), tile)
            return
        # 标记像素法：复制 tile 在枢轴点画唯一色点，旋转后找到该点新坐标
        marker = tile.copy()
        marker.putpixel((int(round(pivot_x)), anchor_y), (255, 0, 0, 255))
        rm = marker.rotate(-angle, resample=Image.NEAREST, expand=True, center=(pivot_x, anchor_y))
        rp = None
        mp = rm.load()
        for yy in range(rm.height):
            for xx in range(rm.width):
                if mp[xx, yy] == (255, 0, 0, 255):
                    rp = (xx, yy)
                    break
            if rp:
                break
        if rp is None:   # 理论上不会发生（枢轴在 tile 内）
            rp = (int(round(pivot_x)), anchor_y)
        rot = tile.rotate(-angle, resample=Image.BICUBIC, expand=True, center=(pivot_x, anchor_y))
        img.paste(rot, (int(round(tx - rp[0])), int(round(ty - rp[1]))), rot)

    def walk(node, m):
        tr = node.get("transform")
        if tr:
            m = mat_compose(m, parse_transform(tr))
        for ch in node:
            tag = ch.tag.replace(NS, "")
            if tag == "g":
                walk(ch, m)
            elif tag == "rect":
                x, y = float(ch.get("x", 0)), float(ch.get("y", 0))
                ww, hh = float(ch.get("width")), float(ch.get("height"))
                fill = _col(ch.get("fill"))
                stroke = _col(ch.get("stroke"))
                sw = float(ch.get("stroke-width", 0)) * scale
                rx = float(ch.get("rx", 0))
                rotated = abs(m[1]) > 1e-9 or abs(m[2]) > 1e-9
                if not rotated:
                    # 注意：必须先过组矩阵再映射像素，否则带 translate 的组（如 X 板
                    # translate(0 -53.34)）里的矩形会画到错误位置（实测板身整个飘出画布）
                    ax, ay = mat_apply(m, x, y)
                    bx, by = mat_apply(m, x + ww, y + hh)
                    p0, p1 = T(ax, ay), T(bx, by)
                    box = [p0[0], p0[1], p1[0], p1[1]]
                    if rx > 0:
                        dr.rounded_rectangle(box, radius=rx * scale, fill=fill,
                                             outline=stroke,
                                             width=max(1, int(round(sw))) if stroke else 0)
                    else:
                        dr.rectangle(box, fill=fill, outline=stroke,
                                     width=max(1, int(round(sw))) if stroke else 0)
                else:
                    # 旋转：四角变换后按多边形画（rx 在旋转场景忽略，本项目 rx<=0.26mm 可忽略）
                    corners = [(x, y), (x + ww, y), (x + ww, y + hh), (x, y + hh)]
                    pts = [T(*mat_apply(m, cx, cy)) for cx, cy in corners]
                    draw_poly(pts, fill, stroke, sw)
            elif tag == "line":
                a = T(*mat_apply(m, float(ch.get("x1")), float(ch.get("y1"))))
                b = T(*mat_apply(m, float(ch.get("x2")), float(ch.get("y2"))))
                stroke = _col(ch.get("stroke"), "#000000")
                sw = max(1, int(round(float(ch.get("stroke-width", 1)) * scale)))
                dash = ch.get("stroke-dasharray")
                if dash:
                    # 手动虚线：沿线按 dash/on-off 步进
                    x1, y1, x2, y2 = a[0], a[1], b[0], b[1]
                    length = math.hypot(x2 - x1, y2 - y1)
                    if length == 0:
                        continue
                    ux, uy = (x2 - x1) / length, (y2 - y1) / length
                    on, off = parse_floats(dash)
                    on_px, off_px = on * scale, off * scale
                    d, drawing = 0.0, True
                    seg_start = 0.0
                    while d < length:
                        d += on_px if drawing else off_px
                        if drawing:
                            dr.line([x1 + ux * seg_start, y1 + uy * seg_start,
                                     x1 + ux * min(d, length), y1 + uy * min(d, length)],
                                    fill=stroke, width=sw)
                        seg_start = d
                        drawing = not drawing
                else:
                    dr.line([a[0], a[1], b[0], b[1]], fill=stroke, width=sw)
            elif tag == "circle":
                cx, cy = mat_apply(m, float(ch.get("cx")), float(ch.get("cy")))
                r = float(ch.get("r")) * scale * math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
                c = T(cx, cy)
                dr.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=_col(ch.get("fill")))
            elif tag == "text":
                x, y = float(ch.get("x", 0)), float(ch.get("y", 0))
                size_px = int(round(float(ch.get("font-size")) * scale))
                font = load_font(size_px)
                anchor = ANCHOR_MAP[ch.get("text-anchor", "start")]
                bold = ch.get("font-weight") == "bold"
                s = ch.text if ch.text else ""
                if s:
                    tr2 = ch.get("transform")
                    tm = mat_compose(m, parse_transform(tr2)) if tr2 else m
                    draw_rotated_text(s, font, _col(ch.get("fill"), "#000000"), anchor, tm, x, y, bold)
    walk(root, mat_identity())
    img.save(png_path)
    return png_path, img.size


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + "-preview.png"
    sc = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    path, size = render(src, dst, sc)
    print("rendered %s -> %s (%dx%d)" % (src, path, size[0], size[1]))
