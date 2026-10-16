#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
面包板 Z 规划图生成器
====================
把两块 830 点全尺寸面包板沿电源轨长边拼接成一块 28 行 x 63 列的整板 Z：

    面包板 X（上板，14 行 x 63 列）：| - + | a b c d e | f g h i j | + - |
    面包板 Y（下板，14 行 x 63 列）：| - + | k l m n o | p q r s t | + - |
    面包板 Z（28 行 x 63 列）：      | - + | a b c d e | f g h i j | + - | - + | k l m n o | p q r s t | + - |
    （| = 隔断：板缘 / 电源轨标线 / 中隔槽 / 拼接缝）

为什么 X 在上、Y 在下：行序必须自上而下连读才能得到用户给的 Z 序列
（X 的 14 行在前、Y 的 14 行在后），且只有沿导轨长边拼接才能保持 63 列不变、
行数翻倍到 28。两块板在拼接缝处仅物理相邻，电气不互通。

Y 板终端行改名 k-t，避免与 X 板 a-j 重名（拼接后同一块板内行号必须唯一）。

几何与样式参照 docs/source/breadboard.svg（Breadboard Studio 输出），单位 mm：
  板体 165.1 x 54.6、中隔槽 y=25.7 h=3.2、导轨标线外-内+、轨中间断开、
  终端行孔心 y=13.33/15.87/18.41/20.95/23.49/31.11/33.65/36.19/38.73/41.27、
  列孔心 x=3.81+(c-1)*2.54（63 列）、导轨孔 5 孔一小组（25+25，中间断开）。
  两板拼接时重叠 1.26mm（与参考图一致），让 Z 呈连续板面。

输出（单位 mm 的规划图，非电气验证）：
    docs/source/breadboard-z-outline.svg   仅轮廓（第一步：板体/导轨/隔断/标号/拼接缝）
    docs/source/breadboard-z.svg           轮廓 + 全部 1660 个插孔（第二步）
