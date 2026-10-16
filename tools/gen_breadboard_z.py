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
                                          + ESP32-S3 元件插在拼接缝中央（第三步）

第三步（ESP32-S3 插入）要点：
    元件几何来自 docs/source/breadboard.svg 的同型元件，原样存于
    tools/data/esp32s3_n16r8_component.svg（局部(x,y) -> 世界(112.4-y, -13.34+x)）：
      板身世界 x[55.25,112.4] y[-13.34,14.6]，中心(83.8, 0.63) 正压拼接缝中心；
      上排针（局部 x=1.27）落在世界 y=-12.07 = X 板 j 行，下排针（局部 x=26.67）
      落在世界 y=13.33 = Y 板 k 行；针距 2.54，每排 22 针、列 22..43（22 针在 63 列
      上最居中的合法落点，中心与板中心仅差半孔）。
      排针对应的连接器（按官方 pinout user guide v1.1，已用 data-pin 逐针核对）：
      上排 = J3（GND/TX/RX/GPIO1/2/42-19/GND，与 CH343 同侧），下排 = J1
      （3V3/3V3/RST/GPIO4-14/5V/GND）；USB 双口朝右（USB 在局部低 y 端）。
    孔三态：free（空闲）/ occupied（被针插入，44 孔）/ blocked（被板身遮挡，76 孔）。
    注意：用户原话"J1→j、J3→k"与"USB 朝右"互为镜像，二者只能取一；按 2026-10-16
    用户决定保持 USB 朝右，故图中引线标注/图例以官方 pinout 为准（J3→j、J1→k）。
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

# ---------------------------------------------------------------- ESP32-S3 元件（第三步）
DEVICE_SVG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                          "esp32s3_n16r8_component.svg")   # 元件几何数据（原样导出，勿手改坐标）
DEV_COL_FIRST, DEV_COL_LAST = 22, 43      # 上下两排针各 22 针占据的列
DEV_PINS_PER_ROW = DEV_COL_LAST - DEV_COL_FIRST + 1        # 22
DEV_BODY_X0, DEV_BODY_X1 = 55.25, 112.4   # 元件板身世界 x 区间（判定导轨挡孔用）
# 引脚插入的行：上排针 -> X 板 j 行（世界 y=-12.07）；下排针 -> Y 板 k 行（世界 y=13.33）
DEV_OCCUPIED = {"zx": "j", "zy": "k"}
# 板身遮挡的导轨：X 板靠拼接缝的下两条轨、Y 板靠拼接缝的上两条轨（板身 x 区间内各 19 孔）
DEV_BLOCKED_RAILS = {"zx": ("bottom_inner", "bottom_outer"),
                     "zy": ("top_outer", "top_inner")}

