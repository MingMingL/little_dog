#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简 SVG -> PNG 预览工具（仅覆盖本项目图纸用到的图元子集）
==========================================================
用途：本机没有 cairo/rsvg 等原生 SVG 渲染库时，用 Pillow 把规划图渲成 PNG 做视觉校验。
支持：viewBox 平移、g[transform=translate]、rect(含 rx)、line(含 stroke-dasharray)、
      circle、text(font-size/fill/text-anchor/font-weight)。图元按文档顺序绘制。

用法：
    python tools/svg_to_png.py docs/source/breadboard-z.svg out.png [scale]

注意：文字度量是近似值（仅用于校验布局，不作为交付物）。
"""

import sys
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

NS = "{http://www.w3.org/2000/svg}"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def load_font(px):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    return ImageFont.load_default()


def parse_floats(s):
    return [float(v) for v in s.replace(",", " ").split()]


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

    def walk(node, tx, ty):
        tr = node.get("transform")
        if tr:
            # 仅支持 translate(x y)
            vals = parse_floats(tr[tr.index("(") + 1:tr.rindex(")")])
            tx, ty = tx + vals[0], ty + vals[1]
        for ch in node:
            tag = ch.tag.replace(NS, "")
            if tag == "g":
                walk(ch, tx, ty)
            elif tag == "rect":
                x, y = float(ch.get("x", 0)) + tx, float(ch.get("y", 0)) + ty
                ww, hh = float(ch.get("width")), float(ch.get("height"))
                rx = float(ch.get("rx", 0))
                p0, p1 = T(x, y), T(x + ww, y + hh)
                box = [p0[0], p0[1], p1[0], p1[1]]
                fill = ch.get("fill")
                stroke = ch.get("stroke")
                sw = float(ch.get("stroke-width", 0)) * scale
                if rx > 0:
                    dr.rounded_rectangle(box, radius=rx * scale, fill=fill,
                                         outline=stroke, width=max(1, int(round(sw))) if stroke else 0)
                else:
                    dr.rectangle(box, fill=fill, outline=stroke,
                                 width=max(1, int(round(sw))) if stroke else 0)
            elif tag == "line":
                a, b = T(float(ch.get("x1")) + tx, float(ch.get("y1")) + ty), \
                       T(float(ch.get("x2")) + tx, float(ch.get("y2")) + ty)
                stroke = ch.get("stroke", "#000000")
                sw = max(1, int(round(float(ch.get("stroke-width", 1)) * scale)))
                dash = ch.get("stroke-dasharray")
                if dash:
                    # 手动虚线：沿线按 dash/on-off 步进
                    import math
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
                cx, cy = float(ch.get("cx")) + tx, float(ch.get("cy")) + ty
                r = float(ch.get("r"))
                c = T(cx, cy)
                rr = r * scale
                dr.ellipse([c[0] - rr, c[1] - rr, c[0] + rr, c[1] + rr], fill=ch.get("fill"))
            elif tag == "text":
                x, y = float(ch.get("x")) + tx, float(ch.get("y")) + ty
                size_px = int(round(float(ch.get("font-size")) * scale))
                font = load_font(size_px)
                anchor = {"start": "l", "middle": "m", "end": "r"}[ch.get("text-anchor", "start")]
                bold = ch.get("font-weight") == "bold"
                dr.text((T(x, y)[0], T(x, y)[1]), ch.text or "", font=font,
                        fill=ch.get("fill", "#000000"), anchor=anchor + "s",
                        stroke_width=max(1, size_px // 14) if bold else 0,
                        stroke_fill=ch.get("fill", "#000000"))
    walk(root, 0, 0)
    img.save(png_path)
    return png_path, img.size


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + "-preview.png"
    sc = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    path, size = render(src, dst, sc)
    print("rendered %s -> %s (%dx%d)" % (src, path, size[0], size[1]))