"""

import os
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- 基本几何（mm）
BOARD_W, BOARD_H = 165.1, 54.6
OVERLAP = 1.26                      # 两板拼接重叠量（参考图同样处理，板面连续）
X_TY = -(BOARD_H - OVERLAP)         # X（上板）平移：y = -53.34
Y_TY = 0.0                          # Y（下板）平移：y = 0
SEAM_Y = OVERLAP / 2.0              # 拼接缝标记线画在重叠区正中：y = 0.63

PITCH = 2.54                        # 孔距
COL_X0 = 3.81                       # 第 1 列终端孔心 x；第 c 列 = COL_X0 + (c-1)*PITCH
N_COLS = 63                         # 每板 63 列

ROW_Y_TOP = [13.33, 15.87, 18.41, 20.95, 23.49]   # 上半区行孔心 y（a..e / k..o）
ROW_Y_BOT = [31.11, 33.65, 36.19, 38.73, 41.27]   # 下半区行孔心 y（f..j / p..t）
X_ROWS = list("abcdefghij")         # X 板终端行：a-e 上半区，f-j 下半区
Y_ROWS = list("klmnopqrst")         # Y 板终端行改名：k-o 上半区，p-t 下半区

RAIL_YS = [("top_outer", 5.71), ("top_inner", 8.25),
           ("bottom_inner", 46.35), ("bottom_outer", 48.89)]   # 4 条电源轨孔心 y
RAIL_BASE_L, RAIL_BASE_R = 8.89, 85.09   # 导轨孔左/右段起点（x=82.55 处中间断开）
RAIL_GROUP_STEP = 15.24             # 5 孔一小组，小组间距 = 6*2.54
RAIL_HOLES_PER_SIDE = 25           # 断开两侧各 25 孔，一条轨共 50 孔

COLOR = {
    "body": "#f3f1e9", "body_stroke": "#c9c5b6",
    "ravine": "#d9d5c7", "ravine_stroke": "#c1bcab",
    "minus": "#2563eb", "plus": "#dc2626", "brk": "#7c2d12",
    "hole": "#3b3b3b", "label": "#6b6b6b", "name": "#8a8577",
    "seam": "#d97706", "title": "#111827", "note": "#6b7280",
    "warn": "#92400e", "badge": "#fef3c7",
}
FONT = "system-ui, -apple-system, 'PingFang SC', 'Noto Sans CJK SC', sans-serif"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "source")
OUTLINE_SVG = os.path.join(OUT_DIR, "breadboard-z-outline.svg")
FULL_SVG = os.path.join(OUT_DIR, "breadboard-z.svg")

CANVAS_X0, CANVAS_Y0 = -8.0, -65.34   # 画布原点（与参考图一致，左侧/上侧留边距）
CANVAS_W = 181.1
CANVAS_H = 146.4                      # 下缘留出图例区（最后一行基线 y=79.6）
PX_W = 1120
PX_H = int(round(PX_W * CANVAS_H / CANVAS_W))


def fmt(v):
    """坐标格式化：去掉二进制浮点噪音（4.109999999999999 -> 4.11）。"""
    s = ("%.2f" % float(v)).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def line(x1, y1, x2, y2, stroke, sw, cls, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return ('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s" class="%s"%s/>'
            % (fmt(x1), fmt(y1), fmt(x2), fmt(y2), stroke, fmt(sw), cls, d))


def text(x, y, s, size, fill, anchor, cls, bold=False):
    b = ' font-weight="bold"' if bold else ""
    return ('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s"%s font-family="%s" class="%s">%s</text>'
            % (fmt(x), fmt(y), fmt(size), fill, anchor, b, FONT, cls, esc(s)))


def rect(x, y, w, h, rx, fill, stroke, sw, cls):
    return ('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="%s" stroke-width="%s" class="%s"/>'
            % (fmt(x), fmt(y), fmt(w), fmt(h), fmt(rx), fill, stroke, fmt(sw), cls))


def rail_marks():
    """4 条电源轨标线 + 中间断开标记。上半区外侧'-'(蓝)/内侧'+'(红)，下半区相反。"""
    out = []
    specs = [("-", 4.11, "minus"), ("+", 9.85, "plus"),      # 上半区
             ("+", 44.75, "plus"), ("-", 50.49, "minus")]    # 下半区
    for sign, y, ck in specs:
        out.append(line(7.39, y, 157.71, y, COLOR[ck], 0.35, "rail-mark"))
        out.append(text(5.69, y + 0.8, sign, 2.2, COLOR[ck], "middle", "rail-mark", bold=True))
        out.append(text(159.41, y + 0.8, sign, 2.2, COLOR[ck], "middle", "rail-mark", bold=True))
    # 导轨中间断开（坐标照抄参考图，保证两板 visuals 一致）
    for y1, y2, ty in [(4.41, 7.01, 8.91), (6.95, 9.55, 6.05),
                       (45.05, 47.65, 49.55), (47.59, 50.19, 46.69)]:
        out.append(line(82.55, y1, 82.55, y2, COLOR["brk"], 0.35, "rail-break"))
        out.append(text(82.55, ty, "断", 1.4, COLOR["brk"], "middle", "rail-break"))
    return out


def col_numbers():
    """列号 1/5/10/.../60/63，板的上下各一排（照参考图位置）。"""
    out = []
    for c in [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 63]:
        x = COL_X0 + (c - 1) * PITCH
        for y in (11.03, 44.67):
            out.append(text(x, y, str(c), 1.7, COLOR["label"], "middle", "board-label"))
    return out


def row_letters(rows):
    """行字母标在板内侧左右边缘（照参考图 x=1.21 / x=163.89）。"""
    out = []
    for i, letter in enumerate(rows):
        y = (ROW_Y_TOP if i < 5 else ROW_Y_BOT)[i if i < 5 else i - 5] + 0.6
        for x in (1.21, 163.89):
            out.append(text(x, y, letter, 1.7, COLOR["label"], "middle", "board-label"))
    return out


def board_group(board_id, ty, rows, name_text):
    """一块 830 孔面包板的轮廓（不含插孔）。board_id: zx / zy。"""
    g = ['<g id="board:%s" transform="translate(0 %s)" class="board" data-board="%s">'
         % (board_id, fmt(ty), board_id)]
    g.append(rect(0, 0, BOARD_W, BOARD_H, 1.5, COLOR["body"], COLOR["body_stroke"], 0.3, "board-body"))
    g.append(rect(0, 25.7, BOARD_W, 3.2, 0, COLOR["ravine"], COLOR["ravine_stroke"], 0.15, "board-ravine"))
    g.extend(rail_marks())
    g.extend(col_numbers())
    g.extend(row_letters(rows))
    g.append(text(2, 2.8, name_text, 1.6, COLOR["name"], "start", "board-name"))
    # 徽标：几何/电气近似（照参考图，坐标 149.45,0.8）
    g.append('<g class="badge" data-badge="%s">' % board_id)
    g.append(rect(149.45, 0.8, 14.15, 2.4, 0.5, COLOR["badge"], COLOR["seam"], 0.2, "badge"))
    g.append(text(150.45, 2.5, "⚠ 几何近似 电气近似", 1.4, COLOR["warn"], "start", "badge"))
    g.append('</g>')
    g.append('</g>')
    return "".join(g)


def seam_marker():
    """X|Y 拼接缝标记：重叠区正中一条琥珀色虚线 + 说明。"""
    out = ['<g id="seam" class="seam">']
    out.append(line(2, SEAM_Y, 163, SEAM_Y, COLOR["seam"], 0.3, "seam-mark", dash="1.2 0.9"))
    out.append(text(82.55, 2.5, "X｜Y 拼接缝（导轨相邻 · 电气不互通）", 1.4, COLOR["seam"], "middle", "seam-label"))
    out.append('</g>')
    return "".join(out)


def hole_circles(prefix, rows):
    """一块板的全部 830 个插孔：终端 10 行 x 63 列 + 4 条轨 x 50 孔。"""
    out = []
    for c in range(1, N_COLS + 1):
        x = COL_X0 + (c - 1) * PITCH
        for i, letter in enumerate(rows):
            y = ROW_Y_TOP[i] if i < 5 else ROW_Y_BOT[i - 5]
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="hole hole-free" data-hole="%s.%s%d"/>'
                       % (fmt(x), fmt(y), COLOR["hole"], prefix, letter, c))
    for rname, ry in RAIL_YS:
        for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
            j = (i - 1) if i <= RAIL_HOLES_PER_SIDE else (i - 1) - RAIL_HOLES_PER_SIDE
            base = RAIL_BASE_L if i <= RAIL_HOLES_PER_SIDE else RAIL_BASE_R
            x = base + (j // 5) * RAIL_GROUP_STEP + (j % 5) * PITCH
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="hole hole-free" data-hole="%s.%s_%d"/>'
                       % (fmt(x), fmt(ry), COLOR["hole"], prefix, rname, i))
    return "".join(out)


def build(include_holes):
    p = ['<?xml version="1.0" encoding="UTF-8"?>']
    p.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s" width="%d" height="%d" font-family="%s">'
             % (fmt(CANVAS_X0), fmt(CANVAS_Y0), fmt(CANVAS_W), fmt(CANVAS_H), PX_W, PX_H, FONT))
    if include_holes:
        p.append("<title>面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔）</title>")
        p.append("<desc>Two 830-point breadboards joined along their power-rail long edges "
                 "into board Z (28 rows x 63 columns). Units: millimetres. "
                 "Planning drawing, not an electrical verification.</desc>")
    else:
        p.append("<title>面包板 Z · 轮廓 —— 双 830 点并排拼接（28 行 × 63 列）</title>")
        p.append("<desc>Board Z outline only (bodies, rails, gaps, labels, seam). "
                 "Units: millimetres. Planning drawing, not an electrical verification.</desc>")
    p.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff"/>'
             % (fmt(CANVAS_X0), fmt(CANVAS_Y0), fmt(CANVAS_W), fmt(CANVAS_H)))
    # 第一步：Z 的轮廓（两块板体 + 拼接缝）
    p.append('<g id="z-outline">')
    p.append(board_group("zx", X_TY, X_ROWS, "X · 830 孔面包板（上板 · 终端行 a–j）"))
    p.append(board_group("zy", Y_TY, Y_ROWS, "Y · 830 孔面包板（下板 · 终端行 k–t）"))
    p.append(seam_marker())
    p.append('</g>')
    # 第二步：全部插孔（独立分组，可整体隐藏复看轮廓）
    # 注意：孔分组必须带上与板体相同的 translate，否则两块板的孔会重叠在同一区域
    if include_holes:
        p.append('<g id="z-holes">')
        p.append('<g id="holes:zx" transform="translate(0 %s)">' % fmt(X_TY))
        p.append(hole_circles("zx", X_ROWS))
        p.append('</g>')
        p.append('<g id="holes:zy" transform="translate(0 %s)">' % fmt(Y_TY))
        p.append(hole_circles("zy", Y_ROWS))
        p.append('</g>')
        p.append('</g>')
    # 标题与图例
    if include_holes:
        p.append(text(-6, -61.84, "面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(-6, -57.2, "X（上板，行 a–j）+ Y（下板，行 k–t）沿电源轨长边拼接为整板 Z；"
                                  "先轮廓（#z-outline）后插孔（#z-holes）。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.append(text(-6, 62, "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                              "Y = | - + | k l m n o | p q r s t | + - |",
                      2.4, COLOR["title"], "start", "doc-legend", bold=True))
        p.append(text(-6, 66.4, "每板 830 孔 = 终端 10 行 × 63 列（630）+ 电源轨 4 条 × 50 孔（200）；"
                                "Z 共 1660 孔。孔距 2.54 mm，单位 mm。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 70.8, "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通；每条电源轨沿长度方向中间断开（图中'断'）。",
                      1.8, COLOR["warn"], "start", "doc-legend"))
        p.append(text(-6, 75.2, "中隔槽将 a–e 与 f–j（Y 板为 k–o 与 p–t）隔开；"
                                "Y 板 k–t 与 X 板 a–j 是各自独立的行号空间。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 79.6, "本图为规划图，几何/电气均为近似，不代表实测结果。",
                      1.8, COLOR["note"], "start", "doc-legend"))
    else:
        p.append(text(-6, -61.84, "面包板 Z · 轮廓 —— 双 830 点并排拼接（28 行 × 63 列）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(-6, -57.2, "X（上板）+ Y（下板）沿电源轨长边拼接；本图为第一步：只画 Z 的轮廓，不含插孔。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.append(text(-6, 62, "轮廓要素：板体 165.1 × 54.6 × 2 / 中隔槽 / 电源轨标线（外 − 内 +，中间断开）/ "
                              "拼接缝 / 行列标号（63 列；X 行 a–j，Y 行 k–t）",
                      2.4, COLOR["title"], "start", "doc-legend", bold=True))
        p.append(text(-6, 66.4, "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                                "Y = | - + | k l m n o | p q r s t | + - |",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 70.8, "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通。",
                      1.8, COLOR["warn"], "start", "doc-legend"))
        p.append(text(-6, 75.2, "下一步：叠加全部 1660 个插孔（见 breadboard-z.svg 的 #z-holes 分组）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 79.6, "本图为规划图，几何/电气均为近似，不代表实测结果。",
                      1.8, COLOR["note"], "start", "doc-legend"))
    p.append('</svg>')
    return "".join(p)


def validate(path, expect_holes):
    """结构自检：XML 可解析、插孔数、每板孔（含组 translate）必须落在本板 y 区间内。"""
    tree = ET.parse(path)
    root = tree.getroot()
    ns = "{http://www.w3.org/2000/svg}"
    # 各板孔心允许的全局 y 区间（局部孔心 y 5.71..48.89 + 板平移）
    bands = {"zx": (-53.34 + 5.71, -53.34 + 48.89), "zy": (5.71, 48.89)}
    per = {"zx": 0, "zy": 0}
    stats = {"n": 0, "xs": [], "ys": []}

    def walk(node, tx, ty):
        tr = node.get("transform")
        if tr:  # 累加平移（本图只用 translate）
            vals = [float(v) for v in tr[tr.index("(") + 1:tr.rindex(")")].replace(",", " ").split()]
            tx, ty = tx + vals[0], ty + vals[1]
        for ch in node:
            if ch.tag == ns + "g":
                walk(ch, tx, ty)
            elif ch.tag == ns + "circle":
                cx, cy = float(ch.get("cx")) + tx, float(ch.get("cy")) + ty
                stats["n"] += 1
                stats["xs"].append(cx)
                stats["ys"].append(cy)
                name = ch.get("data-hole", "")
                if name[:2] in bands:
                    per[name[:2]] += 1
                    lo, hi = bands[name[:2]]
                    assert lo - 1e-6 <= cy <= hi + 1e-6, \
                        "%s: %s 孔 y=%.2f 越出本板区间 [%.2f, %.2f]（translate 漏挂？）" % (path, name, cy, lo, hi)

    walk(root, 0.0, 0.0)
    n, xs, ys = stats["n"], stats["xs"], stats["ys"]
    assert n == expect_holes, "%s: 期望 %d 孔，实际 %d" % (path, expect_holes, n)
    if expect_holes:
        assert per == {"zx": 830, "zy": 830}, "%s: 每板孔数异常 %s" % (path, per)
        assert min(xs) >= 3.81 - 1e-6 and max(xs) <= 161.29 + 1e-6, "孔心 x 越界"
        assert min(ys) >= -53.34 + 5.71 - 1e-6, "孔心 y 越界(上)"
        assert max(ys) <= 48.89 + 1e-6, "孔心 y 越界(下)"
    return n


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    outline = build(include_holes=False)
    full = build(include_holes=True)
    with open(OUTLINE_SVG, "w", encoding="utf-8") as f:
        f.write(outline)
    with open(FULL_SVG, "w", encoding="utf-8") as f:
        f.write(full)
    n1 = validate(OUTLINE_SVG, 0)
    n2 = validate(FULL_SVG, 1660)
    print("OK  %-28s %6.1f KB  插孔 %d" % (os.path.basename(OUTLINE_SVG),
                                           len(outline.encode("utf-8")) / 1024.0, n1))
    print("OK  %-28s %6.1f KB  插孔 %d" % (os.path.basename(FULL_SVG),
                                           len(full.encode("utf-8")) / 1024.0, n2))


if __name__ == "__main__":
    main()
