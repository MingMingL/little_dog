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
                                          + 逐个接入的元件与跳线（第四步起，见元件接入层）

第三步（ESP32-S3 插入）要点：
    元件几何来自 docs/source/breadboard.svg 的同型元件，原样存于
    tools/data/esp32s3_n16r8_component.svg（局部(x,y) -> 世界(137.8-y, -13.34+x)）：
      板身世界 x[80.65,137.8] y[-13.34,14.6]，中心(109.2, 0.63) 正压拼接缝中心；
      上排针（局部 x=1.27）落在世界 y=-12.07 = X 板 j 行，下排针（局部 x=26.67）
      落在世界 y=13.33 = Y 板 k 行；针距 2.54，每排 22 针、列 32..53（2026-10-17
      应用户要求从原来的 22..43 整体右移 10 列；右端 139.19mm 仍在板内 165.1mm）。
      排针对应的连接器（按官方 pinout user guide v1.1，已用 data-pin 逐针核对）：
      上排 = J3（GND/TX/RX/GPIO1/2/42-19/GND，与 CH343 同侧），下排 = J1
      （3V3/3V3/RST/GPIO4-14/5V/GND）；USB 双口朝右（USB 在局部低 y 端）。
    孔三态：free（空闲）/ occupied（被针插入，44 孔 + 元件针孔与跳线落孔）/ blocked（被遮挡不可插，
    个数随板身位置动态算：ESP32 板身遮住的靠缝 4 轨 72 孔 + INMP441 本体 10 孔 + MAX98357A 本体 35 孔）。
    注意：用户原话"J1→j、J3→k"与"USB 朝右"互为镜像，二者只能取一；按 2026-10-16
    用户决定保持 USB 朝右，故图中引线标注/图例以官方 pinout 为准（J3→j、J1→k）。

第四步起（元件接入层）要点：
    连通性铁律（2026-10-16 用户纠正）：MB-102/830 终端区金属条是**竖条**——每列、每个半区
    （a–e 区 / f–j 区）5 孔一条，凹槽断开；横向的"行"不是网络；导轨是横条 50 孔（中段断开）。
    因此 ESP32 直插安全（44 针各占一列，互不短接）；J1 的 GPIO 针在 k 行列 c，同列 l–o 行
    （Y 板）同网；J3 的 GPIO 针在 j 行列 c，同列 f–i 行（X 板）同网。
    元件一律横跨若干列插入（各列独立网络），用跳线归位到目标列的同网孔；落孔名与 data-hole 对齐。
    两排针模块（如 INMP441 圆形小板）必须**骑中隔槽**插入：两排针分落槽两侧的半区
    （Y 板 o 行 + p 行）——若同插一个半区，模块自己的两排针会在同一列短接！
    元件几何取实物/Fritzing 零件：INMP441 见 docs/source/inmp441.svg（Ø15mm 圆板，
    上下两排各 3 孔，下排 SD/VDD/GND、上排 SCK/WS/L/R，列距 2.54mm、行距 7.57mm）。
    元件本体挡住的孔记 blocked（新增）；线从针位画到落孔（跳线实际落在同列空闲孔）。

元件 2/9（MAX98357A I2S 功放，用户 2026-10-17 确认：7 针单排 + 顶部 2P 喇叭端子，
    SD_MODE 接 VIN、GAIN 接 GND、喇叭 4Ω 3W 画示意符号）：
    几何抄 docs/source/MAX98357A.svg（Adafruit REV-A：板身 17.78×19.09mm、7 针间距 2.54 在下沿、
    针心距板顶 16.55mm、顶部 2P 端子螺丝心局部 (7.04,3.50)/(10.54,3.50)）。
    用户原选"p 行列 38–44"实测不可行：那样本体盖住列 38–40 的 l–o 半区，而三根信号线唯一的
    合法落孔正是同列同半区孔（k38–40 又被 ESP32 针占）——信号线无处落孔。故按等价意图改为
    **针插 q 行（33.65）列 45–51**：本体 x[114.30,132.08] y[17.10,36.19]（只压 m–r 行 × 45–51 列，
    m38/39/40 落孔自在），信号针到 k38/39/40 的距离与用户意图一致；本体下沿距 r 行孔心恰 2.54mm。
    接线：LRC/BCK/DIN → m40/m39/m38（GPIO16/15/7，J1 针同列 l–o 同网，走"下潜—左扫"弧线避开本体）；
    GAIN→GND、SD→VIN 两根模块内并接跳线；GND→下轨外轨 − 第 40 孔、VDD→下轨内轨 + 第 41 孔
    （与"VDD→5V 轨、GND→GND 轨"的项目约定一致；轨由后续电源区从 k52/k53 供电）；
    喇叭符号放右上净空楔形 x[138.6,147.8] y[16.0,20.0]（避开 ESP32 板身/J1 标注/麦克风圆板与跳线）。
    绘制顺序调整：**先画全部本体、再画全部跳线**（component_body_group + component_wires_group），
    否则后画元件的本体盖住先画元件的跳线（麦克风 4 根线横穿功放本体，必须压线不压体）。
    微调：INMP441 的 WS 跳线控制点上移 4mm（20.5→24.5），否则它正好从功放喇叭端子的螺丝中心穿过；
    针脚/落孔/网络均未变。
    标签策略：信号线孔侧不写文字（麦克风 GPIO4/5/6 标签已占到 x≈98.6，m38 上方无空位，
    2026-10-17 渲染目检确认"7"与原"GPIO6"压字后去掉），GPIO 全称改放在针上方白丝印；
    电源/轨线标签照常（所在区域无争用）。
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
DEV_COL_FIRST, DEV_COL_LAST = 32, 53      # 上下两排针各 22 针占据的列（2026-10-17 右移 10 列）
DEV_PINS_PER_ROW = DEV_COL_LAST - DEV_COL_FIRST + 1        # 22
DEV_BODY_X0, DEV_BODY_X1 = 80.65, 137.8   # 元件板身世界 x 区间（判定导轨挡孔用）
# 引脚插入的行：上排针 -> X 板 j 行（世界 y=-12.07）；下排针 -> Y 板 k 行（世界 y=13.33）
DEV_OCCUPIED = {"zx": "j", "zy": "k"}
# 板身遮挡的导轨：X 板靠拼接缝的下两条轨、Y 板靠拼接缝的上两条轨
DEV_BLOCKED_RAILS = {"zx": ("bottom_inner", "bottom_outer"),
                     "zy": ("top_outer", "top_inner")}


def dev_blocked_rail_names():
    """板身遮住的导轨孔名集合：4 条靠缝轨中，孔心 x 落在板身区间 [DEV_BODY_X0, X1] 内的孔。
    板身移动后个数会变（列 22-43 时 19 孔/轨，列 32-53 时 18 孔/轨），故不写死。"""
    out = set()
    for prefix, rails in DEV_BLOCKED_RAILS.items():
        for rname in rails:
            for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
                if DEV_BODY_X0 - 1e-6 <= rail_hole_x(i) <= DEV_BODY_X1 + 1e-6:
                    out.add("%s.%s_%d" % (prefix, rname, i))
    return out

COLOR = {
    "body": "#f3f1e9", "body_stroke": "#c9c5b6",
    "ravine": "#d9d5c7", "ravine_stroke": "#c1bcab",
    "minus": "#2563eb", "plus": "#dc2626", "brk": "#7c2d12",
    "hole": "#3b3b3b", "hole_blocked": "#9ca3af", "label": "#6b6b6b", "name": "#8a8577",
    "seam": "#d97706", "title": "#111827", "note": "#6b7280",
    "warn": "#92400e", "badge": "#fef3c7",
    # 元件模块（第四步起）与跳线（红=VCC / 近黑=GND / 紫=信号）
    "pcb": "#15803d", "pcb_stroke": "#052e16", "pcb_text": "#f0fdf4",
    "module_pin": "#e7e5e4", "module_pin_edge": "#57534e",
    "wire_vcc": "#dc2626", "wire_gnd": "#111827", "wire_sig": "#7c3aed",
    "wire_spk": "#0f766e",                       # 喇叭线（teal，与红/黑/紫区分）
    "wire_mtr": "#ea580c",                       # 电机驱动线（橙，TB6612 控制/电源/电机输出）
}
FONT = "system-ui, -apple-system, 'PingFang SC', 'Noto Sans CJK SC', sans-serif"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "source")
OUTLINE_SVG = os.path.join(OUT_DIR, "breadboard-z-outline.svg")
FULL_SVG = os.path.join(OUT_DIR, "breadboard-z.svg")