COLOR = {
    "body": "#f3f1e9", "body_stroke": "#c9c5b6",
    "ravine": "#d9d5c7", "ravine_stroke": "#c1bcab",
    "minus": "#2563eb", "plus": "#dc2626", "brk": "#7c2d12",
    "hole": "#3b3b3b", "hole_blocked": "#9ca3af", "label": "#6b6b6b", "name": "#8a8577",
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
CANVAS_H = 162.0                      # 下缘图例区：全图 8 行，末行基线 y=92.8
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
    """X|Y 拼接缝标记：重叠区正中一条琥珀色虚线 + 说明。
    标签避开元件板身（世界 x[55.25,112.4]），放在左侧空处 x=32。"""
    out = ['<g id="seam" class="seam">']
    out.append(line(2, SEAM_Y, 163, SEAM_Y, COLOR["seam"], 0.3, "seam-mark", dash="1.2 0.9"))
    out.append(text(32, 2.5, "X｜Y 拼接缝（导轨相邻 · 电气不互通）", 1.4, COLOR["seam"], "middle", "seam-label"))
    out.append('</g>')
    return "".join(out)


def rail_hole_x(i):
    """第 i 个导轨孔（i=1..50）的板内 x 坐标：左右两段各 25 孔，5 孔一小组。"""
    j = (i - 1) if i <= RAIL_HOLES_PER_SIDE else (i - 1) - RAIL_HOLES_PER_SIDE
    base = RAIL_BASE_L if i <= RAIL_HOLES_PER_SIDE else RAIL_BASE_R
    return base + (j // 5) * RAIL_GROUP_STEP + (j % 5) * PITCH


def hole_circles(prefix, rows, with_device=True):
    """一块板的全部 830 个插孔：终端 10 行 x 63 列 + 4 条轨 x 50 孔。
    with_device=True 时按 ESP32-S3 插入情况标注三态：
      hole-free      空闲
      hole-occupied  被引脚插入（X j / Y k 行列 22-43，上下两排共 44 孔）
      hole-blocked   被板身遮挡、不可插（靠拼接缝的 4 条轨、板身 x 区间内，共 76 孔）
    挡孔样式（浅灰 #9ca3af）照参考图；占孔与空闲同色，靠上面的引脚圆覆盖区分。
    """
    occ_row = DEV_OCCUPIED[prefix] if with_device else None
    blk_rails = DEV_BLOCKED_RAILS[prefix] if with_device else ()
    out = []
    for c in range(1, N_COLS + 1):
        x = COL_X0 + (c - 1) * PITCH
        for i, letter in enumerate(rows):
            y = ROW_Y_TOP[i] if i < 5 else ROW_Y_BOT[i - 5]
            if letter == occ_row and DEV_COL_FIRST <= c <= DEV_COL_LAST:
                cls = "hole hole-occupied"
            else:
                cls = "hole hole-free"
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="%s" data-hole="%s.%s%d"/>'
                       % (fmt(x), fmt(y), COLOR["hole"], cls, prefix, letter, c))
    for rname, ry in RAIL_YS:
        for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
            x = rail_hole_x(i)
            if rname in blk_rails and DEV_BODY_X0 - 1e-6 <= x <= DEV_BODY_X1 + 1e-6:
                cls, fill = "hole hole-blocked", COLOR["hole_blocked"]
            else:
                cls, fill = "hole hole-free", COLOR["hole"]
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="%s" data-hole="%s.%s_%d"/>'
                       % (fmt(x), fmt(ry), fill, cls, prefix, rname, i))
    return "".join(out)


def load_device_xml():
    """读取 ESP32 元件数据文件，剥出 <g> 本体并即时做 XML 校验（坏文件直接报错，不带病出图）。"""
    with open(DEVICE_SVG, encoding="utf-8") as f:
        s = f.read()
    i = s.index('<g id="component:')
    j = s.rindex("</g>") + len("</g>")
    xml = s[i:j]
    ET.fromstring(xml)
    return xml