CANVAS_X0, CANVAS_Y0 = -8.0, -65.34   # 画布原点（与参考图一致，左侧/上侧留边距）
CANVAS_W = 181.1
CANVAS_H = 240.0                      # 下缘图例区：全图 16 行，末行基线 y=164.0
                                     # （元件 4/9 TCRT5000 入图：图例 +2 行，末行 155.2→164.0）
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
    标签避开元件板身（世界 x[80.65,137.8]），放在左侧空处 x=32。"""
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


def hole_circles(prefix, rows, with_device=True, extra_occ=None, extra_blk=None):
    """一块板的全部 830 个插孔：终端 10 行 x 63 列 + 4 条轨 x 50 孔。
    with_device=True 时按 ESP32-S3 插入情况标注三态：
      hole-free      空闲
      hole-occupied  被引脚插入（X j / Y k 行列 32-53，上下两排共 44 孔）；
                     或第四步起的模块针 / 跳线落孔（extra_occ，INMP441 共 11 孔）
      hole-blocked   被板身遮挡、不可插：ESP32 板身遮住的靠缝 4 条轨（共 72 孔）；
                     或第四步起元件本体遮住的孔（extra_blk，INMP441 圆板共 10 孔）
    挡孔样式（浅灰 #9ca3af）照参考图；占孔与空闲同色，靠上面的引脚圆/落孔彩环覆盖区分。
    """
    occ_row = DEV_OCCUPIED[prefix] if with_device else None
    blk_rails = DEV_BLOCKED_RAILS[prefix] if with_device else ()
    dev_blk = dev_blocked_rail_names() if with_device else set()
    extra_occ = extra_occ or set()
    extra_blk = extra_blk or set()
    out = []
    for c in range(1, N_COLS + 1):
        x = COL_X0 + (c - 1) * PITCH
        for i, letter in enumerate(rows):
            y = ROW_Y_TOP[i] if i < 5 else ROW_Y_BOT[i - 5]
            name = "%s.%s%d" % (prefix, letter, c)
            if (letter == occ_row and DEV_COL_FIRST <= c <= DEV_COL_LAST) or name in extra_occ:
                cls, fill = "hole hole-occupied", COLOR["hole"]
            elif name in extra_blk:
                cls, fill = "hole hole-blocked", COLOR["hole_blocked"]
            else:
                cls, fill = "hole hole-free", COLOR["hole"]
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="%s" data-hole="%s"/>'
                       % (fmt(x), fmt(y), fill, cls, name))
    for rname, ry in RAIL_YS:
        for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
            x = rail_hole_x(i)
            name = "%s.%s_%d" % (prefix, rname, i)
            if rname in blk_rails and name in dev_blk:
                cls, fill = "hole hole-blocked", COLOR["hole_blocked"]
            elif name in extra_occ:
                cls, fill = "hole hole-occupied", COLOR["hole"]
            elif name in extra_blk:
                cls, fill = "hole hole-blocked", COLOR["hole_blocked"]
            else:
                cls, fill = "hole hole-free", COLOR["hole"]
            out.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="%s" data-hole="%s"/>'
                       % (fmt(x), fmt(ry), fill, cls, name))
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
    X 板 j 行，下排 22 针 = J1（3V3/RST/GPIO4-14/5V/GND）落 Y 板 k 行（均为列 32-53，
    2026-10-17 起由列 22-43 整体右移 10 列）。
    用户原话"J1→j、J3→k"与该取向互为镜像（绕缝心转 180° 才能同时满足），已定 USB 朝右。
    图层顺序：元件本体（含 44 针与丝印）-> 元件徽标 -> J1/J3 引线标注 -> 拼接缝虚线
    （缝线最后画，覆盖在元件上，直观表达"元件骑缝"）。
    """
    p = ['<g id="z-devices" class="devices">']
    p.append(load_device_xml())
    # 元件徽标（坐标照参考图 annotations 中该元件的徽标）
    # 位置史：初始贴板身左沿 (55.25,15.2) -> 2026-10-17 上午右移 (98.0,15.2) 避 m 行落孔标签
    # -> 同日下午板身右移 10 列后改贴板身右沿 (123.65,15.2)（此时落孔标签在 x<=99，左沿被占）。
    # 该带（板身下沿 14.6 与 m 行标签基线 17.26 之间）只有 x>99 是净空。
    p.append('<g class="badge" data-badge="esp32s3_n16r8_dual_usb_1">')
    p.append(rect(123.65, 15.2, 14.15, 2.4, 0.5, COLOR["badge"], COLOR["seam"], 0.2, "badge"))
    p.append(text(124.65, 16.9, "⚠ 几何近似 电气近似", 1.4, COLOR["warn"], "start", "badge"))
    p.append('</g>')
    # J1 / J3 引线标注：引线指向上下两排针右端（末针列 53 -> x=135.89，偏移量同旧版 +2.21），
    # 文字落在板右侧空白处。USB 朝右取向下，上排针（j 行）属 J3、下排针（k 行）属 J1（官方 pinout）。
    p.append(line(138.1, -12.07, 141.7, -10.9, COLOR["seam"], 0.25, "device-call"))
    p.append(text(142.4, -10.5, "J3（上排 22 针）→ X 板 j 行", 1.4, COLOR["title"], "start", "device-call"))
    p.append(line(138.1, 13.33, 141.7, 14.5, COLOR["seam"], 0.25, "device-call"))
    p.append(text(142.4, 15.1, "J1（下排 22 针）→ Y 板 k 行", 1.4, COLOR["title"], "start", "device-call"))
    # 引脚序列速查（左->右 = 列 32..53），方便后续元件接线时查 GPIO 落点。
    # 2026-10-17 随板身右移改挂到板身左侧空白带（x=3.0）：右侧只剩 29mm 放不下整行，
    # 且撞 J1/J3 引线标注；左侧 78mm 净空与旧位置同高（贴近各自行），语义不变。
    p.append(text(3.0, -6.7, "j 行（J3）列32..53：GND TX RX 1 2 42 41 40 39 38 37 36 35 0 45 48 47 21 20 19 GND GND",
                  1.1, COLOR["note"], "start", "device-call"))
    p.append(text(3.0, 11.3, "k 行（J1）列32..53：3V3 3V3 RST 4 5 6 7 15 16 17 18 8 3 46 9 10 11 12 13 14 5V GND",
                  1.1, COLOR["note"], "start", "device-call"))
    # 拼接缝虚线覆盖元件（画在元件之后，表达元件骑在缝上）
    p.append(line(2, SEAM_Y, 163, SEAM_Y, COLOR["seam"], 0.3, "seam-mark", dash="1.2 0.9"))
    # 挡孔叠加：板身遮住的 72 个导轨孔，用浅灰点画到元件之上，让"这些孔插不了线"
    # 在图上一眼可见（不带 data-hole；正式数据仍以 #z-holes 里的 hole-blocked 为准）
    for nm in sorted(dev_blocked_rail_names()):
        prefix, rest = nm.split(".")
        rname, i = rest.rsplit("_", 1)
        wy = dict(RAIL_YS)[rname] + (X_TY if prefix == "zx" else Y_TY)
        p.append('<circle cx="%s" cy="%s" r="0.5" fill="%s" class="hole hole-blocked"/>'
                 % (fmt(rail_hole_x(int(i))), fmt(wy), COLOR["hole_blocked"]))
    p.append('</g>')
    return "".join(p)


# ---------------------------------------------------------------- 元件接入层（第四步起，逐个添加）
# 列连通规则下的接线圈模型：模块针排横跨若干列（各列独立网络，绝不能竖插同一列），
# 落点全部用"孔名"与 #z-holes 的 data-hole 对齐（终端孔 prefix.row col；导轨孔 prefix.轨名_序号）。
# INMP441（元件 1/9，用户 2026-10-16 确认实物 = Fritzing 圆形板 2×3）：
#   落点由用户指定：上排针（SCK/WS/L/R）插 o 行 60–62 列，下排针（SD/VDD/GND）插 p 行 60–62 列。
#   o/p 两行分居中隔槽两侧（同列不同网），这正是两排针模块唯一合法插法。
#   落点依据：J1 的 GPIO 针在 k 行，同列 l–o 行同网，故信号/VCC 的跳线统一落在 m 行目标列
#   （m32=3V3、m35=GPIO4、m36=GPIO5、m37=GPIO6）；GND 针就近落 Y 下轨外轨（− 轨）第 50 孔；
#   L/R 针与 GND 针同列跨槽，用短跳线在模块处短接（INMP441 选左声道，L/R 必须接 GND）。
#   坑：GND 针若落列 32/33 会短接 3V3、列 34 会把 RST 钳到地——所以模块放右侧全空闲列 60–62。
#   （ESP32 右移 10 列后，落孔列号随之 +10：m22/25/26/27 -> m32/35/36/37。）
#   本体 Ø15mm 圆板盖住 10 个周边孔（记 blocked）：n60–62 / o59,o63 / p59,p63 / q60–62。
# 电源红线 / GND 黑线 / 信号紫线。
INMP441 = {
    "id": "inmp441",
    "label": "INMP441",
    "sub": "I2S 麦克风",
    "board": "zy",
    "label_dx": -2.6,                               # 圆板标题左移至板块中心附近，避开板内 L/R→GND 竖线白底线（x≈158.2）
    # 针（行, 列, 丝印）：上排 SCK/WS/L/R → o 行；下排 SD/VDD/GND → p 行（各 3 列跨槽）
    "pins": [("o", 60, "SCK"), ("o", 61, "WS"), ("o", 62, "L/R"),
             ("p", 60, "SD"), ("p", 61, "VDD"), ("p", 62, "GND")],
    "body": {"shape": "circle", "cx": 156.19, "cy": 27.30, "r": 7.5},   # Ø15mm，中心=6 孔中心
    "blocked": ["zy.n60", "zy.n61", "zy.n62", "zy.o59", "zy.o63",
                "zy.p59", "zy.p63", "zy.q60", "zy.q61", "zy.q62"],
    "wires": [
        # from/to: ("pin", 针下标) 或 ("hole", 孔名)；ctrl 为二次贝塞尔控制点（世界坐标）
        # 落孔随 ESP32 右移 10 列 +10：m25/26/27/22 -> m35/36/37/32（ctrl x 同步 +25.4）
        # WS 的 ctrl y 2026-10-17 由 20.5 上移到 24.5：原弧线正好从 MAX98357A 喇叭端子的
        # 螺丝中心穿过（螺丝心 y=20.60），上移后净空 ≥1.6mm；针脚/落孔/网络均未变。
        {"from": ("pin", 0), "to": ("hole", "zy.m35"), "ctrl": (125.4, 21.5),
         "cls": "wire_sig", "label": "GPIO4", "label_dx": -0.6, "label_anchor": "end"},
        {"from": ("pin", 1), "to": ("hole", "zy.m36"), "ctrl": (129.4, 24.5),
         "cls": "wire_sig", "label": "GPIO5", "label_dx": 0.0, "label_anchor": "middle"},
        {"from": ("pin", 3), "to": ("hole", "zy.m37"), "ctrl": (127.4, 29.0),
         "cls": "wire_sig", "label": "GPIO6", "label_dx": 0.6, "label_anchor": "start"},
        {"from": ("pin", 4), "to": ("hole", "zy.m32"), "ctrl": (121.4, 25.5),
         "cls": "wire_vcc", "label": "3V3", "label_dx": 0.0, "label_anchor": "middle"},
        {"from": ("pin", 5), "to": ("hole", "zy.bottom_outer_50"), "ctrl": (158.0, 41.0),
         "cls": "wire_gnd", "label": "GND −轨", "label_dx": 0.9, "label_anchor": "start", "label_dy": 0.55},
        {"from": ("pin", 2), "to": ("pin", 5), "ctrl": (158.73, 27.3),
         "cls": "wire_gnd", "label": None},
    ],
}
# 元件 2/9：MAX98357A I2S 功放（Adafruit REV-A 7 针 + 顶部 2P 喇叭端子）
# 放置：针插 Y 板 q 行（y=33.65）列 45–51。用户原选"p 行列 38–44"实测不可行——
# 本体下沿会盖住 p 行，进而盖住列 38–40 的 l–o 半区（信号线唯一合法落孔区），
# 而 k38–40 已被 ESP32 针占用 → 信号无处落孔。q 行方案本体 y[17.10,36.19]，
# 只挡 m–r 行 × 45–51 列；m38/39/40 落孔空闲，信号距离意图不变。
MAX98357A = {
    "id": "max98357a",
    "label": "MAX98357A",
    "sub": "I2S 功放",
    "board": "zy",
    # 丝印顺序来自 Fritzing 原件：LRC/BCLK/DIN/GAIN/SD/GND/Vin（左->右）
    "pins": [("q", 45, "LRC"), ("q", 46, "BCLK"), ("q", 47, "DIN"),
             ("q", 48, "GAIN"), ("q", 49, "SD"), ("q", 50, "GND"), ("q", 51, "VIN")],
    # 本体世界坐标（左上角 + 宽高）：x[114.30,132.08] y[17.10,36.19]
    "body": (114.30, 17.10, 17.78, 19.09),
    # 本体覆盖区挡孔：m/n/o/p/r 行 × 列 45–51 = 35 孔（q45–51 是针孔，占 occupied）
    "blocked": [  # m 行
        "zy.m45", "zy.m46", "zy.m47", "zy.m48", "zy.m49", "zy.m50", "zy.m51",
        # n 行
        "zy.n45", "zy.n46", "zy.n47", "zy.n48", "zy.n49", "zy.n50", "zy.n51",
        # o 行
        "zy.o45", "zy.o46", "zy.o47", "zy.o48", "zy.o49", "zy.o50", "zy.o51",
        # p 行
        "zy.p45", "zy.p46", "zy.p47", "zy.p48", "zy.p49", "zy.p50", "zy.p51",
        # r 行（孔心 y=36.19 恰在本体下沿）
        "zy.r45", "zy.r46", "zy.r47", "zy.r48", "zy.r49", "zy.r50", "zy.r51",
    ],
    "wires": [
        # I2S 三信号针 -> ESP32 同列同半区空闲孔 m40/m39/m38（ESP32 针在 k38–40，同网）。
        # 走"下潜—左扫"二次贝塞尔（ctrl x 深插左侧）避开本体横穿；三者互交 1 次属
        # 端点换序的拓扑必然（最右针 LRC 落最左网 GPIO16）。
        # 孔侧不写标签：麦克风 GPIO4/5/6 标签已占到 x≈98.6，m38 上方无空位；GPIO 全称
        # 丝印放在针上方（见 extra_svg 白字），映射关系由图例说明。
        {"from": ("pin", 0), "to": ("hole", "zy.m40"), "ctrl": (104.0, 46.0),
         "cls": "wire_sig", "label": None},
        {"from": ("pin", 1), "to": ("hole", "zy.m39"), "ctrl": (106.5, 44.0),
         "cls": "wire_sig", "label": None},
        {"from": ("pin", 2), "to": ("hole", "zy.m38"), "ctrl": (109.0, 42.0),
         "cls": "wire_sig", "label": None},
        # GAIN/SD_MODE 按用户决策模块内并接（替代默认悬空）：GAIN->GND、SD->VIN。
        {"from": ("pin", 3), "to": ("pin", 5), "ctrl": (125.73, 28.0),
         "cls": "wire_gnd", "label": None},
        {"from": ("pin", 4), "to": ("pin", 6), "ctrl": (128.27, 28.0),
         "cls": "wire_vcc", "label": None},
        # 电源：GND->下轨外轨 - 第 40 孔；VIN->下轨内轨 + 第 41 孔（与 v3/v4 文档一致）
        {"from": ("pin", 5), "to": ("hole", "zy.bottom_outer_40"), "ctrl": (126.0, 42.0),
         "cls": "wire_gnd", "label": "GND −轨", "label_dx": 0.9, "label_anchor": "start", "label_dy": 0.55},
        {"from": ("pin", 6), "to": ("hole", "zy.bottom_inner_41"), "ctrl": (130.81, 40.0),
         "cls": "wire_vcc", "label": "5V +轨", "label_dx": 0.9, "label_anchor": "start", "label_dy": 0.55},
        # 喇叭输出：VO+ 接右螺丝、VO- 接左螺丝（喇叭无极性，仅示意）；teal 色区分
        {"from": ("xy", (124.84, 20.60)), "to": ("xy", (139.60, 18.20)), "ctrl": (133.0, 17.5),
         "cls": "wire_spk", "label": None},
        {"from": ("xy", (121.34, 20.60)), "to": ("xy", (139.60, 17.80)), "ctrl": (130.5, 15.0),
         "cls": "wire_spk", "label": None},
    ],
    # 附加图形：喇叭端子块（深色）+ 两螺丝；VO+/VO- 标签；针侧 GPIO 全称丝印；
    # 右上净空楔形内的喇叭示意符号（音圈 + 锥盆 + 标注）
    "extra_svg": (
        '<rect x="119.59" y="18.50" width="7.40" height="5.60" rx="0.4" '
        'fill="#1f2937" stroke="#111827" stroke-width="0.15"/>'
        '<circle cx="121.34" cy="20.60" r="1.30" fill="#9ca3af" stroke="#4b5563" stroke-width="0.15"/>'
        '<circle cx="124.84" cy="20.60" r="1.30" fill="#9ca3af" stroke="#4b5563" stroke-width="0.15"/>'
        + text(121.34, 25.25, "VO-", 0.85, COLOR["wire_spk"], "middle", "spk-label")
        + text(124.84, 25.25, "VO+", 0.85, COLOR["wire_spk"], "middle", "spk-label")
        # 针侧 GPIO 全称丝印：孔侧不写标签（与麦克风标签争位），全称在这里（白字）
        + text(115.57, 32.10, "GPIO16", 0.8, "#e5e7eb", "middle", "module-pin-label")
        + text(118.11, 32.10, "GPIO15", 0.8, "#e5e7eb", "middle", "module-pin-label")
        + text(120.65, 32.10, "GPIO7", 0.8, "#e5e7eb", "middle", "module-pin-label")
        # 喇叭示意符号（右上净空楔形 x[138.6,147.8] y[16.0,20.0]）
        + '<rect x="139.60" y="17.30" width="1.00" height="1.40" rx="0.12" '
        'fill="#374151" stroke="#111827" stroke-width="0.12"/>'
        + '<polygon points="140.60,17.30 143.40,16.10 143.40,19.90 140.60,18.70" '
        'fill="#e5e7eb" stroke="#374151" stroke-width="0.18"/>'
        + text(143.90, 21.30, "喇叭 4Ω 3W", 1.0, "#374151", "start", "spk-label")
    ),
    # 标题/副标题自定义位置：默认值会与端子块、针丝印、并接跳线相撞
    "label_pos": (123.19, 26.30),
    "sub_pos": (123.19, 28.50),
}
# 元件 3/9：TB6612FNG 双路电机驱动（用户 2026-10-17 确认：方框+丝印、两个电机示意符号）
# 关键决策：**模块本体放板外**——VM（4.5–10V）与电机输出电流（1.2A/路）绝不上面包板
# （面包板触点额定电流约 500mA，且板内无 VMM 电源网络）。只把 7 根控制信号 + VCC/GND 落孔。
# 落孔（9 个，config.h 权威引脚：STBY=GPIO10、PWMA/AIN1/AIN2/PWMB=11/12/13/14、BIN1/BIN2=47/21）：
#   STBY/PWMA/AIN1/AIN2/PWMB → zy.l47–l51（GPIO10–14）
#     —— J1 针在 k 行，同列 l–o 同网；m/n/o 行 × 45–51 列被 MAX98357A 本体挡死（blocked）、
#     k47–51 是 ESP32 针，故 l 行是唯一合法行。5 根线只能斜线压 MAX 本体下插（麦克风 4 线
#     横穿功放本体已确立"压线不压体"先例；贝塞尔 ctrl 深插左下，压体段集中在 MAX 左上无丝印区）。
#   BIN1/BIN2 → zx.i48/i49（GPIO47/21）——同列 f–i 同网（j48/49 是 ESP32 针，同列同网）。
#     走列 48/49 缝隙竖带（x=124.46/127.00）南下：过 ESP32 j/k 针缝隙（距针心 1.27 不碰）、
#     压 MAX 右侧无丝印区（GAIN/SD 丝印在 x≤121）、过 p–t 行缝隙与轨缝，板下横移入框。
#   VCC → zy.o32（3V3，与麦克风 VDD→m32 同网；l32 正下方会穿麦克风 VDD 落孔彩环，故落 o 行）
#   GND → zy.bottom_outer_44（下轨外轨 −；与麦克风 GND（50 孔）、MAX GND（40 孔）同轨）
# 板外布局：方框 x[92,126] y[63,87]（图例整体下移 +36 后腾出板外区）；框上沿 9 针=9 根落线入口，
# 框左沿 VM 接 7.4V 电池符号（x≈27），框右沿 AO1/AO2、BO1/BO2 接电机 M1/M2（圆心 (150,68)/(150,86)）。
# 线色：橙 wire_mtr（与红 VCC/黑 GND/紫信号/teal 喇叭区分）。
TB6612FNG = {
    "id": "tb6612fng",
    "label": "TB6612FNG",
    "sub": "双路电机驱动 · 板外",
    "board": "zy",                                # 锚板（无板上针，占孔全来自 wires）
    "boards": ("zy", "zx"),                       # 落孔跨两板（信号在 Y、BIN 在 X）
    "off_board": True,                            # 本体在板外：不占针孔、无挡孔
    "label_pos": (109.0, 66.2),
    "sub_pos": (109.0, 68.1),
    "body": (92.0, 63.0, 34.0, 24.0),             # 板外方框 x[92,126] y[63,87]
    # 针：板外元件用世界坐标 dict（不占孔）。idx0–8 = 框上沿 9 针（= 9 根落线入口）；
    # idx9 VM（左沿）、idx10–13 AO1/AO2、BO1/BO2（右沿）。上沿 9 针丝印写在 extra_svg
    # 的框内顶行（避免压框外横带跳线），故此处 lab=""。
    "pins": [
        {"xy": (95.0, 63.0), "lab": ""},          # 0 STBY
        {"xy": (98.5, 63.0), "lab": ""},          # 1 PWMA
        {"xy": (102.0, 63.0), "lab": ""},         # 2 AIN1
        {"xy": (105.5, 63.0), "lab": ""},         # 3 AIN2
        {"xy": (109.0, 63.0), "lab": ""},         # 4 PWMB
        {"xy": (112.5, 63.0), "lab": ""},         # 5 VCC
        {"xy": (116.0, 63.0), "lab": ""},         # 6 BIN1
        {"xy": (119.5, 63.0), "lab": ""},         # 7 BIN2
        {"xy": (123.0, 63.0), "lab": ""},         # 8 GND
        {"xy": (92.0, 73.0), "lab": "VM"},        # 9  VM（7.4V 电机电源）
        {"xy": (126.0, 75.0), "lab": "AO1"},      # 10
        {"xy": (126.0, 78.0), "lab": "AO2"},      # 11
        {"xy": (126.0, 82.0), "lab": "BO1"},      # 12
        {"xy": (126.0, 85.0), "lab": "BO2"},      # 13
    ],
    "wires": [
        # 5 根信号：l 行孔 → 框上沿针。二次贝塞尔斜穿 MAX 本体左上无丝印区（"压线不压体"
        # 先例同麦克风线）；ctrl 深插左下让弧线尽快离开 MAX（x<114.3 后彻底干净）。
        # 终点升序排列使 5 线互不相交；GPIO 丝印见图例（l 行上方是 ESP32 k 针，无标签空位）。
        {"from": ("hole", "zy.l47"), "to": ("pin", 0), "ctrl": (110.0, 40.0), "cls": "wire_mtr"},
        {"from": ("hole", "zy.l48"), "to": ("pin", 1), "ctrl": (106.0, 38.0), "cls": "wire_mtr"},
        {"from": ("hole", "zy.l49"), "to": ("pin", 2), "ctrl": (105.0, 36.0), "cls": "wire_mtr"},
        {"from": ("hole", "zy.l50"), "to": ("pin", 3), "ctrl": (104.0, 34.0), "cls": "wire_mtr"},
        {"from": ("hole", "zy.l51"), "to": ("pin", 4), "ctrl": (103.0, 32.0), "cls": "wire_mtr"},
        # BIN1/BIN2：X 板 i48/i49 孔 → 竖带（BIN1 x=124.46 列 48/49 缝；BIN2 x=134.62 列 52/53 缝）
        # → 板下 y=61.2/58.5 横带左行 → 入框上沿针。
        # BIN1 斜段直接入带（不碰邻孔 i49）；BIN2 须先上抬走 h/i 行间缝中线 y=-15.88
        # （距两行孔心各 1.27）再横右入带，否则斜段压 i50/i51 行孔。
        # 134.62 竖带：越 MAX 右缘 132.08（不压体）、过 l/m/n/o 与 p–t 行列缝（距孔心 1.27）、
        # 避开 MAX "GND −轨"/"5V +轨" 标签与 GND 落孔 bottom_outer_44（138.43）、
        # 与 5 根 l 线扇形零交叉（扇形最右点 130.81）；仅压 ESP32 板身 USB/按钮间裸板区。
        {"from": ("hole", "zx.i48"), "to": ("pin", 6), "pts": [(123.19, -14.61), (124.46, -13.8), (124.46, 61.2), (116.0, 61.2), (116.0, 63.0)], "cls": "wire_mtr"},
        {"from": ("hole", "zx.i49"), "to": ("pin", 7), "pts": [(125.73, -14.61), (125.73, -15.88), (134.62, -15.88), (134.62, 58.5), (119.5, 58.5), (119.5, 63.0)], "cls": "wire_mtr"},
        # VCC：o32（3V3）→ 框上沿针。单条贝塞尔，全程 x<114.3 不碰 MAX；穿 p/q 行空闲孔（先例允许）。
        {"from": ("hole", "zy.o32"), "to": ("pin", 5), "ctrl": (98.0, 44.0), "cls": "wire_vcc"},
        # GND：框上沿针 → 下轨外轨 − 第 44 孔。pts 反向书写让 label 渲染在轨孔侧
        # （标签右移 label_dx=2.6，避开本线 x=139.70 竖带）。
        {"from": ("pin", 8), "to": ("hole", "zy.bottom_outer_44"),
         "pts": [(123.0, 63.0), (123.0, 62.8), (139.70, 62.8), (139.70, 51.5), (138.43, 48.89)],
         "cls": "wire_gnd", "label": "GND −轨", "label_dx": 2.6, "label_anchor": "start", "label_dy": 0.55},
        # VM：电池正极 ⇒ 框左沿针（水平长线，板外净空带）
        {"from": ("xy", (32.0, 73.0)), "to": ("pin", 9), "pts": [(32.0, 73.0), (92.0, 73.0)], "cls": "wire_vcc"},
        # 电机输出：AO1/AO2 → M1（左轮）；BO1/BO2 → M2（右轮）。落点均在电机符号圆弧上。
        {"from": ("pin", 10), "to": ("xy", (145.0, 66.0)), "pts": [(126.0, 75.0), (145.0, 66.0)], "cls": "wire_mtr"},
        {"from": ("pin", 11), "to": ("xy", (145.0, 70.0)), "pts": [(126.0, 78.0), (145.0, 70.0)], "cls": "wire_mtr"},
        {"from": ("pin", 12), "to": ("xy", (145.0, 84.5)), "pts": [(126.0, 82.0), (145.0, 84.5)], "cls": "wire_mtr"},
        {"from": ("pin", 13), "to": ("xy", (145.0, 87.5)), "pts": [(126.0, 85.0), (145.0, 87.5)], "cls": "wire_mtr"},
    ],
    # 框内 16 针丝印（复刻真实双列排布：左列 PWMA/AIN2/AIN1/STBY/BIN1/BIN2/PWMB/GND、
    # 右列 VM/VCC/GND/AO1/AO2/BO2/BO1/GND）+ 框内顶行 9 针名（上沿落线入口标注，
    # 放框内避免压框外 y≈58–62 横带）+ 电池符号 + 电机 M1/M2 示意符号。
    # 电池形制（左长片=正极、右短片=负极）+ 引线；电机 = 圆 M + 标注（电机极性由 TB6612 内部决定）。
    "extra_svg": (
        # 框内顶行：9 针名（与框上沿针一一对应）
        text(95.0, 64.3, "STBY", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(98.5, 64.3, "PWMA", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(102.0, 64.3, "AIN1", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(105.5, 64.3, "AIN2", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(109.0, 64.3, "PWMB", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(112.5, 64.3, "VCC", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(116.0, 64.3, "BIN1", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(119.5, 64.3, "BIN2", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        + text(123.0, 64.3, "GND", 0.75, "#f0fdf4", "middle", "tb-pin-top")
        # 框内 16 针丝印（左列 x=97 / 右列 x=121，行距 2.0）
        + "".join(text(97.0, 72.4 + 2.0 * i, nm, 0.8, "#bbf7d0", "middle", "tb-pin-label")
                  for i, nm in enumerate(("PWMA", "AIN2", "AIN1", "STBY",
                                          "BIN1", "BIN2", "PWMB", "GND")))
        + "".join(text(121.0, 72.4 + 2.0 * i, nm, 0.8, "#bbf7d0", "middle", "tb-pin-label")
                  for i, nm in enumerate(("VM", "VCC", "GND", "AO1", "AO2", "BO2", "BO1", "GND")))
        # 电池符号（7.4V，板外）：长片=正极、短片=负极、引线接 VM 线起点 (32,73)
        + line(27.0, 66.0, 27.0, 80.0, "#374151", 0.9, "bat-plate")
        + line(30.2, 69.0, 30.2, 77.0, "#374151", 0.9, "bat-plate")
        + line(30.2, 73.0, 32.0, 73.0, COLOR["wire_vcc"], 0.45, "wire")
        + text(28.6, 64.0, "7.4V", 1.1, "#111827", "middle", "bat-label", bold=True)
        + text(28.6, 83.6, "电池（板外）", 1.0, "#6b7280", "middle", "bat-label")
        # 电机示意符号 M1/M2（圆心均在 AO/BO 线落点上）
        + '<circle cx="150.0" cy="68.0" r="5.0" fill="#e5e7eb" stroke="#374151" stroke-width="0.3" class="mtr-body"/>'
        + text(150.0, 68.9, "M1", 2.6, "#111827", "middle", "mtr-label", bold=True)
        + text(150.0, 75.6, "M1 · 左轮", 1.0, "#374151", "middle", "mtr-label")
        + '<circle cx="150.0" cy="86.0" r="5.0" fill="#e5e7eb" stroke="#374151" stroke-width="0.3" class="mtr-body"/>'
        + text(150.0, 86.9, "M2", 2.6, "#111827", "middle", "mtr-label", bold=True)
        + text(150.0, 93.6, "M2 · 右轮", 1.0, "#374151", "middle", "mtr-label")
    ),
}


def row_y(prefix, letter):
    """行字母 -> 世界 y 孔心坐标（X 板 translate X_TY，Y 板不偏）。"""
    rows = X_ROWS if prefix == "zx" else Y_ROWS
    i = rows.index(letter)
    y = ROW_Y_TOP[i] if i < 5 else ROW_Y_BOT[i - 5]
    return y + (X_TY if prefix == "zx" else Y_TY)


def col_x(c):
    """列号 -> 世界 x 孔心坐标。"""
    return COL_X0 + (c - 1) * PITCH


def hole_xy(name):
    """孔名（如 zy.m22 / zy.top_outer_41）-> 世界坐标。"""
    prefix, rest = name.split(".")
    ty = X_TY if prefix == "zx" else Y_TY
    if "_" in rest:                                   # 导轨孔：轨名_序号
        rname, idx = rest.rsplit("_", 1)
        for rn, ry in RAIL_YS:
            if rn == rname:
                return rail_hole_x(int(idx)), ry + ty
        raise ValueError("未知导轨: %s" % name)
    letter, c = rest[:-2], int(rest[-2:])             # 终端孔：字母+列号
    return col_x(c), row_y(prefix, letter)


def pin_ref(pin):
    """针条目 -> ("rc", 行字母, 列号) 或 ("xy", 世界x, 世界y)。
    板上元件用旧格式 (行, 列, 丝印)；板外元件（TB6612FNG，comp["off_board"]=True）
    用 {"xy": (x, y), "lab": 丝印}——针位是模块本体上的世界坐标，不占面包板孔。"""
    if isinstance(pin, dict):
        if "xy" in pin:
            return ("xy", pin["xy"][0], pin["xy"][1])
        return ("rc", pin["row"], pin["col"])
    return ("rc", pin[0], pin[1])


def pin_label(pin):
    return pin["lab"] if isinstance(pin, dict) else pin[2]


# 元件 4/9：TCRT5000 五路巡线传感器（用户 2026-10-16 决策：分散阵列；VCC 接 3V3）
# 实物（docs/source/TCRT5000.svg，Fritzing 零件判读，缩放 0.28227 mm/单位）：
#   蓝板 24.15×11.09，左端单列 4 针 Vcc/Gnd/Do/Ao（针距 2.52≈2.54，针心在板身左缘外 0.88mm）。
#   **单列针竖插会整列同网短接（铁律）→ 只能横躺占 4 连续列**；AO 针悬空（config.h 只读 DO）。
# config.h：LINE_SENSOR_1..5 = GPIO17/18/38/39/40（数字量 DO，不占 ADC）：
#   GPIO17/18 = k41/k42（Y 板，同列同网须落 l/m/n/o 行 41/42 列）
#   GPIO38/39/40 = j41/j40/j39（X 板，同列同网须落 f/g/h/i 行 39/40/41 列）
# X 板的线与 Y 板网跨不过拼接缝（两板导轨仅物理相邻）→ 探头分两排：
#   排 1（Y 板）：TCRT1 n 行起列 20（GOIO17→DO 跳线 l41）、TCRT2 o 行起列 33（GPIO18→跳线 o42）
#   排 2（X 板 f 行）：TCRT3 起列 27（GPIO40→跳线 i39）、TCRT4 起列 38（GPIO39，f40 与 j40
#     同列同网**直连零跳线**）、TCRT5 起列 49（GPIO38→跳线 i41）
# 起列 27/38/49 + 20/33：本体 24.15mm、起列间隔 11 列（27.94mm）→ 本体间隙 3.87mm、
# 最左针伸出 1.81mm（伸不进邻舱本体），互不遮挡；TCRT2 本体右缘 112.51 < MAX 左缘 114.30。
# 电源（用户决策 3V3）：VCC→上内轨 +、GND→上外轨 −（轨孔均避开 ESP32 板身遮挡区
# x[80.65,137.8]；轨与 3V3/GND 网连通由元件 9 电源区完成，图例注明）。
def make_tcrt(idx, board, rw, col0, gpio, kind):
    """一只 TCRT5000 探头。kind: ("direct",None) DO 直连（针列即 GPIO 网列）；
    ("jump", 落孔名, 走线方式) 跳线到 GPIO 同列网孔，方式=("ctrl",pt) 或 ("pts",点列)。"""
def make_tcrt(idx, board, rw, col0, gpio, kind, gnd_pts=None, vcc_ctrl=None):
    """一只 TCRT5000 探头。kind: ("direct",) DO 直连（针列即 GPIO 网列）；
    ("ctrl",落孔名,ctrl点) 或 ("pts",落孔名,点列)：DO 跳线到 GPIO 同列网孔。
    gnd_pts：覆盖 GND 线为 pts 折线（TCRT2 绕开麦克风 m32 落孔/标签与板身）；vcc_ctrl 同理可覆盖。"""
    px0 = col_x(col0)
    spine = px0 + 1.5 * PITCH                 # 4 针排中心
    py = row_y(board, rw)                     # 针排行心
    # 本体：板身中心在针排中心右侧 12.96mm（Fritzing 原型：板身中心 15.66 vs 针中心 2.70，
    # 差 12.96）→ 板身左缘 = spine - 1.81（最左针心伸出板缘 1.81mm，后 3 针焊盘在板身内，
    # 与真实小板一致；blocked 掩膜排除全部 4 针孔）
    x0 = spine - 1.81
    x1 = x0 + 24.15
    y0, y1 = py - 5.55, py + 5.55
    if idx == 2:
        y0 = 18.95                            # 上缘抬 1.01：让开 MAX 信号落孔 m38/39/40（孔底 18.91）
    # 电源轨目的地：就近轨孔（Y 板 top_* 需避开板身遮挡区 x[80.65,137.8]）
    rail_vcc, rail_vcc_ctrl, rail_gnd, rail_gnd_ctrl = {
        1: ("zy.top_inner_16", (50.5, 14.6), "zy.top_outer_15", (50.5, 13.2)),
        2: ("zy.top_inner_23", (74.0, 13.0), "zy.top_outer_23", None),
        3: ("zx.top_inner_21", (69.2, -33.6), "zx.top_outer_22", (71.6, -35.0)),
        4: ("zx.top_inner_42", (131.5, -33.6), "zx.top_outer_44", (134.5, -35.0)),
        5: ("zx.top_inner_31", (99.0, -33.6), "zx.top_outer_32", (100.9, -35.0)),
    }[idx]
    if vcc_ctrl is not None:
        rail_vcc_ctrl = vcc_ctrl
    wires = [
        {"from": ("pin", 0), "to": ("hole", rail_vcc), "ctrl": rail_vcc_ctrl,
         "cls": "wire_vcc", "label": None},
    ]
    if gnd_pts is not None:
        wires.append({"from": ("pin", 1), "to": ("hole", rail_gnd), "pts": gnd_pts,
                      "cls": "wire_gnd", "label": None})
    else:
        wires.append({"from": ("pin", 1), "to": ("hole", rail_gnd), "ctrl": rail_gnd_ctrl,
                      "cls": "wire_gnd", "label": None})
    if kind[0] == "ctrl":
        wires.append({"from": ("pin", 2), "to": ("hole", kind[1]), "ctrl": kind[2],
                      "cls": "wire_sig", "label": None})
    elif kind[0] == "pts":
        wires.append({"from": ("pin", 2), "to": ("hole", kind[1]), "pts": kind[2],
                      "cls": "wire_sig", "label": None})
    # kind == ("direct",) 时 DO 针与 ESP32 针同列同网，零跳线
    cx = (x0 + x1) / 2.0
    return {
        "id": "tcrt%d" % idx,
        "label": "TCRT%d" % idx,
        "sub": "线检 · GPIO%d" % gpio,
        "board": board,
        "boards": (board,),
        "label_pos": (cx, y0 + 2.6),
        "sub_pos": (cx, y0 + 4.5),
        "pins": [(rw, col0, "VCC"), (rw, col0 + 1, "GND"),
                 (rw, col0 + 2, "DO"), (rw, col0 + 3, "AO")],
        "body": (x0, y0, x1 - x0, y1 - y0),
        "blocked_rects": [(x0, y0, x1, y1)],   # 本体掩膜（自动排除 4 个针孔）
        "wires": wires,
    }


TCRT1 = make_tcrt(1, "zy", "n", 20, 17, ("ctrl", "zy.l41", (76.0, 17.0)))
TCRT2 = make_tcrt(2, "zy", "o", 33, 18,
                  # DO 落 l42（同列 42 同连 GPIO18；o42 在本体挡孔内故改上排孔）：
                  # o35 → 上抬 n/o 缝中线 y=22.22 → 横右 → 列 42/43 缝 x=109.22 竖上 → 入 l42
                  ("pts", "zy.l42", [(col_x(35), 23.49), (col_x(35), 22.22),
                                     (109.22, 22.22), (109.22, 15.87), (col_x(42), 15.87)]),
                  # VCC 弧线从 m32（麦克风 3V3 落孔）与其标签西侧 1.2mm 外掠过，
                  # 末端擦 TCRT1 本体右上角 0.6mm（白名单豁免，物理即贴板角走线）
                  vcc_ctrl=(77.0, 16.0),
                  # GND 绕开：o34 → 斜左（贴 n/o 缝避开 o32 落孔）→ 竖段 x=79.5 出板身左缘外 →
                  # 两轨之间缝 y=7.2 横左 → 入 top_outer_23（VCC 入 top_inner_23，错开布线）
                  gnd_pts=[(88.9, 23.49), (82.0, 21.0), (79.5, 15.0), (79.5, 7.2),
                           (74.93, 7.2), (74.93, 5.71)])
TCRT3 = make_tcrt(3, "zx", "f", 27, 40, ("ctrl", "zx.i39", (88.0, -19.0)))
# TCRT4 与 TCRT5 交换起列区：TCRT5（GPIO38）放起列 39，DO 针 f41 与 j41（GPIO38）同列同网
# **直连零跳线**；TCRT4（GPIO39）放最右起列 51，DO 跳线 f53→i40 斜穿 TCRT5 本体 6mm
# （"压线不压体"先例，TCRT5 本体该段无丝印）。
TCRT4 = make_tcrt(4, "zx", "f", 51, 39, ("ctrl", "zx.i40", (112.0, -14.6)))
TCRT5 = make_tcrt(5, "zx", "f", 39, 38, ("direct",))       # f41 与 j41 同列同网，零跳线
COMPONENTS = [INMP441, MAX98357A, TB6612FNG, TCRT1, TCRT2, TCRT3, TCRT4, TCRT5]

def auto_blocked(comp):
    """按 comp["blocked_rects"]（世界坐标矩形 [(x0,y0,x1,y1)]）自动生成挡孔名集合，
    排除元件自身针孔（针孔必须可插——"挡孔≠占孔"铁律）。"""
    rects = comp.get("blocked_rects")
    if not rects:
        return set()
    pin_holes = set()
    for pin in comp["pins"]:
        ref = pin_ref(pin)
        if ref[0] == "rc":
            pin_holes.add("%s.%s%d" % (comp["board"], ref[1], ref[2]))
    out = set()
    for prefix, rows in (("zx", X_ROWS), ("zy", Y_ROWS)):
        if prefix not in comp.get("boards", (comp["board"],)):
            continue
        ty = X_TY if prefix == "zx" else Y_TY
        for c in range(1, N_COLS + 1):
            x = COL_X0 + (c - 1) * PITCH
            for i, letter in enumerate(rows):
                y = (ROW_Y_TOP[i] if i < 5 else ROW_Y_BOT[i - 5]) + ty
                for x0, y0, x1, y1 in rects:
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        nm = "%s.%s%d" % (prefix, letter, c)
                        if nm not in pin_holes:
                            out.add(nm)
                        break
    return out


def component_hole_marks():
    """元件针孔 + 跳线落孔 -> ({prefix: set(孔名)}, {prefix: set(本体挡孔)})。
    供 hole_circles 标 occupied / blocked。"""
    occ, blk = {}, {}
    for comp in COMPONENTS:
        for pin in comp["pins"]:
            ref = pin_ref(pin)
            if ref[0] == "rc":               # 板上针：锚板占位
                occ.setdefault(comp["board"], set()).add(
                    "%s.%s%d" % (comp["board"], ref[1], ref[2]))
            # off_board 的 xy 针在板外，无孔可占（模块本体不在面包板上）
        for w in comp["wires"]:
            for end in (w["from"], w["to"]):
                if end[0] == "hole":         # 跳线落孔：按孔名前缀归板（可跨板）
                    occ.setdefault(end[1].split(".")[0], set()).add(end[1])
        if not comp.get("off_board"):        # 板外元件无本体挡孔（不遮任何孔）
            b = blk.setdefault(comp["board"], set())
            for nm in comp.get("blocked", ()):
                b.add(nm)
            if comp.get("blocked_rects"):
                b |= auto_blocked(comp)      # 矩形本体自动掩膜（排除自身针孔）
    return occ, blk


def endpoint_xy(comp, end):
    """线端 -> 世界坐标。end = ("pin", 针下标) / ("hole", 孔名) / ("xy", (x,y))。
    xy 端点用于不落在孔上的连接（如喇叭端子螺丝 -> 喇叭符号）。
    板外元件的针是模块本体上的世界坐标（pin 条目带 "xy"）。"""
    if end[0] == "pin":
        ref = pin_ref(comp["pins"][end[1]])
        if ref[0] == "xy":
            return ref[1], ref[2]
        return col_x(ref[2]), row_y(comp["board"], ref[1])
    if end[0] == "xy":
        return end[1]
    return hole_xy(end[1])


def wire_segments(p0, ctrl, p1, n=26):
    """二次贝塞尔采样为折线点列（渲染器只认 line/circle/rect/text，不认 path）。"""
    pts = []
    for k in range(n + 1):
        t = k / float(n)
        mt = 1.0 - t
        pts.append((mt * mt * p0[0] + 2 * mt * t * ctrl[0] + t * t * p1[0],
                    mt * mt * p0[1] + 2 * mt * t * ctrl[1] + t * t * p1[1]))
    return pts


def component_body_group(comp):
    """元件本体层：板身（圆形/矩形）+ extra_svg + 标题/副标题 + 针脚圆与丝印。
    针丝印位置：上半区行排在针上方、下半区行排在针下方——都在本体内部（真实 PCB 丝印
    就在板内），避开跳线与孔位；两排针模块因此上下各有 3 个丝印标签。
    与 component_wires_group 分离：build() 先画全部本体、再画全部跳线，否则麦克风
    横穿功放本体的跳线会被后画的本体盖住。"""
    p = ['<g class="component-module" data-component="%s">' % comp["id"]]
    rows = X_ROWS if comp["board"] == "zx" else Y_ROWS
    body = comp["body"]
    if isinstance(body, dict) and body.get("shape") == "circle":
        cx, cy, r = body["cx"], body["cy"], body["r"]
        p.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="0.25" class="module-body"/>'
                 % (fmt(cx), fmt(cy), fmt(r), COLOR["pcb"], COLOR["pcb_stroke"]))
        lx = cx + comp.get("label_dx", 0.0)   # 圆板标题可左移，避开板内竖直跳线
        p.append(text(lx, cy - 1.0, comp["label"], comp.get("label_size", 1.7),
                      COLOR["pcb_text"], "middle", "module-label", bold=True))
        p.append(text(lx, cy + 1.3, comp["sub"], comp.get("sub_size", 1.05),
                      COLOR["pcb_text"], "middle", "module-label"))
    else:
        x0, y0, w, h = body
        p.append(rect(x0, y0, w, h, 0.5, COLOR["pcb"], COLOR["pcb_stroke"], 0.25, "module-body"))
        cx = x0 + w / 2.0
        if comp.get("extra_svg"):               # 端子块/螺丝/喇叭符号等附加图形
            p.append(comp["extra_svg"])
        lx, ly = comp.get("label_pos", (cx, y0 + h * 0.52))
        sx, sy = comp.get("sub_pos", (cx, y0 + h * 0.80))
        p.append(text(lx, ly, comp["label"], comp.get("label_size", 1.7),
                      COLOR["pcb_text"], "middle", "module-label", bold=True))
        p.append(text(sx, sy, comp["sub"], comp.get("sub_size", 1.05),
                      COLOR["pcb_text"], "middle", "module-label"))
    for _i, pin in enumerate(comp["pins"]):
        ref = pin_ref(pin)
        if ref[0] == "xy":                  # 板外针：世界坐标直接画，丝印随针
            px, py = ref[1], ref[2]
            p.append('<circle cx="%s" cy="%s" r="0.42" fill="%s" stroke="%s" stroke-width="0.08" class="module-pin"/>'
                     % (fmt(px), fmt(py), COLOR["module_pin"], COLOR["module_pin_edge"]))
            p.append(text(px, py - 0.85, pin_label(pin), 0.8, COLOR["pcb_text"],
                          "middle", "module-pin-label"))
            continue
        px, py = col_x(ref[2]), row_y(comp["board"], ref[1])
        p.append('<circle cx="%s" cy="%s" r="0.42" fill="%s" stroke="%s" stroke-width="0.08" class="module-pin"/>'
                 % (fmt(px), fmt(py), COLOR["module_pin"], COLOR["module_pin_edge"]))
        upper = rows.index(ref[1]) < 5
        ly = py - 1.55 if upper else py + 1.95
        p.append(text(px, ly, pin_label(pin), 0.95, COLOR["pcb_text"], "middle", "module-pin-label"))
    p.append('</g>')
    return "".join(p)


def component_wires_group(comp):
    """元件跳线层：白底彩线 + 落孔彩环与标签（在本体层之后统一绘制）。
    单根曲线：from/to/ctrl 二次贝塞尔；多段折线：w["pts"] = 完整点列（含首末端，
    首末端须与 from/to 一致，彩环/标签仍按 from/to 定位）。"""
    p = ['<g class="component-wires" data-component="%s">' % comp["id"]]
    for w in comp["wires"]:
        p0p = endpoint_xy(comp, w["from"])
        p1p = endpoint_xy(comp, w["to"])
        if "pts" in w:                     # 多段折线（板外元件绕 MAX 本体/图例区）
            segs = [(w["pts"][k], w["pts"][k + 1]) for k in range(len(w["pts"]) - 1)]
        else:
            qs = wire_segments(p0p, w["ctrl"], p1p)
            segs = list(zip(qs, qs[1:]))
        for a, b in segs:                       # 白底线：交叠处仍可分辨
            p.append(line(a[0], a[1], b[0], b[1], "#ffffff", 1.0, "wire-under"))
        for a, b in segs:
            p.append(line(a[0], a[1], b[0], b[1], COLOR[w["cls"]], 0.45, "wire"))
        if w.get("label"):
            if w["to"][0] == "hole":
                hx, hy = hole_xy(w["to"][1])
                if "_" in w["to"][1].split(".")[1]:   # 落轨：标签放孔的右侧
                    p.append(text(hx + w.get("label_dx", 0.9), hy + w.get("label_dy", 0.55),
                                  w["label"], w.get("label_size", 1.0), COLOR[w["cls"]], "start", "wire-label"))
                else:                                 # 落终端行：标签放孔上方（避开下行孔）
                    p.append(text(hx + w.get("label_dx", 0.0), hy - 1.15,
                                  w["label"], w.get("label_size", 1.0), COLOR[w["cls"]], w.get("label_anchor", "middle"), "wire-label"))
                p.append('<circle cx="%s" cy="%s" r="0.85" fill="none" stroke="%s" stroke-width="0.3" class="wire-land"/>'
                         % (fmt(hx), fmt(hy), COLOR[w["cls"]]))
            else:                                     # 模块针对模块针（如 L/R 并接 GND）
                px, py2 = endpoint_xy(comp, w["to"])
                p.append('<circle cx="%s" cy="%s" r="0.85" fill="none" stroke="%s" stroke-width="0.3" class="wire-land"/>'
                         % (fmt(px), fmt(py2), COLOR[w["cls"]]))
    p.append('</g>')
    return "".join(p)


def build(include_holes):
    p = ['<?xml version="1.0" encoding="UTF-8"?>']
    p.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s" width="%d" height="%d" font-family="%s">'
             % (fmt(CANVAS_X0), fmt(CANVAS_Y0), fmt(CANVAS_W), fmt(CANVAS_H), PX_W, PX_H, FONT))
    if include_holes:
        p.append("<title>面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔 · 中央插 ESP32-S3 · 元件 4/9 INMP441 + MAX98357A + TB6612FNG + TCRT5000×5）</title>")
        p.append("<desc>Two 830-point breadboards joined along their power-rail long edges "
                 "into board Z (28 rows x 63 columns), with an ESP32-S3 board plugged across "
                 "the seam and components 4/9 (INMP441, MAX98357A, TB6612FNG motor driver, "
                 "five TCRT5000 line sensors) wired in. Units: millimetres. "
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
        wire_holes, body_blocked = component_hole_marks()
        p.append('<g id="holes:zx" transform="translate(0 %s)">' % fmt(X_TY))
        p.append(hole_circles("zx", X_ROWS, with_device=True,
                              extra_occ=wire_holes.get("zx"), extra_blk=body_blocked.get("zx")))
        p.append('</g>')
        p.append('<g id="holes:zy" transform="translate(0 %s)">' % fmt(Y_TY))
        p.append(hole_circles("zy", Y_ROWS, with_device=True,
                              extra_occ=wire_holes.get("zy"), extra_blk=body_blocked.get("zy")))
        p.append('</g>')
        p.append('</g>')
        # 第三步：ESP32-S3 元件（画在孔之上，引脚盖住占孔）
        p.append(device_group())
        # 第四步起：逐个加入的元件与其跳线（画在 ESP32 之上）
        # 两趟绘制：先画全部元件本体，再画全部跳线——否则麦克风横穿功放本体的
        # 跳线会被后画的功放本体盖住（SVG 无 z-index，全靠文档顺序）
        p.append('<g id="z-components">')
        for comp in COMPONENTS:
            p.append(component_body_group(comp))
        p.append('</g>')
        p.append('<g id="z-wires">')
        for comp in COMPONENTS:
            p.append(component_wires_group(comp))
        p.append('</g>')
    # 标题与图例
    if include_holes:
        p.append(text(-6, -61.84, "面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔 · 中央插 ESP32-S3）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(-6, -57.2, "X（上板，行 a–j）+ Y（下板，行 k–t）沿电源轨长边拼接为整板 Z；"
                                  "先轮廓（#z-outline）后插孔（#z-holes），再插 ESP32-S3（#z-devices），"
                                  "然后逐个接入元件与跳线（#z-components 本体 + #z-wires 跳线，当前 4/9：INMP441 + MAX98357A 功放 + TB6612FNG 电机驱动 + TCRT5000×5 巡线）。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.append(text(-6, 98, "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                              "Y = | - + | k l m n o | p q r s t | + - |",
                      2.4, COLOR["title"], "start", "doc-legend", bold=True))
        p.append(text(-6, 102.4, "每板 830 孔 = 终端 10 行 × 63 列（630）+ 电源轨 4 条 × 50 孔（200）；"
                                "Z 共 1660 孔。孔距 2.54 mm，单位 mm。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 106.8, "ESP32-S3 N16R8（双 Type-C，44 针）插在拼接缝正中央横跨两板，USB 口朝右："
                                "上排 22 针 = J3 → X 板 j 行、下排 22 针 = J1 → Y 板 k 行（均为列 32–53，左→右 = 列 32→53）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 111.2, "J1/J3 按官方 pinout（user guide v1.1）判定：USB 朝右时 J1 在下（k 行）、J3 在上（j 行），"
                                "与最初'J1→j、J3→k'的表述互为镜像；两种取向只能取一，本图取 USB 朝右。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 115.6, "元件 1：INMP441 数字麦克风（I2S）圆形小板 Ø15mm，上排针 SCK/WS/L/R 插 o 行 60–62 列、"
                                "下排针 SD/VDD/GND 插 p 行 60–62 列——两排针骑中隔槽分插两个半区（同半区会同列短接）。",
                      1.8, COLOR["pcb"], "start", "doc-legend"))
        p.append(text(-6, 120, "接线圈 5 根 + 1 根模块内短跳线（线端彩环 = 跳线落孔）：红 VDD→m32（3V3）；"
                              "紫 SCK/WS/SD→m35/m36/m37（GPIO4/5/6，J1 针同列 l–o 同网）；"
                              "黑 GND→下轨外轨 − 第 50 孔；L/R 与 GND 两针短接（选左声道）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 124.4, "元件 2：MAX98357A I2S 功放（7 针单排 + 顶部 2P 喇叭端子），7 针插 Y 板 q 行（下半区）列 45–51，"
                                "丝印左→右 = LRC/BCLK/DIN/GAIN/SD/GND/VIN；本体下沿距 ESP32 板身 2.5mm。",
                      1.8, COLOR["pcb"], "start", "doc-legend"))
        p.append(text(-6, 128.8, "接线圈 8 根 + 1 根模块内短跳线（线端彩环 = 跳线落孔）：紫 LRC/BCLK/DIN→m40/m39/m38（GPIO16/15/7）；"
                                "GAIN→GND、SD→VIN 两针短接（用户决策，替代默认悬空）；黑 GND→下轨外轨 − 第 40 孔；"
                                "红 VIN→下轨内轨 + 第 41 孔；青 VO+/VO−→喇叭 4Ω 3W 示意符号。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 133.2, "元件 3：TB6612FNG 双路电机驱动（16 针双列，VM 4.5–10V、1.2A/路）。VM 电源与 1A+ 电机电流"
                                "绝不上面包板——模块本体（方框）画在板外 y[63,87]，只把 9 根控制/电源线落孔；"
                                "AO1/AO2、BO1/BO2 接两个电机示意符号（M1/M2），VM 接 7.4V 电池。",
                      1.8, COLOR["pcb"], "start", "doc-legend"))
        p.append(text(-6, 137.6, "接线圈 9 根（橙 = 电机线）+ 板外 5 根：紫 STBY/PWMA/AIN1/AIN2/PWMB→l47–l51（GPIO10–14，"
                                "J1 针同列 l–o 同网；m/n/o 45–51 列被 MAX 本体挡死，l 行是唯一合法行，斜线压 MAX 本体下插）；"
                                "BIN1/BIN2→X 板 i48/i49（GPIO47/21，同列 f–i 同网）；红 VCC→o32（3V3）；黑 GND→下轨外轨 − 第 44 孔。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 142.0, "元件 4：TCRT5000 五路巡线（用户 2026-10-16 决策：分散阵列、VCC 接 3V3）。蓝板 24.15×11.09，"
                                "左端单列 4 针 Vcc/Gnd/Do/Ao——单列针竖插会整列同网短接（铁律），只能横躺占 4 连续列；"
                                "AO 针悬空（config.h 只读数字量 DO，不占 ADC、与 WiFi 无冲突）。",
                      1.8, COLOR["pcb"], "start", "doc-legend"))
        p.append(text(-6, 146.4, "五探头分两排：Y 板 n 行起列 20（TCRT1·GPIO17，DO 跳线→l41）、o 行起列 33（TCRT2·GPIO18，DO 跳线→o42）；"
                                "X 板 f 行起列 27/38/49（TCRT3·GPIO40 跳线→i39、TCRT4·GPIO39 落 f40 与 j40 同列直连零线、"
                                "TCRT5·GPIO38 跳线→i41）——GPIO17/18 的网在 Y 板，X 板的线跨不过拼接缝，故两探头分到 Y 板。"
                                "VCC 全落上内轨 +、GND 全落上外轨 −（轨避板身遮挡区；轨与 3V3/GND 网连通由元件 9 电源区完成）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 150.8, "孔三态：深灰 = 空闲 / 被 ESP32 针（44）或跳线与模块针（66）占用；"
                                "浅灰 = 被板身遮挡不可插（blocked 252 孔 = ESP32 靠缝 4 轨 72 孔 + INMP441 本体 10 孔 + "
                                "MAX98357A 本体 35 孔 + TCRT5000×5 本体 135 孔）。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 155.2, "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通；每条电源轨沿长度方向中间断开（图中'断'）。",
                      1.8, COLOR["warn"], "start", "doc-legend"))
        p.append(text(-6, 159.6, "中隔槽将 a–e 与 f–j（Y 板为 k–o 与 p–t）隔开：同一列、同一半区的 5 孔才互通；"
                              "两排针模块必须骑槽插，若两排针插进同一半区，模块自己的两排针就会同列短接。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 164.0, "本图为规划图，几何/电气均为近似，不代表实测结果。",
                      1.8, COLOR["note"], "start", "doc-legend"))
    else:
        p.append(text(-6, -61.84, "面包板 Z · 轮廓 —— 双 830 点并排拼接（28 行 × 63 列）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(-6, -57.2, "X（上板）+ Y（下板）沿电源轨长边拼接；本图为第一步：只画 Z 的轮廓，不含插孔。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.append(text(-6, 98, "轮廓要素：板体 165.1 × 54.6 × 2 / 中隔槽 / 电源轨标线（外 − 内 +，中间断开）/ "
                              "拼接缝 / 行列标号（63 列；X 行 a–j，Y 行 k–t）",
                      2.4, COLOR["title"], "start", "doc-legend", bold=True))
        p.append(text(-6, 102.4, "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                                "Y = | - + | k l m n o | p q r s t | + - |",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 106.8, "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通。",
                      1.8, COLOR["warn"], "start", "doc-legend"))
        p.append(text(-6, 111.2, "第二步：叠加全部 1660 个插孔；第三步：拼接缝正中央插入 ESP32-S3"
                                "（USB 朝右：上排 J3 → X 板 j 行、下排 J1 → Y 板 k 行）。"
                                "第四步起逐个接入元件（当前 3/9：INMP441 + MAX98357A 功放 + TB6612FNG 电机驱动）。均见 breadboard-z.svg。",
                      1.8, COLOR["note"], "start", "doc-legend"))
        p.append(text(-6, 115.6, "本图为规划图，几何/电气均为近似，不代表实测结果。",
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
    has_component = {}  # data-component -> True（第四步起的元件分组）

    def walk(node, m):
        tr = node.get("transform")
        if tr:
            m = mat_compose(m, parse_transform(tr))
        for ch in node:
            if ch.tag == ns + "g":
                if (ch.get("id") or "").startswith("component:"):
                    nonlocal_check(ch)
                if ch.get("data-component"):
                    has_component[ch.get("data-component")] = True
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
        assert comp_node.get("transform") == "translate(137.8 -13.34) rotate(90)", \
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
        # 引脚落位：上排 y=-12.07（X 板 j 行）、下排 y=13.33（Y 板 k 行），x 覆盖列 32..53
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
        # 占孔：恰好覆盖 44 个 ESP32 针位 + 元件针孔/跳线落孔，且与引脚一一对应
        comp_occ, comp_blk = component_hole_marks()
        module_blk = set()
        for comp in COMPONENTS:
            module_blk |= comp_blk.get(comp["board"], set())
        dev_occ = set()
        for rk, rl in DEV_OCCUPIED.items():
            for c in range(DEV_COL_FIRST, DEV_COL_LAST + 1):
                dev_occ.add("%s.%s%d" % (rk, rl, c))
        want = set(dev_occ)
        for rk in comp_occ:                     # 元件针孔与跳线落孔（第四步起，按板全收）
            for h in comp_occ.get(rk, ()):
                want.add(h)
        assert occ_names == want, \
            "%s: occupied 孔 %d 个与针位/落孔 %d 个不匹配（多=%s 缺=%s）" % (
                path, len(occ_names), len(want),
                sorted(occ_names - want)[:4], sorted(want - occ_names)[:4])
        assert states["hole-occupied"] == len(want), \
            "%s: occupied 孔 %d != %d" % (path, states["hole-occupied"], len(want))
        # ---- 元件接入层校验（第四步起）----
        for comp in COMPONENTS:
            assert has_component.get(comp["id"]), \
                "%s: 缺少元件分组 data-component=%s" % (path, comp["id"])
            for pin in comp["pins"]:
                ref = pin_ref(pin)
                if ref[0] == "xy":
                    continue                  # 板外针不占孔，无针孔可校验
                name = "%s.%s%d" % (comp["board"], ref[1], ref[2])
                assert name in occ_names, "%s: 元件 %s 的针孔 %s 未标占" % (path, comp["id"], name)
                assert name not in dev_occ, \
                    "%s: 元件 %s 的针孔 %s 与 ESP32 针位重叠" % (path, comp["id"], name)
            for w in comp["wires"]:
                for end in (w["from"], w["to"]):
                    assert end[0] in ("pin", "hole", "xy"), \
                        "%s: 元件 %s 的线端类型非法: %r" % (path, comp["id"], end)
                    if end[0] == "hole":
                        assert end[1] in occ_names, \
                            "%s: 元件 %s 的跳线落孔 %s 未标占" % (path, comp["id"], end[1])
                        assert end[1].split(".")[0] in comp.get("boards", (comp["board"],)), \
                            "%s: 落孔 %s 与元件不在同一板（%s）" % (path, end[1], comp["id"])
            for nm in comp_blk.get(comp["board"], ()):   # 本体挡孔
                assert nm.split(".")[0] == comp["board"], \
                    "%s: 挡孔 %s 与元件不在同一板（%s）" % (path, nm, comp["id"])
                assert nm not in occ_names, \
                    "%s: 挡孔 %s 又是占孔（本体遮挡与插针冲突）" % (path, nm)
            for pin in comp["pins"]:                 # 针孔不得落在本体挡孔里
                ref = pin_ref(pin)
                if ref[0] == "xy":
                    continue
                nm = "%s.%s%d" % (comp["board"], ref[1], ref[2])
                assert nm not in comp_blk.get(comp["board"], ()), \
                    "%s: 元件 %s 的针孔 %s 又被本体挡（自相矛盾）" % (path, comp["id"], nm)
        # 挡孔：ESP32 靠缝 4 条轨（个数随板身位置动态算）+ 元件本体遮挡孔（INMP441 圆板 10 孔）
        n_blocked = len(dev_blocked_rail_names()) + len(module_blk)
        assert len(blk_names) == n_blocked, "%s: blocked 孔 %d != %d" % (path, len(blk_names), n_blocked)
        assert states["hole-blocked"] == n_blocked, \
            "%s: hole-blocked 计数 %d != %d" % (path, states["hole-blocked"], n_blocked)
        for nm in blk_names:
            if nm in module_blk:
                continue                          # 元件本体挡孔不必在靠缝导轨上
            rk, rest = nm.split(".")
            assert rest.rsplit("_", 1)[0] in DEV_BLOCKED_RAILS[rk], \
                "%s: 挡孔 %s 不在靠缝导轨上" % (path, nm)
        # 总数守恒：1660 = 空闲 + ESP32 44 占 + 元件针/跳线落孔 + 挡孔
        assert states["hole-free"] == 1660 - len(want) - n_blocked, \
            "%s: free 孔 %d 异常（应为 %d）" % (path, states["hole-free"], 1660 - len(want) - n_blocked)
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
    comp_occ, comp_blk = component_hole_marks()
    n_mod_occ = sum(len(v) for v in comp_occ.values())      # 元件针孔 + 跳线落孔
    n_mod_blk = sum(len(v) for v in comp_blk.values())      # 元件本体挡孔
    n_occ = 2 * DEV_PINS_PER_ROW + n_mod_occ
    n_blk = len(dev_blocked_rail_names()) + n_mod_blk
    print("OK  %-28s %6.1f KB  插孔 %d（free %d / occupied %d = ESP32 %d + 元件 %d / blocked %d）"
          % (os.path.basename(FULL_SVG), len(full.encode("utf-8")) / 1024.0, n2,
             1660 - n_occ - n_blk, n_occ, 2 * DEV_PINS_PER_ROW, n_mod_occ, n_blk))
    print("OK  ESP32-S3 N16R8：44 针全部落在 X j 行 / Y k 行列 32–53，元件骑拼接缝中央")
    print("OK  元件 1 INMP441：圆板 2×3，SCK/WS/L/R 插 o 行 60–62、SD/VDD/GND 插 p 行 60–62；"
          "5 根跳线落孔（m32/m35/m36/m37/bottom_outer_50）+ L/R→GND 模块内短接；本体挡孔 10")
    print("OK  元件 2 MAX98357A：7 针单排插 q 行 45–51（丝印 LRC/BCLK/DIN/GAIN/SD/GND/VIN）；"
          "3 信号落 m40/m39/m38（GPIO16/15/7），GND→下轨外轨 − 40、VIN→下轨内轨 + 41，"
          "GAIN→GND / SD→VIN 短接，VO+/VO−→喇叭 4Ω 3W；本体挡孔 35")
    print("OK  元件 3 TB6612FNG：板外方框（x[92,126] y[63,87]）不占孔不挡孔；"
          "STBY/PWMA/AIN1/AIN2/PWMB 落 l47–l51（GPIO10–14，斜线压 MAX 本体），BIN1/BIN2 落 "
          "X i48/i49（GPIO47/21），VCC→o32（3V3），GND→下轨外轨 − 44；"
          "VM→7.4V 电池、AO1/AO2、BO1/BO2→电机 M1/M2；落孔 9，板外线 5")
    print("OK  元件 4 TCRT5000×5：单列 4 针只能横躺（同列同网铁律），AO 悬空；"
          "TCRT1/2 落 Y 板 n20–23（GPIO17→跳线 l41）、o33–36（GPIO18→pts 跳线 l42）；"
          "TCRT3/5/4 落 X 板 f 行 27/39/51（GPIO40→跳线 i39、GPIO38 直连 f41=j41、"
          "GPIO39→跳线 i40 斜穿 TCRT5 本体）；VCC→上内轨 +、GND→上外轨 −（轨由元件 9 接网）；"
          "本体挡孔 135，落孔/占孔 34")


if __name__ == "__main__":
    main()