def device_group():
    """第三步：ESP32-S3 N16R8 插到拼接缝正中央，横跨 X/Y 两板。
    物理取向：USB 口朝右（用户 2026-10-16 决定）。按官方 pinout（user guide v1.1，
    已用 data-pin 逐针核对）：上排 22 针 = J3（GND/TX/RX/GPIO1/2/42-19/GND）落
    X 板 j 行，下排 22 针 = J1（3V3/RST/GPIO4-14/5V/GND）落 Y 板 k 行（均为列 22-43）。
    用户原话"J1→j、J3→k"与该取向互为镜像（绕缝心转 180° 才能同时满足），已定 USB 朝右。
    图层顺序：元件本体（含 44 针与丝印）-> 元件徽标 -> J1/J3 引线标注 -> 拼接缝虚线
    （缝线最后画，覆盖在元件上，直观表达"元件骑缝"）。
    """
    p = ['<g id="z-devices" class="devices">']
    p.append(load_device_xml())
    # 元件徽标（坐标照参考图 annotations 中该元件的徽标）
    p.append('<g class="badge" data-badge="esp32s3_n16r8_dual_usb_1">')
    p.append(rect(55.25, 15.2, 14.15, 2.4, 0.5, COLOR["badge"], COLOR["seam"], 0.2, "badge"))
    p.append(text(56.25, 16.9, "⚠ 几何近似 电气近似", 1.4, COLOR["warn"], "start", "badge"))
    p.append('</g>')
    # J1 / J3 引线标注：引线指向上下两排针右端，文字落在板右侧空白处。
    # USB 朝右取向下，上排针（j 行）属 J3、下排针（k 行）属 J1（官方 pinout）。
    p.append(line(112.7, -12.07, 116.3, -10.9, COLOR["seam"], 0.25, "device-call"))
    p.append(text(117.0, -10.5, "J3（上排 22 针）→ X 板 j 行", 1.4, COLOR["title"], "start", "device-call"))
    p.append(line(112.7, 13.33, 116.3, 14.5, COLOR["seam"], 0.25, "device-call"))
    p.append(text(117.0, 15.1, "J1（下排 22 针）→ Y 板 k 行", 1.4, COLOR["title"], "start", "device-call"))
    # 引脚序列速查（左->右 = 列 22..43），方便后续元件接线时查 GPIO 落点
    p.append(text(117.0, -6.7, "j 行（J3）列22..43：GND TX RX 1 2 42 41 40 39 38 37 36 35 0 45 48 47 21 20 19 GND GND",
                  1.1, COLOR["note"], "start", "device-call"))
    p.append(text(117.0, 11.3, "k 行（J1）列22..43：3V3 3V3 RST 4 5 6 7 15 16 17 18 8 3 46 9 10 11 12 13 14 5V GND",
                  1.1, COLOR["note"], "start", "device-call"))
    # 拼接缝虚线覆盖元件（画在元件之后，表达元件骑在缝上）
    p.append(line(2, SEAM_Y, 163, SEAM_Y, COLOR["seam"], 0.3, "seam-mark", dash="1.2 0.9"))
    # 挡孔叠加：板身遮住的 76 个导轨孔，用浅灰点画到元件之上，让"这些孔插不了线"
    # 在图上一眼可见（不带 data-hole；正式数据仍以 #z-holes 里的 hole-blocked 为准）
    for prefix, ty in (("zx", X_TY), ("zy", Y_TY)):
        for rname in DEV_BLOCKED_RAILS[prefix]:
            wy = dict(RAIL_YS)[rname] + ty
            for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
                x = rail_hole_x(i)
                if DEV_BODY_X0 - 1e-6 <= x <= DEV_BODY_X1 + 1e-6:
                    p.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="hole hole-blocked"/>'
                             % (fmt(x), fmt(wy), COLOR["hole_blocked"]))
    p.append('</g>')
    return "".join(p)


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
        p.append(hole_circles("zx", X_ROWS, with_device=True))
        p.append('</g>')
        p.append('<g id="holes:zy" transform="translate(0 %s)">' % fmt(Y_TY))
        p.append(hole_circles("zy", Y_ROWS, with_device=True))
        p.append('</g>')
        p.append('</g>')
        # 第三步：ESP32-S3 元件（画在孔之上，引脚盖住占孔）
        p.append(device_group())
    # 标题与图例
    if include_holes:
        p.append(text(-6, -61.84, "面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔 · 中央插 ESP32-S3）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(-6, -57.2, "X（上板，行 a–j）+ Y（下板，行 k–t）沿电源轨长边拼接为整板 Z；"
                                  "先轮廓（#z-outline）后插孔（#z-holes），再插 ESP32-S3（#z-devices）。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.append(text(-6, 62, "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                              "Y = | - + | k l m n o | p q r s t | + - |",
                      2.4, COLOR["title"], "start", "doc-legend", bold=True))
        p.append(text(-6, 66.4, "每板 830 孔 = 终端 10 行 × 63 列（630）+ 电源轨 4 条 × 50 孔（200）；"
                                "Z 共 1660 孔。孔距 2.54 mm，单位 mm。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 70.8, "ESP32-S3 N16R8（双 Type-C，44 针）插在拼接缝正中央横跨两板，USB 口朝右："
                                "上排 22 针 = J3 → X 板 j 行、下排 22 针 = J1 → Y 板 k 行（均为列 22–43，左→右 = 列 22→43）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 75.2, "J1/J3 按官方 pinout（user guide v1.1）判定：USB 朝右时 J1 在下（k 行）、J3 在上（j 行），"
                                "与最初'J1→j、J3→k'的表述互为镜像；两种取向只能取一，本图取 USB 朝右。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 79.6, "孔三态：深灰 = 空闲或被针占用（free / occupied）；"
                                "浅灰 = 被板身遮挡不可插（blocked 76 孔：靠缝 4 条轨各 19 孔）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 84, "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通；每条电源轨沿长度方向中间断开（图中'断'）。",
                      1.8, COLOR["warn"], "start", "doc-legend"))
        p.append(text(-6, 88.4, "中隔槽将 a–e 与 f–j（Y 板为 k–o 与 p–t）隔开；"
                              "Y 板 k–t 与 X 板 a–j 是各自独立的行号空间。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 92.8, "本图为规划图，几何/电气均为近似，不代表实测结果。",
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
        p.append(text(-6, 75.2, "第二步：叠加全部 1660 个插孔；第三步：拼接缝正中央插入 ESP32-S3"
                                "（USB 朝右：上排 J3 → X 板 j 行、下排 J1 → Y 板 k 行）。均见 breadboard-z.svg。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 79.6, "本图为规划图，几何/电气均为近似，不代表实测结果。",
                      1.8, COLOR["note"], "start", "doc-legend"))
    p.append('</svg>')
    return "".join(p)


# ---------------------------------------------------------------- 仿射变换（校验/分析用）
# 矩阵记法 (a, b, c, d, e, f)：x' = a*x + c*y + e ; y' = b*x + d*y + f
def mat_identity():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_compose(m1, m2):
    """先应用 m1 再应用 m2（SVG transform 列表的复合顺序）。"""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(s):
    """解析 SVG transform 串（translate / rotate[about point] / scale），返回复合矩阵。"""
    import re as _re
    m = mat_identity()
    for name, args in _re.findall(r"(translate|rotate|scale)\(([^)]*)\)", s or ""):
        v = [float(t) for t in args.replace(",", " ").split()]
        if name == "translate":
            mm = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            mm = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        else:  # rotate(angle[, cx, cy])：屏幕系顺时针为正
            import math
            ang = math.radians(v[0])
            ca, sa = math.cos(ang), math.sin(ang)
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                mm = mat_compose(
                    mat_compose((1.0, 0.0, 0.0, 1.0, cx, cy), (ca, sa, -sa, ca, 0.0, 0.0)),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                mm = (ca, sa, -sa, ca, 0.0, 0.0)
        m = mat_compose(m, mm)
    return m


def validate(path, expect_holes):
    """结构自检：XML 可解析、插孔数、每板孔（含组 translate/rotate）落位、ESP32 引脚与孔态。"""
    import collections
    tree = ET.parse(path)
    root = tree.getroot()
    ns = "{http://www.w3.org/2000/svg}"
    # 各板孔心允许的全局 y 区间（局部孔心 y 5.71..48.89 + 板平移）
    bands = {"zx": (-53.34 + 5.71, -53.34 + 48.89), "zy": (5.71, 48.89)}
    per = {"zx": 0, "zy": 0}
    states = collections.Counter()
    occ_names, blk_names = set(), set()
    pins = []          # (world_x, world_y) 引脚圆心
    has_device = False

    def walk(node, m):
        tr = node.get("transform")
        if tr:
            m = mat_compose(m, parse_transform(tr))
        for ch in node:
            if ch.tag == ns + "g":
                if (ch.get("id") or "").startswith("component:"):
                    nonlocal_check(ch)
                walk(ch, m)
            elif ch.tag == ns + "circle":
                cx, cy = mat_apply(m, float(ch.get("cx")), float(ch.get("cy")))
                name = ch.get("data-hole", "")
                cls = ch.get("class", "")
                if "pin-header" in cls:
                    pins.append((cx, cy))
                    continue
                if name[:2] in bands:
                    per[name[:2]] += 1
                    lo, hi = bands[name[:2]]
                    assert lo - 1e-6 <= cy <= hi + 1e-6, \
                        "%s: %s 孔 y=%.2f 越出本板区间 [%.2f, %.2f]（translate 漏挂？）" % (path, name, cy, lo, hi)
                    if "hole-occupied" in cls:
                        occ_names.add(name)
                    elif "hole-blocked" in cls:
                        blk_names.add(name)
                    states[cls.split()[-1] if cls else "none"] += 1

    def nonlocal_check(comp_node):
        nonlocal has_device
        has_device = True
        assert comp_node.get("transform") == "translate(112.4 -13.34) rotate(90)", \
            "%s: 元件变换被改动: %r" % (path, comp_node.get("transform"))

    walk(root, mat_identity())
    n = sum(states.values())
    assert n == expect_holes, "%s: 期望 %d 孔，实际 %d" % (path, expect_holes, n)
    if expect_holes:
        assert per == {"zx": 830, "zy": 830}, "%s: 每板孔数异常 %s" % (path, per)
        # ---- ESP32 元件校验（仅全图）----
        assert has_device, "%s: 缺少 ESP32 元件分组（#z-devices）" % path
        assert len(pins) == 2 * DEV_PINS_PER_ROW, \
            "%s: 引脚圆 %d 个，应为 %d（上下两排 22 针）" % (path, len(pins), 2 * DEV_PINS_PER_ROW)
        # 引脚落位：上排 y=-12.07（X 板 j 行）、下排 y=13.33（Y 板 k 行），x 覆盖列 22..43
        row_of = {}
        for px, py in pins:
            rk = "zx" if abs(py - (-12.07)) < 0.05 else ("zy" if abs(py - 13.33) < 0.05 else None)
            assert rk, "%s: 引脚落在非 j/k 行: world=(%.2f, %.2f)" % (path, px, py)
            c = int(round((px - COL_X0) / PITCH)) + 1
            assert DEV_COL_FIRST <= c <= DEV_COL_LAST, \
                "%s: 引脚列 %d 越出 [%d, %d]" % (path, c, DEV_COL_FIRST, DEV_COL_LAST)
            row_of.setdefault(rk, []).append(c)
        assert sorted(row_of.get("zx", [])) == list(range(DEV_COL_FIRST, DEV_COL_LAST + 1)), \
            "%s: X 板 j 行引脚列不连续" % path
        assert sorted(row_of.get("zy", [])) == list(range(DEV_COL_FIRST, DEV_COL_LAST + 1)), \
            "%s: Y 板 k 行引脚列不连续" % path
        # 占孔：恰好覆盖 44 个针位，且与引脚一一对应
        want = set()
        for rk, rl in DEV_OCCUPIED.items():
            for c in range(DEV_COL_FIRST, DEV_COL_LAST + 1):
                want.add("%s.%s%d" % (rk, rl, c))
        assert occ_names == want, \
            "%s: occupied 孔 %d 个与针位 %d 个不匹配（多=%s 缺=%s）" % (
                path, len(occ_names), len(want),
                sorted(occ_names - want)[:4], sorted(want - occ_names)[:4])
        assert states["hole-occupied"] == 2 * DEV_PINS_PER_ROW, \
            "%s: occupied 孔 %d != %d" % (path, states["hole-occupied"], 2 * DEV_PINS_PER_ROW)
        # 挡孔：靠缝 4 条轨、板身 x 区间内共 76 孔
        assert len(blk_names) == 76, "%s: blocked 孔 %d != 76" % (path, len(blk_names))
        assert states["hole-blocked"] == 76, "%s: hole-blocked 计数 %d != 76" % (path, states["hole-blocked"])
        for nm in blk_names:
            rk, rest = nm.split(".")
            assert rest.rsplit("_", 1)[0] in DEV_BLOCKED_RAILS[rk], \
                "%s: 挡孔 %s 不在靠缝导轨上" % (path, nm)
        # 总数守恒：1660 = 空闲 + 44 占 + 76 挡
        assert states["hole-free"] == 1660 - 44 - 76, \
            "%s: free 孔 %d 异常" % (path, states["hole-free"])
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
    print("OK  %-28s %6.1f KB  插孔 %d（free 1540 / occupied 44 / blocked 76）"
          % (os.path.basename(FULL_SVG), len(full.encode("utf-8")) / 1024.0, n2))
    print("OK  ESP32-S3 N16R8：44 针全部落在 X j 行 / Y k 行列 22–43，元件骑拼接缝中央")


if __name__ == "__main__":
    main()
