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
    "wire_5v": "#991b1b",                        # 5V 网（深红，区别于 3V3 的正红 #dc2626）
}
FONT = "system-ui, -apple-system, 'PingFang SC', 'Noto Sans CJK SC', sans-serif"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "source")
OUTLINE_SVG = os.path.join(OUT_DIR, "breadboard-z-outline.svg")
FULL_SVG = os.path.join(OUT_DIR, "breadboard-z.svg")

CANVAS_X0, CANVAS_Y0 = -8.0, -104.0   # 画布原点（上侧扩展 38.7mm：容纳板外 OLED 本体 y[-98.4,-62.9]）
CANVAS_W = 181.1
CANVAS_H_MIN = 286.66                 # 画布高度**下限**（轮廓图保持此值）；全图高度由图例末行
                                      # 回算（CANVAS_H 写死的时代，图例加行就得手改常量，
                                      # 2026-10-19 元件 7/8/9 入图改为末行基线 + 5.4 自动回算）
LEGEND_X = -6.0                       # 图例文字左缘
LEGEND_BOTTOM_PAD = 5.4               # 末行基线到画布下缘的距离
PX_W = 1120


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
         # 标签右上移（2026-10-19）：默认位置 (82.55,18.96) 正好压在电源区 3V3 西段馈线的
         # 19.68 横档上（标签与线重叠=看不清），移到 (84.75,16.81) 后与所有横档错开。
         "cls": "wire_vcc", "label": "3V3", "label_dx": 2.2, "label_dy": -1.6,
         "label_anchor": "middle"},
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
        # （GND 标签三次搬家，都是被邻居挤的：原在孔东侧 y=49.44 → 压电源区桥接线 40→41；
        #   改孔西侧 → 压 TB6612 的 BIN1 竖带（x=124.46，离孔心仅 1.27）；最后定为
        #   **孔东北侧、轨线以北 1.2mm** 的短标签 "GND"——上方是下内轨孔（46.35）与自己的
        #   贝塞尔线（同元件豁免），下方让开桥接线，长度压到 2mm 避免撞自己的 "5V +轨" 标签。）
        {"from": ("pin", 5), "to": ("hole", "zy.bottom_outer_40"), "ctrl": (126.0, 42.0),
         "cls": "wire_gnd", "label": "GND", "label_dx": 0.9, "label_anchor": "start", "label_dy": -1.2},
        # VIN 走 5V 网（2026-10-19 用户决策：3V3 只带小电流，功放峰值电流改由 5V 承担），
        # 故用深红 wire_5v 而不是 3V3 的正红 wire_vcc——颜色必须和实际电位一致，否则
        # 看图接线的人会把 5V 插进 3V3 轨（ESP32 LDO 会被喇叭峰值电流拖垮）。
        {"from": ("pin", 6), "to": ("hole", "zy.bottom_inner_41"), "ctrl": (130.81, 40.0),
         "cls": "wire_5v", "label": "5V +轨", "label_dx": 0.9, "label_anchor": "start", "label_dy": 0.55},
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
        # （标签左移 label_dx=-2.6：2026-10-19 电源区加轨组短跳线后，孔 44 东侧 2.54mm 就是
        #  桥接线 45→46，标签仍放右侧会整段压在桥上；西侧到桥 40→41 的终点（130.81）还有
        #  7mm 空档，且本线自己的 x=139.70 竖带在 y≥51.5，与标签（y=49.44）不相干。）
        {"from": ("pin", 8), "to": ("hole", "zy.bottom_outer_44"),
         "pts": [(123.0, 63.0), (123.0, 62.8), (139.70, 62.8), (139.70, 51.5), (138.43, 48.89)],
         "cls": "wire_gnd", "label": "GND −轨", "label_dx": -2.6, "label_anchor": "end", "label_dy": 0.55},
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
        # 电池符号（7.4V，板外）：长片=正极、短片=负极。
        # 2026-10-19 极性修正：原引线从**短片（负极）**接到 VM，接反了。
        # 改从长片（正极 x=27）上端绕到 (32,63) 再下行到 VM 线起点 (32,73)（不穿过负极片）；
        # 负极引线由元件 8 的 IN− 线从短片下端 (30.2,77) 接出（黑色）。
        + line(27.0, 66.0, 27.0, 80.0, "#374151", 0.9, "bat-plate")
        + line(30.2, 69.0, 30.2, 77.0, "#374151", 0.9, "bat-plate")
        + line(27.0, 66.0, 27.0, 63.0, COLOR["wire_vcc"], 0.45, "wire")
        + line(27.0, 63.0, 32.0, 63.0, COLOR["wire_vcc"], 0.45, "wire")
        + line(32.0, 63.0, 32.0, 73.0, COLOR["wire_vcc"], 0.45, "wire")
        + text(25.2, 66.5, "+", 1.4, "#b91c1c", "middle", "bat-label", bold=True)
        + text(32.4, 77.6, "−", 1.4, "#111827", "middle", "bat-label", bold=True)
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


# 元件 4/9 撤销（2026-10-19 用户决策 B）：TCRT5000 五路巡线阵列整体移除。
# 原因：TCRT 模块的发射/接收头在 PCB 正面，插上面包板后镜头朝上（朝天花板）；
# 且量程仅 0.2–15mm，底盘一离地即超程——物理上照不到地板，巡迹/防跌落都落空。
# 防跌落改由 VL53L0X 支架前倾 30° 承担（见元件 5 与图例"侧视"小图）。
# LINE_SENSOR_1..5（GPIO17/18/38/39/40）同轮从 config.h 删除（grep 确认零代码引用），
# 这 5 个 GPIO 与 X/Y 板对应孔位一并释放，转为预留。
# 元件 5/9：VL53L0X 激光测距（2026-10-19 改决策 B：支架前倾 30°，防跌落 + 近距防撞；XSHUT/GPIO 悬空）
# 实物（docs/source/VL53L0X.svg，Adafruit rev C breakout，viewBox 77.16x50.4 = 27.24x17.78mm，
# 缩放 0.3531 mm/单位）：板身 27.24x17.78，底部单排 6 针 VIN/GND/SCL/SDA/GPIO/XSHUT。
# 板高 17.78 > X 板任一半区的 10.16mm行带 -> 本体必在板外（用户选：悬板外上方、窗朝北）。
# 接线（config.h：SDA=GPIO41=j38、SCL=GPIO42=j37，均为 X 板 j 行针，网在 f-i 行列 38/37）：
#   6 针横躺落 a 行列 35-40（VIN/GND/SCL/SDA/GPIO/XSHUT）；
#   SCL/SDA 走"东竖带"折线（中段贴 38/39 缝 x=99.06/101.60，避开 b–h 行同列孔与 ESP32 板身）
#     汇入 i37/i38 与 j37/j38 同列同网；
#   VIN/GND 绕本体西南角落 top_inner_25 / top_outer_25（线贴板身角走，白名单）；
#   GPIO/XSHUT 悬空（breakout 板载上拉，默认使能、地址 0x29）。
# 本体挡孔：板上段覆盖 top_* 轨孔 26-34（18 孔；VL53 自身 VIN/GND 落 25 孔在掩膜外）。
VL53L0X = {
    "id": "vl53l0x",
    "label": "VL53L0X",
    "sub": "防跌落 · 前倾30°",
    "board": "zx",
    "boards": ("zx",),
    "label_pos": (96.52, -48.6),
    "sub_pos": (96.52, -46.4),
    "body": (82.90, -57.90, 27.24, 17.78),     # 本体跨板缘：板外段 + 板上轨区段
    "pins": [("a", 35, "VIN"), ("a", 36, "GND"), ("a", 37, "SCL"), ("a", 38, "SDA"),
             ("a", 39, "GPIO"), ("a", 40, "XSHUT")],
    "blocked_rects": [(82.90, -57.90, 110.14, -40.12)],
    "wires": [
        # VIN/GND：弧线先北上离开 a 行孔带（否则贴行心向西会连扫 a34/a35 孔），
        # 再贴本体西南板身角入轨（VIN _25 / GND _23 错开 4mm + ctrl 平行，避免互扫落孔
        # 彩环；擦点均在近端连接距离内。落轨待电源区接 3V3）
        {"from": ("pin", 0), "to": ("hole", "zx.top_inner_25"), "ctrl": (87.0, -42.0),
         "cls": "wire_vcc", "label": None},
        {"from": ("pin", 1), "to": ("hole", "zx.top_outer_23"), "ctrl": (75.0, -44.0),
         "cls": "wire_gnd", "label": None},
        # SCL：a37 南下出体 → 横右贴 38/39 缝（x=99.06）竖下 → h/i 行间缝（y=-15.88）→ 入 i37。
        # 竖带同时避开 TCRT3 本体右缘（96.8）与 TCRT5 本体左缘（102.33）。
        {"from": ("pin", 2), "to": ("hole", "zx.i37"),
         "pts": [(95.25, -40.01), (95.25, -38.7), (99.06, -38.7), (99.06, -15.88),
                 (95.25, -15.88), (95.25, -14.61)], "cls": "wire_sig", "label": None},
        # SDA：a38 微南下 → 斜右贴 39/40 缝（x=101.6）竖下 → 斜入 i38（与 SCL 竖带平行距 2.54）
        {"from": ("pin", 3), "to": ("hole", "zx.i38"),
         "pts": [(97.79, -40.01), (97.79, -39.4), (101.6, -38.7), (101.6, -16.6),
                 (97.79, -14.61)], "cls": "wire_sig", "label": None},
    ],
    # 传感器窗（板顶北端，对准狗前方）+ 地址丝印 + 探针说明
    "extra_svg": (
        '<circle cx="96.52" cy="-54.5" r="4.2" fill="#1f2937" stroke="#0b0e0f" stroke-width="0.25" class="vl53-window"/>'
        '<circle cx="96.52" cy="-54.5" r="1.6" fill="#4b5563" stroke="none"/>'
        + text(96.52, -42.6, "I2C 0x29", 0.85, "#d1fae5", "middle", "vl53-sub")
        + text(88.6, -51.4, "前倾30°", 0.8, "#a7f3d0", "middle", "vl53-note")
    ),
}
# 元件 6/9：OLED 1.3 寸 SH1106（7 针 SPI 模块，实为 9 针单排）
# 实物（docs/source/OLED.svg，Fritzing breadboard，缩放 0.3531 mm/单位）：
#   板身 35.04×35.54，单排 9 针（针距 2.54 = 7.2 单位）位于板身上缘 y=8.73（相对 viewBox 顶部 3.08mm）：
#   左→右丝印 = Gnd Vin 3v3 CS Rst SA0 Clk Data DC（左侧编号 5–12，右侧 4 个大焊盘 = 安装孔）。
#   **Rst 在正中第 5 针，而 config.h 的 GPIO48（Rst）落在 j47、GPIO1/2（Clk/Data）落在 j35/j36
#   ——首尾跨 12 列 30.5mm，远超模块 9 针 20.3mm 的长度，任何起列/翻转都装不下。**
# 用户 2026-10-19 决策：本体放**板外（面包板上方）**、屏幕朝上便于查看，9 根飞线直连目标孔。
# 焊盘 x 对齐 a 行 44–52 的栅格（避开 VL53 的 a35–40 与其掩膜列 26–34），
#   丝印顺序 = 列号顺序（Gnd=44 … DC=52），与实物朝上放置时的左→右一致，不做转置。
# 接线（config.h：SCLK=GPIO1=j35、MOSI=GPIO2=j36、DC=GPIO46=k45、RES=GPIO48=j47、CS 接 GND）：
#   Gnd→top_outer_35、CS→top_outer_38、SA0→top_outer_40（外轨 = GND 网，CS/SA0 接地固定片选与地址）；
#   Vin→top_inner_36、3v3→top_inner_38（内轨 = 3V3 网，与 VL53 VIN 同轨；若模块 LDO 压差不够，
#   元件 9 电源区可把 Vin 改接 5V 专用轨）；Rst→j47、Clk→j35、Data→j36、DC→k45（跨拼接缝）。
def make_oled():
    """OLED 模块：9 个焊盘在本体下沿（本体悬在板外上方），9 根飞线直连目标孔，屏幕朝上。

    为什么是"飞线直连"而不是"9 针插 a 行"（2026-10-19 重写）：
      本体在板外，针根本插不进面包板；焊盘 -> 面包板孔之间必须画飞线。
      首版让飞线先落 a 行 44-52、再从 a 行接出去，结果本体与 a 行之间 19mm **没有任何引线**，
      图上模块像悬空孤岛；而且 a 行东段的电源线横穿针脚丝印。改成一根线走到底（焊盘 -> 目标孔）。

    为什么每根线都要"换道"（2026-10-19 校验后定案）：
      本体在板外北面，目标孔全在两条上轨以南 —— 9 根线必须自上而下穿过
      top_outer(-47.63) / top_inner(-45.09)。轨孔与列孔共用 2.54 栅格（每 5 孔一组、组间空档），
      Vin/3v3/Rst/Clk/Data/DC 的焊盘正下方都有轨孔，直落会"穿过"轨孔造成误读；
      故这些线在轨区改走**列间缝**（离两侧轨孔各 1.27mm > 接触阈值 1.05），
      用一段 1.27mm 短横在外轨以北的空档（y=-49.04）换道。
      Gnd(113.03) 落在轨孔空档可直落；CS(120.65)/SA0(125.73) 的目标就是正下方的外轨孔
      （终点即目标，不算穿越），同样直落。
    丝印顺序 = 列号顺序（Gnd 在西、DC 在东，与实物朝上放置时一致，不做转置），
      这样 Clk/Data 这两个要跑到最西 j35/j36 的针排在最东，竖带互不错位，全图仅 1 处交叉。
    """
    RAIL_Y = dict(RAIL_YS)                    # 本地板内坐标
    Y_OUTER = RAIL_Y["top_outer"] + X_TY      # -47.63
    Y_INNER = RAIL_Y["top_inner"] + X_TY      # -45.09
    Y_RAILGAP = (Y_OUTER + Y_INNER) / 2.0     # -46.36（两轨正中空带，离两条轨孔各 1.27）
    Y_JOG = Y_OUTER - 1.41                    # -49.04（外轨以北 1.41 的空档，换道横档）
    SEAM = 1.27                               # 列间缝到孔心的距离
    Y_J_N = row_y("zx", "j") - 1.27           # -13.34  j 行带北侧空档（离 i/j 孔各 1.27）
    Y_J_S = row_y("zx", "j") + 1.27           # -10.80  j 行带南侧空档
    Y_J_S2 = row_y("zx", "j") + 2.57          #  -9.50  j 行与下内轨之间的空档（离孔 2.5+）
    Y_K_N = row_y("zy", "k") - 1.27           #  12.06  Y 板 k 行带北侧空档

    col0, col1 = 44, 52
    pin_y = row_y("zx", "a")                  # -40.01（仅用于文档对照，飞线不再落 a 行）
    body_w, body_h, pin_inset = 35.04, 35.54, 19.0   # 本体在板外：下沿离 a 行 19mm
    bx0 = (col_x(col0) + col_x(col1)) / 2.0 - body_w / 2.0
    by1 = pin_y - pin_inset                   # -59.01（本体下沿 = 焊盘排）
    by0 = by1 - body_h                        # -94.55（整块在板外，不挡任何孔）
    pin_order = ["Gnd", "Vin", "3v3", "CS", "Rst", "SA0", "Clk", "Data", "DC"]
    # 焊盘 = 板外世界坐标（模块本体下沿）；丝印顺序与列号同向（针 i ↔ 列 44+i）
    pins = [{"xy": (col_x(col0 + i), by1), "lab": pin_order[i]} for i in range(len(pin_order))]

    def jog(px, seam_x):
        """焊盘 -> 换道横档 -> 竖带起点（seam_x 可等于 px：正下方是轨孔空档，直落不换道）。"""
        if seam_x == px:
            return [(px, by1)]
        return [(px, by1), (px, Y_JOG), (seam_x, Y_JOG)]

    def to_rail(px, seam_x, rail_i, y_rail):
        """焊盘 -> 竖穿上轨 -> 轨间空带 -> 轨孔（终点即目标）。"""
        hx = rail_hole_x(rail_i)
        return jog(px, seam_x) + [(seam_x, Y_RAILGAP), (hx, Y_RAILGAP), (hx, y_rail)]

    wires = [
        # ① Gnd(113.03) -> zx.top_outer_35（110.49）：走**外轨以北空档 Y_JOG** 换道。
        #    2026-10-19 改道原因：原来走轨间空档 Y_RAILGAP(-46.36) 西行占掉 x[110.49,113.03]，
        #    而电源区要把 3V3 送进上内轨东段，只能从 111.76 竖廊下来再沿轨间空档西行落 top_inner_35
        #    ——两条线会在 -46.36 上共线重叠（一 GND 一 3V3 = 图上短接）。改走北侧 -49.04 后
        #    轨间空档整段让给 3V3，两条线只在 110.49 处上下错开，互不重叠。
        {"from": ("pin", 0), "to": ("hole", "zx.top_outer_35"), "cls": "wire_gnd",
         "pts": [(col_x(44), by1), (col_x(44), Y_JOG),
                 (rail_hole_x(35), Y_JOG), (rail_hole_x(35), Y_OUTER)], "label": None},
        # ② Vin(115.57) -> zx.top_inner_36（115.57）：正下方是外轨孔 36（非目标），换道东缝 116.84
        {"from": ("pin", 1), "to": ("hole", "zx.top_inner_36"), "cls": "wire_vcc",
         "pts": to_rail(col_x(45), col_x(45) + SEAM, 36, Y_INNER), "label": None},
        # ③ 3v3(118.11) -> zx.top_inner_38（120.65）：换道东缝 119.38（118.11 是外轨孔 37，非目标）
        {"from": ("pin", 2), "to": ("hole", "zx.top_inner_38"), "cls": "wire_vcc",
         "pts": to_rail(col_x(46), col_x(46) + SEAM, 38, Y_INNER), "label": None},
        # ④ CS(120.65) -> zx.top_outer_38（120.65）：正下方即目标外轨孔，直落
        {"from": ("pin", 3), "to": ("hole", "zx.top_outer_38"), "cls": "wire_gnd",
         "pts": [(col_x(47), by1), (col_x(47), Y_OUTER)], "label": None},
        # ⑤ SA0(125.73) -> zx.top_outer_40（125.73）：同上，直落
        {"from": ("pin", 4), "to": ("hole", "zx.top_outer_40"), "cls": "wire_gnd",
         "pts": [(col_x(49), by1), (col_x(49), Y_OUTER)], "label": None},
        # ⑥ Rst(123.19) -> zx.j47（GPIO48）：换道东缝 124.46 竖下，j 行带北侧空档西行一孔
        {"from": ("pin", 5), "to": ("hole", "zx.j47"), "cls": "wire_sig",
         "pts": jog(col_x(48), col_x(48) - SEAM)
                + [(col_x(48) - SEAM, Y_J_N), (col_x(47), Y_J_N),
                   (col_x(47), row_y("zx", "j"))], "label": None},
        # ⑦ Clk(128.27) -> zx.j35（GPIO1）：换道东缝 129.54 竖下，j 行带南侧空档一路西行
        {"from": ("pin", 6), "to": ("hole", "zx.j35"), "cls": "wire_sig",
         "pts": jog(col_x(50), col_x(50) - SEAM)
                + [(col_x(50) - SEAM, Y_J_S), (col_x(35) - SEAM, Y_J_S),
                   (col_x(35) - SEAM, row_y("zx", "j")),
                   (col_x(35), row_y("zx", "j"))], "label": None},
        # ⑧ Data(130.81) -> zx.j36（GPIO2）：换道东缝 132.08 竖下，走 j 行与下内轨之间的空档
        #    （与 Clk 的 -10.80 空档错开一层，避免两条长横档叠在同一 y 上）
        {"from": ("pin", 7), "to": ("hole", "zx.j36"), "cls": "wire_sig",
         "pts": jog(col_x(51), col_x(51) - SEAM)
                + [(col_x(51) - SEAM, Y_J_S2), (col_x(36) + SEAM, Y_J_S2),
                   (col_x(36) + SEAM, row_y("zx", "j")),
                   (col_x(36), row_y("zx", "j"))], "label": None},
        # ⑨ DC(133.35) -> zy.k45（GPIO46，跨拼接缝）：换道东缝 134.62 竖下，穿 X 板下轨、
        #    拼接缝、Y 板上轨，到 k 行带北侧空档西行到 k45（橙线，与 TB6612 电机线同色系但更靠北）
        {"from": ("pin", 8), "to": ("hole", "zy.k45"), "cls": "wire_mtr",
         "pts": jog(col_x(52), col_x(52) - SEAM)
                + [(col_x(52) - SEAM, Y_K_N), (col_x(45) - SEAM, Y_K_N),
                   (col_x(45) - SEAM, row_y("zy", "k")),
                   (col_x(45), row_y("zy", "k"))], "label": None},
    ]

    return {
        "id": "oled",
        "label": "OLED 1.3\"",
        "sub": "SH1106 · SPI · 屏幕朝上",
        "board": "zx",
        "boards": ("zx", "zy"),
        "off_board": True,                     # 本体在板外：不占针孔、无挡孔
        "label_pos": (bx0 + body_w / 2.0, by0 + body_h - 5.4),
        "sub_pos": (bx0 + body_w / 2.0, by0 + body_h - 2.6),
        "pins": pins,
        "body": (bx0, by0, body_w, body_h),
        "blocked_rects": [],                   # 本体整块在板外，不压任何孔
        "wires": wires,
        # 屏幕玻璃 + 玻璃上的分辨率丝印（针位圆环与针名由通用渲染器按 xy 焊盘自动画）
        "extra_svg": (
            '<rect x="%s" y="%s" width="%s" height="%s" rx="1.0" fill="#0f172a" stroke="#1f2937" stroke-width="0.25"/>'
            % (fmt(bx0 + 2.4), fmt(by0 + 2.4), fmt(body_w - 4.8), fmt(by1 - by0 - 13.0))
            + text(bx0 + body_w / 2.0, (by0 + 2.4 + by1 - 13.0) / 2.0 + 0.7,
                   "128 × 64", 2.2, "#94a3b8", "middle", "oled-screen")
        ),
    }
OLED = make_oled()

# 元件 7/9：模式按键 + 10k 上拉（GPIO9）
# **位置是被迫重排了三次才定下来的**（2026-10-19）：
#   ① GPIO9 的网 = Y 板列 46 上半区（k–o 同列一条），但 k46 是 ESP32 针脚本身、
#      m46/n46/o46 全被 MAX98357A 本体（x 114.30–132.08、y 17.10–36.19）挡死——
#      整条网只剩 **l46** 一个孔能用，按键和上拉都要接 GPIO9，一个孔不够。
#      → 解法：用元件 9 的 GPIO9-EXT 跳线把该网"搬"到西边的空列。
#   ② 第一次搬到列 41–43：被 INMP441 的三根信号弧线压住（它们自东向西横扫 n/o 行，
#      最西只到 m32 = x 82.55，列 41–43 正好在弧线走廊里）。
#   ③ 第二次搬到列 29–31：又被电源区的横档压住——针脚丝印画在孔上方 1.55mm，
#      正好落在行间空档里（n 行丝印 y=19.40 卡在 19.68 横档旁 0.28mm），
#      而 3V3 西段馈线的横档必须从列 32（x 82.55）西行，绕不开 x=80.01。
#   → 最终：**列 27–29**（按键在列 29、上拉电阻跨列 27←29）。这样所有电源横档的
#      x 区间（≥76.20）都与按键/电阻的丝印（≤74.93）隔开 ≥1.27mm，互不遮挡。
#   插法（列 27/29 上半区，中隔槽以北）：
#     按键 一脚 o29（GPIO9 扩展）— 另一脚 p29（列 29 **下半区**，独立网，由 GND-EXT 定义为 GND）
#           ——脚距 7.62mm：正好跨中隔槽上下插，这是唯一能凑出按键脚距又不同网的插法。
#     上拉 一脚 n29（GPIO9 扩展）— 另一脚 n27（列 27，由 3V3-EXT 定义为 3V3），
#           ——跨 2 列 5.08mm，同在 n 行横插，正合轴向电阻。
# 电气：平时 GPIO9 被 10k 拉到 3V3（高），按下接地（低）→ 固件取低电平 / 下降沿。
# 本体：按键 6×6 卡在 o/p 行之间的空档（y 24.30–30.30）**一个孔都不挡**；电阻压 n 行（挡 n28 一孔）。
KEY_BODY = (71.93, 24.30, 6.0, 6.0)           # 轻触开关本体（跨中隔槽，中心 x=列 29 = 74.93）
RES_BODY = (68.32, 19.95, 8.20, 2.00)         # 轴向电阻本体（n 行，跨列 27 → 29）
# 注意：body 用 (x0,y0,w,h)，而 blocked_rects 用 **(x0,y0,x1,y1)**（auto_blocked 按后者判定），
# 两种格式别混——混了要么整块不挡孔、要么挡错一整片。
KEY_BLOCK = (71.93, 24.30, 77.93, 30.30)
RES_BLOCK = (68.32, 19.95, 76.52, 21.95)
BUTTON = {
    "id": "button",
    "label": "模式按键",
    "sub": "GPIO9 · 10k 上拉",
    "board": "zy",
    "boards": ("zy",),
    "body": KEY_BODY,
    "label_pos": (69.2, 25.6),                # 标签放本体西侧空区（东侧是 INMP441 的弧线走廊）
    "sub_pos": (69.2, 27.6),
    "label_anchor": "end", "sub_anchor": "end",
    "label_size": 1.25, "sub_size": 0.95,
    "pins": [("o", 29, "GPIO9"), ("p", 29, "GND"),
             ("n", 29, "GPIO9"), ("n", 27, "3V3")],
    "blocked_rects": [KEY_BLOCK, RES_BLOCK],
    "wires": [],                              # 无跳线：网关系由元件 9 的三根扩展线定义
    "extra_svg": (
        # 电阻：米色本体 + 4 道色环（棕1 / 黑0 / 橙×1k / 金±5% = 10kΩ）
        rect(RES_BODY[0], RES_BODY[1], RES_BODY[2], RES_BODY[3], 1.0,
             "#e7e5e4", "#78716c", 0.2, "res-body")
        + "".join(line(RES_BODY[0] + 1.6 + 1.3 * i, RES_BODY[1] + 0.35,
                       RES_BODY[0] + 1.6 + 1.3 * i, RES_BODY[1] + RES_BODY[3] - 0.35,
                       c, 0.5, "res-band")
                  for i, c in enumerate(("#7c2d12", "#111827", "#ea580c", "#b45309")))
        # 键帽/丝印一律由 KEY_BODY 中心推出来，**不要再写死 x**（2026-10-19 教训：
        # 第二次搬位时只改了 KEY_BODY、忘了改这里的 103.4/105.41，结果图形停在旧列 41，
        # 与列 31 的针环分家——本体矩形和图形必须同源，否则错位看不出来。）
        + text(RES_BODY[0] - 1.4, 21.15, "10k", 0.9, "#374151", "end", "res-label")
        # 按键：深色底座 + 圆形按钮帽（画在本体之上，中心 = 按键所在列）
        + rect(KEY_BODY[0] + 1.0, 24.9, 4.0, 4.0, 1.2, "#1f2937", "#111827", 0.2, "key-cap")
        + '<circle cx="%s" cy="26.9" r="1.1" fill="#4b5563" stroke="#111827" '
          'stroke-width="0.15" class="key-btn"/>' % fmt(KEY_BODY[0] + 3.0)
        + text(KEY_BODY[0] + 3.0, 29.6, "模式", 0.85, "#f3f4f6", "middle", "key-label")
    ),
}

# 元件 8/9：MP1584EN 降压模块（7.4V → 5V，3A）
# 用户 2026-10-19 决策：系统电源 = 7.4V 电池 → MP1584EN 降压 → ESP32 5V 脚（k52）与 5V 轨；
#   电机 VM 直取 7.4V（不经降压，见 TB6612 的 VM 线）。模块 22.2×17.5 且带大电感、
#   走 1–3A 电流 → 本体放板外，只把 4 根线落孔：IN+/IN− 接电池，OUT+ → 5V 轨
#   （Y bottom_inner，即 MAX98357A 的 VIN）、OUT− → GND 轨（Y bottom_outer）。
# 位置：南面板外 x[70,92] y[76,93.5]——西邻 VL53 侧视图（本轮已移到北面）、
#   东邻 TB6612 框（x≥92）、下方是图例文字（y≥98），是唯一放得下 22mm 宽模块、
#   又同时靠近电池（x≈30）与两条下轨的位置。
# IN− 与 OUT− 在模块内部直通（共地），所以电池负极经模块自动并入系统地，无需额外跳线。
MP_BODY = (70.0, 76.0, 22.0, 17.5)
MP1584 = {
    "id": "mp1584en",
    "label": "MP1584EN",
    "sub": "7.4V → 5V · 3A",
    "board": "zy",
    "boards": ("zy",),
    "off_board": True,                        # 本体板外：不占孔、不挡孔
    "body": MP_BODY,
    "label_pos": (81.0, 89.0),
    "sub_pos": (81.0, 91.0),
    "label_size": 1.5, "sub_size": 1.0,
    "pins": [
        {"xy": (76.0, 76.0), "lab": "OUT+"},  # 0 上沿（北上入 5V 轨）
        {"xy": (86.0, 76.0), "lab": "OUT−"},  # 1 上沿（北上入 GND 轨）
        {"xy": (70.0, 84.0), "lab": "IN+"},   # 2 左沿（西接电池正极）
        {"xy": (70.0, 90.0), "lab": "IN−"},   # 3 左沿（西接电池负极）
    ],
    "blocked_rects": [],
    "wires": [
        # OUT+：北上走列 29/30 缝（76.20）→ **t 行与下内轨之间的空档 43.81** 东行
        #    → 落 bottom_inner_38（x=120.65）。
        #    为什么必须落在东段（2026-10-19 定的硬约束）：每条导轨在 x=82.55 处断开、左右两段
        #    电气不互通；5V 的负载（MAX98357A 的 VIN@41、ESP32 5V 脚@42）全在东段，
        #    OUT+ 若落西段（如最早的 hole 25，x=80.01）等于 5V 根本没接上。
        # （落点两次调整，都是 wire_check 逼出来的：31 → TB6612 的 VCC 贝塞尔线擦过（TB 的
        #    VCC 是 3V3 不是 5V，贴在一起会读成 3V3/5V 碰线）；28 → 与 TB VCC 不再相碰，
        #    但引出下面第 4 条硬约束——**轨金属 5 孔一小组**：28 在第 1 组、负载在第 4 组，
        #    中间要搭 3 根桥，而桥 30→31 正好压住 TB VCC 线的必经之路。
        #    改落 38（第 3 组）后只需 1 根桥（40→41）就接到负载所在的第 4 组，桥也全部
        #    避开 TB VCC 线——少画两根线，还更稳。）
        {"from": ("pin", 0), "to": ("hole", "zy.bottom_inner_38"),
         "pts": [(76.0, 76.0), (76.20, 76.0), (76.20, 43.81),
                 (120.65, 43.81), (120.65, 46.35)], "cls": "wire_5v", "label": "5V + 轨"},
        # OUT−：北上走列 33/34 缝（86.36）→ **两下轨之间的空档 47.62** 东行
        #    → 落 bottom_outer_34（x=107.95）。与 OUT+ 分层走（43.81 / 47.62）避免
        #    两根不同网络的线在同一条横档上共线；与按键地线的 47.62 段（x 78.74–85.09）
        #    也留出 1.27mm 间隔不重叠。
        # （落点从 hole 34 移到 hole 35：TB6612 的 PWMB 斜线从 hole 34 上擦过，
        #   电机线压在 GND 轨孔上会误读；35 与按键地线的 47.62 横档（x 71.12–85.09）
        #   还隔着 1.27mm，不重叠。）
        {"from": ("pin", 1), "to": ("hole", "zy.bottom_outer_35"),
         "pts": [(86.0, 76.0), (86.36, 76.0), (86.36, 47.62),
                 (110.49, 47.62), (110.49, 48.89)], "cls": "wire_gnd", "label": None},
        # IN+：从电池正极引线上的分接点 (32,73) 南下 → 西行 → 入左沿针（与 TB VM 同源并联）
        {"from": ("xy", (32.0, 73.0)), "to": ("pin", 2),
         "pts": [(32.0, 73.0), (32.0, 84.0), (70.0, 84.0)], "cls": "wire_vcc", "label": None},
        # IN−：电池负极短片下端 (30.2,77) 南下 → 西行 → 入左沿针
        {"from": ("xy", (30.2, 77.0)), "to": ("pin", 3),
         "pts": [(30.2, 77.0), (30.2, 90.0), (70.0, 90.0)], "cls": "wire_gnd", "label": None},
    ],
    "extra_svg": (
        # 板载电感（黑色方块）+ 可调电位器（圆），实物形制
        rect(73.5, 79.5, 6.5, 4.2, 0.4, "#0f172a", "#334155", 0.2, "mp-inductor")
        + text(76.75, 82.4, "L1", 0.9, "#94a3b8", "middle", "mp-label")
        + '<circle cx="84.5" cy="81.6" r="1.9" fill="#e5e7eb" stroke="#374151" '
          'stroke-width="0.2" class="mp-pot"/>'
        + text(84.5, 85.2, "ADJ", 0.75, "#bbf7d0", "middle", "mp-label")
        + text(81.0, 78.2, "IN ← 电池 7.4V", 0.85, "#bbf7d0", "middle", "mp-label")
        + text(81.0, 93.0, "OUT → 5V 轨", 0.85, "#bbf7d0", "middle", "mp-label")
    ),
}


def rail_bridge(prefix, rname, i, j, cls):
    """轨组短跳线（2026-10-19 新增）：把同一条轨上**相邻两个 5 孔小组**搭通的 5.08mm 跳线。

    为什么必须画（血的教训）：轨印线是连续的，看着一条轨全通；实际上金属条 5 孔一断，
    组间 5.1mm 空隙。元件 9 的 9 根馈线各落各的小组，若不搭桥就会出现"图画了、电不通"：
    降压输出接不到功放、5 个"地"互不相通。面包板上这就是标准操作——用短跳线桥接轨组。
    画法：从孔 i 中心直线到孔 j 中心（i、j 必须相邻，即 j = i+1 且跨组界），两端都画落孔
    彩环（both_rings），白底彩线与其它跳线同规格。因为只连通同网，落在同组邻居孔上也不报假短路。
    """
    def xy(i):
        return (rail_hole_x(i), dict(RAIL_YS)[rname] + (X_TY if prefix == "zx" else Y_TY))
    return {"from": ("hole", "%s.%s_%d" % (prefix, rname, i)),
            "to": ("hole", "%s.%s_%d" % (prefix, rname, j)),
            "pts": [xy(i), xy(j)],
            "cls": cls, "label": None, "both_rings": True}


def make_power():
    """元件 9：电源区——把 4 条电源轨真正接成 3V3 / 5V / GND 网（2026-10-19 定案）。

    三条硬约束（决定了下面每一根线的走向）：

    1. **每条导轨沿长度方向在 x=82.55 处断开，左右两段电气不互通**。X 板上轨的负载分布在
       两侧——VL53 的 VIN/GND 在西段（top_*_23/25）、OLED 的 Gnd/Vin/3v3/CS/SA0 在东段
       （_35/_36/_38/_40），只接一段另一侧就没电。故 3V3 与 GND 各画两根（西段、东段各一），
       而不是"接一孔全轨通电"。Y 板下轨同理：所有 5V/GND 负载都在东段，OUT+ 必须落东段。
    2. **只有 x < 80.35 的竖廊是干净的**：ESP32 板身占 x[80.65,137.80]，VL53 模块本体占
       x[82.90,110.14]（还把上轨整段盖住）。所以从 Y 板去 X 板上轨，一律先西行到板身以西
       （71.12 / 76.20 / 78.74 三条列间缝），再沿 X 板的**行间空档**东行、在目标列北上。
       这样整条线不压板身、不压 VL53 本体——比"竖穿板身"干净得多，也不必再开白名单。
       行间空档离上下两行孔心各 1.27mm > 接触阈值 1.05mm，横档走这里一个孔都不碰。
    3. **不同网络的线不能共线**：两根线画在同一条横档/竖带上 = 图上短接。所以 9 根线各自
       占用不同的 y 或不同的 x 区间（见每根线的注释），交叉（单点互跨）可以，重叠不行。

    第 4 条硬约束（2026-10-19 补，前面 9 根线全部接完才发现，属于电气正确性 bug）：
    **面包板电源轨印成连续红线/蓝线，但金属条实际是"5 孔一小组"**（RAIL_HOLES_PER_SIDE=25、
    RAIL_GROUP_STEP=15.24=6×2.54，组与组之间 5.1mm 空隙）。也就是说：往一个轨孔里插线，
    只通电同组另外 4 个孔，左右邻居全部不通。上面 9 根馈线各自落进自己的小组后，逐个查账发现
    4 处"看着接了、实际没接"：
      · zx.top_inner 东段：3V3 馈线落 35 号（第 2 组），OLED 的 Vin/3v3 落 36/38（第 3 组）→ 不通
      · zx.top_outer 东段：GND 馈线落 36（第 3 组），OLED 的 Gnd 落 35（第 2 组）→ 不通
      · zy.bottom_inner：MP1584 的 OUT+ 落 28（第 1 组），MAX 的 VIN + ESP32 5V 脚落 41/42（第 4 组）
        → 降压输出根本没接到功放和板子，**整机不会上电**
      · zy.bottom_outer：按键地(26)、MP1584 OUT−(35)、功放地(40)、电机地/板子地(44)、麦克风地(50)
        各自落在 5 个不同小组 → 5 个"地"互不相通，**等于没有共地**
    修法：加 9 根"轨组短跳线"（W[9:] 起，见 rail_bridge()）把同一条轨上的相邻小组两两搭通，
    这正是实物面包板上的标准做法（跳线桥接轨组）。它们只有 5.08mm 长、就躺在轨孔连线上，
    没有白底也能看清（轨印线在轨孔外 1.6mm，不打架），也不需要新开白名单。

    轨功能（用户决策）：X top_inner = 3V3（只带小电流：VL53 ~20mA + OLED ~20mA）；
      X top_outer = GND；Y bottom_inner = 5V（MAX98357A 功放，峰值 0.4–1A 不压
      ESP32 板载 LDO）；Y bottom_outer = GND。Y 板上轨与 X 板下轨被 ESP32 板身遮住，不可用。

    另外三根是"网定义扩展线"，给元件 7 的按键与上拉就近造出 GPIO9 / 3V3 / GND 三个网。

    本元件没有本体（不是实物，是一组网定义跳线），只在图例里说明。
    """
    SEAM = 1.27
    # 行间空档（离上下两行孔心各 1.27 > 接触阈值 1.05，横档走这里不压任何孔）
    Y_KL = (row_y("zy", "k") + row_y("zy", "l")) / 2.0      # 14.60（= ESP32 板身南边界）
    Y_LM = (row_y("zy", "l") + row_y("zy", "m")) / 2.0      # 17.14
    Y_MN = (row_y("zy", "m") + row_y("zy", "n")) / 2.0      # 19.68
    Y_NO = (row_y("zy", "n") + row_y("zy", "o")) / 2.0      # 22.22
    Y_TGAP2 = (row_y("zy", "t") + RAIL_YS[2][1] + Y_TY) / 2.0   # 43.81 t 行与下内轨之间
    Y_HI = (row_y("zx", "h") + row_y("zx", "i")) / 2.0      # -15.88（X 板 h/i 行间）
    Y_HG = (row_y("zx", "g") + row_y("zx", "h")) / 2.0      # -18.42（X 板 g/h 行间）
    Y_GF = (row_y("zx", "f") + row_y("zx", "g")) / 2.0      # -20.96（X 板 f/g 行间）
    Y_BGAP = (RAIL_YS[2][1] + RAIL_YS[3][1]) / 2.0 + Y_TY   # 47.62（Y 板两下轨之间）
    Y_TGAP = (RAIL_YS[0][1] + RAIL_YS[1][1]) / 2.0 + X_TY   # -46.36（X 板两上轨之间）
    Y_NORTH = -49.04                                        # 上外轨以北空档（OLED 换道用）

    def rc(prefix, letter, c):
        return (col_x(c), row_y(prefix, letter))

    def rail(prefix, rname, i):
        return (rail_hole_x(i), dict(RAIL_YS)[rname] + (X_TY if prefix == "zx" else Y_TY))

    # 板身以西的四条干净竖廊（列间缝，x < 80.35 才不压 ESP32 板身；互相错开一整列，
    # 保证任意两根不同网络的线都不会共用同一条竖带）
    CL_W1 = col_x(30) + SEAM    # 78.74  3V3 西段用
    CL_W2 = col_x(29) + SEAM    # 76.20  3V3 东段用
    CL_W3 = col_x(28) + SEAM    # 73.66  GND 西段用
    CL_W4 = col_x(27) + SEAM    # 71.12  按键地线用（南段，y 31.11→47.62）
    # 板身以东、VL53 本体以东的两条竖廊（111.76 / 114.30：都在 VL53 右缘 110.14 之外，
    # 又避开了 OLED 已占用的 113.03 / 115.57 / 116.84 / 118.11 / 119.38）
    CL_E1 = col_x(43) + SEAM    # 111.76  3V3 东段北上用
    CL_E2 = col_x(44) + SEAM    # 114.30  GND 东段北上用

    W = []
    # ① 3V3 → X 板上内轨**西段**：n32（3V3_1 网）→ 19.68 横档西行 → 78.74 竖廊北上
    #    → 轨间空档 -46.36 西行 → 落 top_inner_21（69.85）。西段负责 VL53 的 VIN。
    #    为什么不落更靠东的 top_inner_24（77.47）：VL53L0X 的 GND 贝塞尔线正好从
    #    top_inner_24 边上擦过（wire_check 报 OCC x12），3V3 孔贴着 GND 线画会误读成短接；
    #    退到 69.85 就完全避开那条曲线。
    W.append({"from": ("hole", "zy.n32"), "to": ("hole", "zx.top_inner_21"),
              "pts": [rc("zy", "n", 32), (col_x(32), Y_MN), (CL_W1, Y_MN),
                      (CL_W1, Y_TGAP), (rail_hole_x(21), Y_TGAP),
                      rail("zx", "top_inner", 21)],
              "cls": "wire_vcc", "label": None})
    # ② 3V3 → X 板上内轨**东段**：o32（同一 3V3_1 网，换个孔出线避免与 ① 共线）
    #    → 22.22 横档西行到 76.20 → 北上到 -20.96（f/g 行间空档）→ 一路东行到 111.76
    #    → 北上穿两上轨之间的 -46.36 → 西行 1.27 → 落 top_inner_35（110.49）。
    #    为什么走这么大一个 C 形：东段唯一没被 OLED 占的竖廊是 111.76，而它只能从 X 板
    #    行间空档横着过去；-20.96 这一层只有本线用（GND 东段走 -18.42），互不重叠。
    W.append({"from": ("hole", "zy.o32"), "to": ("hole", "zx.top_inner_35"),
              "pts": [rc("zy", "o", 32), (col_x(32), Y_NO), (CL_W2, Y_NO),
                      (CL_W2, Y_GF), (CL_E1, Y_GF),
                      (CL_E1, Y_TGAP), (rail_hole_x(35), Y_TGAP),
                      rail("zx", "top_inner", 35)],
              "cls": "wire_vcc", "label": None})
    # ③ GND → X 板上外轨**西段**：i32（X 板列 32 下半区 = j32 GND_3 同网）
    #    → -15.88 横档西行 → 73.66 竖廊北上 → **外轨以北空档 -49.04** 东行
    #    → 落 top_outer_24（77.47）。
    #    走北侧而不是轨间 -46.36：内/外轨孔 x 相同，从上往下落外轨必须先绕到轨北，
    #    否则会穿过同列的内轨孔（= 把 GND 插进 3V3 轨）。-46.36 那条走廊让给 ①②。
    W.append({"from": ("hole", "zx.i32"), "to": ("hole", "zx.top_outer_24"),
              "pts": [rc("zx", "i", 32), (col_x(32), Y_HI), (CL_W3, Y_HI),
                      (CL_W3, Y_NORTH), (rail_hole_x(24), Y_NORTH),
                      rail("zx", "top_outer", 24)],
              "cls": "wire_gnd", "label": None})
    # ④ GND → X 板上外轨**东段**：h32（同列 32 下半区，换个孔出线）
    #    → -18.42 横档东行 → 114.30 竖廊北上到 -49.04（外轨以北空档）→ 东行 1.27
    #    → 落 top_outer_36（115.57）。
    #    为什么落外轨要走**北侧** -49.04 而不是轨间 -46.36：内轨孔和外轨孔 x 相同，
    #    从上往下落内轨必然穿过外轨孔（等于把 3V3 插进 GND 轨）；而走北侧从上往下落外轨
    #    只经过空档。OLED 的 Vin/3v3 也是这个道理（它们的 -49.04 横档在 x≥115.57，
    #    本线只占 114.30–115.57，正好错开）。
    W.append({"from": ("hole", "zx.h32"), "to": ("hole", "zx.top_outer_36"),
              "pts": [rc("zx", "h", 32), (col_x(32), Y_HG), (CL_E2, Y_HG),
                      (CL_E2, Y_NORTH), (rail_hole_x(36), Y_NORTH),
                      rail("zx", "top_outer", 36)],
              "cls": "wire_gnd", "label": None})
    # ⑤ 5V：bottom_inner_42（5V 轨东段）→ l52（ESP32 5V 脚）**同一列直连**。
    #    x=133.35 在 MAX98357A 本体右缘（132.08）之外，也不碰喇叭符号，中间经过的
    #    m52–t52 都是空孔——真实跳线本来就这么躺在板面上（绝缘皮不接触），比绕远干净。
    W.append({"from": ("hole", "zy.bottom_inner_42"), "to": ("hole", "zy.l52"),
              "pts": [rail("zy", "bottom_inner", 42), rc("zy", "l", 52)],
              "cls": "wire_5v", "label": None})
    # ⑥ GND（Y 板下轨）：l53（GND_4 网）南下到 43.81 空档 → 东行到 137.16（列 53/54 缝）
    #    → 南下穿两下轨之间 → 东行 1.27 → 落 bottom_outer_44（138.43，与 TB6612 的地同孔同网）。
    #    先下到 43.81 再换列是必须的：l53 正下方 x=135.89 有下内轨孔 43（5V 轨），
    #    直接南下会画成"GND 插进 5V 轨"。137.16 离两侧轨孔各 1.27mm。
    W.append({"from": ("hole", "zy.l53"), "to": ("hole", "zy.bottom_outer_44"),
              "pts": [rc("zy", "l", 53), (col_x(53), Y_TGAP2), (col_x(53) + SEAM, Y_TGAP2),
                      (col_x(53) + SEAM, Y_BGAP), (rail_hole_x(44), Y_BGAP),
                      rail("zy", "bottom_outer", 44)],
              "cls": "wire_gnd", "label": None})
    # ⑦ GPIO9 扩展：l46（列 46 上半区唯一没被 MAX 挡住的孔）→ 沿 **y=14.60 板身南边界**
    #    西行 → l29。把 GPIO9 网"搬"到空列 29，按键与上拉才各有孔可插（详见 BUTTON 注释）。
    #    唯一一根要压板身的线：l46 南北都被堵死（北上撞 ESP32 针脚 k46、南下撞 MAX 本体
    #    挡住的 m46），只剩沿板身南缘横穿这一条路——物理上就是"从板子底下穿过去"，
    #    与 TB6612 BIN 竖带、OLED 四根控制线压板身是同一类先例（见 wire_check WHITELIST）。
    W.append({"from": ("hole", "zy.l46"), "to": ("hole", "zy.l29"),
              "pts": [rc("zy", "l", 46), (col_x(46), Y_KL), (col_x(29), Y_KL),
                      rc("zy", "l", 29)],
              "cls": "wire_sig", "label": None})
    # ⑧ 3V3 扩展：l32（3V3_1 网）→ 17.14 横档西行 → l27。列 27 上半区从此 = 3V3，
    #    上拉电阻的另一端（n27）就近取电，不必飞线到 13 列外的 3V3 源。
    W.append({"from": ("hole", "zy.l32"), "to": ("hole", "zy.l27"),
              "pts": [rc("zy", "l", 32), (col_x(32), Y_LM), (col_x(27), Y_LM),
                      rc("zy", "l", 27)],
              "cls": "wire_vcc", "label": None})
    # ⑨ GND 扩展：p29（列 29 **下半区**，与上半区隔中隔槽，独立网）→ 西行到 71.12
    #    → 南下到 47.62（两下轨之间）→ 东行 → 落 bottom_outer_26（85.09，东段）。
    #    71.12 这条竖廊只有本线用（北段的三条竖廊是 73.66/76.20/78.74）。
    W.append({"from": ("hole", "zy.p29"), "to": ("hole", "zy.bottom_outer_26"),
              "pts": [rc("zy", "p", 29), (CL_W4, row_y("zy", "p")),
                      (CL_W4, Y_BGAP), (rail_hole_x(26), Y_BGAP),
                      rail("zy", "bottom_outer", 26)],
              "cls": "wire_gnd", "label": None})
    # ---- 轨组短跳线（W[9:] 起）------------------------------------------------------
    # 把"5 孔一小组"的断点搭通。逐组对照（括号内为落孔来源）：
    #   zx.top_inner 东段：35(3V3 馈线②) → 36/38(OLED Vin/3v3)      —— 3V3 进 OLED
    #   zx.top_outer 东段：35(OLED Gnd) → 36(GND 馈线④)/38/40(CS/SA0) —— GND 上 OLED
    #   zy.bottom_inner 东段：38(MP1584 OUT+) → 41(MAX VIN)/42(ESP32 5V 脚)
    #        只跨 1 个组界（40|41）。OUT+ 落点从 28 改到 38 就是为了少跨两组——少两根桥，
    #        也避开 TB6612 VCC 线的必经之路（详见 mp1584en w0 注释）。
    #   zy.bottom_outer 东段：26(按键地) → 35(MP1584 OUT−) → 40(功放地)
    #        → 44(电机地 + ESP32 GND 脚) → 46/50(麦克风地) —— 五个"地"并成一个地
    W.append(rail_bridge("zx", "top_inner", 35, 36, "wire_vcc"))
    W.append(rail_bridge("zx", "top_outer", 35, 36, "wire_gnd"))
    W.append(rail_bridge("zy", "bottom_inner", 40, 41, "wire_5v"))
    W.append(rail_bridge("zy", "bottom_outer", 30, 31, "wire_gnd"))
    W.append(rail_bridge("zy", "bottom_outer", 35, 36, "wire_gnd"))
    W.append(rail_bridge("zy", "bottom_outer", 40, 41, "wire_gnd"))
    W.append(rail_bridge("zy", "bottom_outer", 45, 46, "wire_gnd"))
    return {
        "id": "power", "label": "", "sub": "",          # 无本体，仅在图例说明
        "board": "zy", "boards": ("zy", "zx"),
        "body": (0.0, 0.0, 0.0, 0.0),
        "pins": [], "blocked_rects": [], "wires": W, "extra_svg": "",
    }


POWER = make_power()
COMPONENTS = [INMP441, MAX98357A, TB6612FNG, VL53L0X, OLED, BUTTON, MP1584, POWER]

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
        for rname, ry in RAIL_YS:                       # 导轨孔（VL53 板外段只压轨道）
            for i in range(1, 2 * RAIL_HOLES_PER_SIDE + 1):
                x, y = rail_hole_x(i), ry + ty
                for x0, y0, x1, y1 in rects:
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        nm = "%s.%s_%d" % (prefix, rname, i)
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
        # 轨组短跳线：两端孔都插了 jumper，两端都画落孔彩环（普通跳线的彩环跟 label 绑定，
        # 桥线没有文字标签，所以单独画，否则图上完全看不出这两个孔被占用）
        if w.get("both_rings"):
            for end in (w["from"], w["to"]):
                if end[0] == "hole":
                    hx, hy = hole_xy(end[1])
                    p.append('<circle cx="%s" cy="%s" r="0.85" fill="none" stroke="%s" stroke-width="0.3" class="wire-land"/>'
                             % (fmt(hx), fmt(hy), COLOR[w["cls"]]))
    p.append('</g>')
    return "".join(p)


def vl53_side_view():
    """图例区侧视小图（占 y=60..96 空带，避开 TB 板外方框 x≥92 与 Y 板下缘 55.23）：
    VL53L0X 支架前倾 30°，光束打到狗身前方的桌面/地板——悬空时距离读数突增（防跌落），
    并保留 140mm 内近距防撞。触发距离 d = h ÷ tan30° ≈ 1.7h。"""
    # 2026-10-19 挪位：原在南面 x[0,70] y[60,96]，画在图例层会**盖住 TB6612 的 7.4V 电池符号**
    # （电池在 x 27–32 / y 64–84，与它重叠）；且南面要让给元件 8 MP1584EN（x[70,92] y[76,93.5]）
    # 和电池→模块的引线。北面 x[2,72] y[-100,-64] 是空区（东邻 VL53 本体 x0=82.9、
    # 南邻标题文字基线 y=-61.84），故整体平移到那里（内部坐标不动，只加 transform）。
    p = ['<g id="vl53-side" class="doc-inset" transform="translate(2 -160)">']
    p.append('<rect x="0" y="60" width="70" height="36" rx="4" fill="#f9fafb" stroke="#b4b2a9" stroke-width="0.25"/>')
    p.append(text(35, 65.5, "侧视 · VL53 支架前倾 30°", 1.4, "#085041", "middle", "doc-legend", bold=True))
    p.append('<rect x="4" y="88" width="62" height="2.5" fill="#B4B2A9" stroke="#888780" stroke-width="0.25"/>')
    p.append(text(58, 92.8, "桌面", 1.1, "#5F5E5A", "middle", "doc-legend"))
    p.append('<rect x="26" y="68" width="34" height="9" rx="4" fill="#9FE1CB" stroke="#0F6E56" stroke-width="0.25"/>')
    p.append(text(43, 72.5, "狗身", 1.1, "#085041", "middle", "doc-legend"))
    p.append('<circle cx="32" cy="80.5" r="3" fill="#FFFFFF" stroke="#5F5E5A" stroke-width="0.25"/>')
    p.append('<circle cx="54" cy="80.5" r="3" fill="#FFFFFF" stroke="#5F5E5A" stroke-width="0.25"/>')
    p.append('<g transform="translate(22 82) rotate(-30)">'
             '<rect x="-5" y="-2.25" width="10" height="4.5" rx="1" fill="#AFA9EC" stroke="#534AB7" stroke-width="0.25"/>'
             '<rect x="-5" y="-2.25" width="2" height="4.5" fill="#26215C" stroke="none"/>'
             '</g>')
    p.append('<line x1="22" y1="82" x2="34" y2="82" stroke="#888780" stroke-width="0.25" stroke-dasharray="2 1.5"/>')
    p.append(text(28, 85.5, "30°", 1.1, "#444441", "middle", "doc-legend"))
    p.append('<line x1="18.5" y1="84" x2="11.6" y2="88" stroke="#E24B4A" stroke-width="0.8" stroke-dasharray="2.5 1.5" marker-end="url(#arrow)"/>')
    p.append('<line x1="9" y1="84" x2="9" y2="88" stroke="#888780" stroke-width="0.25" stroke-dasharray="1.5 1.2" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
    p.append(text(9, 86.6, "h", 1.1, "#444441", "middle", "doc-legend"))
    p.append('<line x1="11.6" y1="91" x2="22" y2="91" stroke="#888780" stroke-width="0.25" stroke-dasharray="1.5 1.2" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
    p.append(text(23.5, 91.7, "d ≈ 1.7h", 1.1, "#444441", "start", "doc-legend"))
    p.append('</g>')
    return "".join(p)


# ---------------------------------------------------------------- 图例（数据驱动 + 自动换行）
# 2026-10-19 元件 7/8/9 入图时改写。原来每条图例手写 y 坐标、不换行，实测最长一行
# 右缘 x=330.4mm，而画布右缘只有 173.1mm —— <img> 内嵌时行尾直接被裁掉，
# 预览 PNG 右缘那道纯色切边就是它（文字结尾一直看不到）。现在：文本进列表
#   → 按可用宽度折行 → 行高累加 → 末行基线回算画布高度。
# 图例加减行、改文案都不用再动任何坐标常量。
LEGEND_STYLES = {                  # 样式 -> (字号, 颜色键, 行高, 加粗)
    "title": (2.4, "title", 4.4, True),
    "pcb":   (1.8, "pcb",   4.4, False),
    "note":  (1.8, "note",  4.4, False),
    "warn":  (1.8, "warn",  4.4, False),
}
_BREAKS = "，、；：）」』》" + " "   # 折行优先断点（断点标点留在上行末，避免行首孤字）

_MSFONT = None


def _measure_font():
    """PIL + 微软雅黑（100px 模板字号）；PIL 不可用返回 None（调用方走 em 估算）。"""
    global _MSFONT
    if _MSFONT is None:
        _MSFONT = False
        try:
            from PIL import ImageFont
            for pth in (r"C:\Windows\Fonts\msyh.ttc",
                        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
                        r"C:\Windows\Fonts\simhei.ttf"):
                try:
                    _MSFONT = ImageFont.truetype(pth, 100)
                    break
                except Exception:
                    continue
        except Exception:
            pass
    return _MSFONT or None


def text_w(s, size):
    """字符串在给定字号（mm）下的近似渲染宽度（mm）。
    优先 PIL + 雅黑度量（浏览器 sans-serif 的合理近似，量出的宽度略小于 Noto，偏保守）；
    不可用时按 em 宽估（CJK/全角 ≈ 1.0em，窄字母 ≈ 0.3em，数字 ≈ 0.56em）。"""
    f = _measure_font()
    if f:
        return f.getlength(s) * size / 100.0
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E7F:                      # CJK / 全角标点
            w += 1.0
        elif ch in "iljtf.,:;'\"|!()[]{}":
            w += 0.30
        elif ch in "mwMW":
            w += 0.85
        elif ch == " ":
            w += 0.28
        elif ch.isdigit():
            w += 0.56
        else:
            w += 0.55
    return w * size


def wrap_text(s, size, max_w):
    """按可用宽度折行。优先在标点/空格处断（标点留上行末），找不到就近断点才硬断。"""
    lines, cur = [], ""
    for ch in s:
        if not cur or text_w(cur + ch, size) <= max_w:
            cur += ch
            continue
        cut = max(cur.rfind(b) for b in _BREAKS)
        if cut >= max(1, len(cur) - 8):     # 断点离行尾不远才回退，否则硬断
            lines.append(cur[:cut + 1])
            cur = cur[cut + 1:] + ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def legend_items(include_holes):
    """图例内容：每条 = (样式, 文本)；样式见 LEGEND_STYLES。孔三态数字动态算，不写死。"""
    comp_occ, comp_blk = component_hole_marks()
    n_mod_occ = sum(len(v) for v in comp_occ.values())      # 元件针孔 + 跳线落孔
    n_mod_blk = sum(len(v) for v in comp_blk.values())      # 元件本体挡孔
    n_dev_blk = len(dev_blocked_rail_names())               # ESP32 靠缝 4 轨挡孔
    n_occ = 2 * DEV_PINS_PER_ROW + n_mod_occ
    n_blk = n_dev_blk + n_mod_blk
    blk_parts = ["ESP32 靠缝 4 轨 %d 孔" % n_dev_blk]
    for comp in COMPONENTS:
        n = len(comp_blk.get(comp["board"], ()))
        if n:
            blk_parts.append("%s 本体 %d 孔" % (comp["label"], n))
    if not include_holes:
        return [
            ("title", "轮廓要素：板体 165.1 × 54.6 × 2 / 中隔槽 / 电源轨标线（外 − 内 +，"
                      "中间断开）/ 拼接缝 / 行列标号（63 列；X 行 a–j，Y 行 k–t）"),
            ("note", "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                     "Y = | - + | k l m n o | p q r s t | + - |"),
            ("warn", "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通。"),
            ("note", "第二步：叠加全部 1660 个插孔；第三步：拼接缝正中央插入 ESP32-S3"
                     "（USB 朝右：上排 J3 → X 板 j 行、下排 J1 → Y 板 k 行）。"
                     "第四步起逐个接入元件（当前 9/9，明细见 breadboard-z.svg 图例）。"),
            ("note", "本图为规划图，几何/电气均为近似，不代表实测结果。"),
        ]
    return [
        ("title", "行序（自上而下，'|' = 隔断）：X = | - + | a b c d e | f g h i j | + - | ；"
                  "Y = | - + | k l m n o | p q r s t | + - |"),
        ("note", "每板 830 孔 = 终端 10 行 × 63 列（630）+ 电源轨 4 条 × 50 孔（200）；"
                 "Z 共 1660 孔。孔距 2.54 mm，单位 mm。"),
        ("note", "ESP32-S3 N16R8（双 Type-C，44 针）插在拼接缝正中央横跨两板，USB 口朝右："
                 "上排 22 针 = J3 → X 板 j 行、下排 22 针 = J1 → Y 板 k 行"
                 "（均为列 32–53，左→右 = 列 32→53）。"),
        ("note", "J1/J3 按官方 pinout（user guide v1.1）判定：USB 朝右时 J1 在下（k 行）、"
                 "J3 在上（j 行），与最初'J1→j、J3→k'的表述互为镜像；两种取向只能取一，"
                 "本图取 USB 朝右。"),
        ("pcb", "元件 1：INMP441 数字麦克风（I2S）圆形小板 Ø15mm，上排针 SCK/WS/L/R 插 o 行"
                "60–62 列、下排针 SD/VDD/GND 插 p 行 60–62 列——两排针骑中隔槽分插两个半区"
                "（同半区会同列短接）。"),
        ("note", "接线圈 5 根 + 1 根模块内短跳线（线端彩环 = 跳线落孔）：红 VDD→m32（3V3）；"
                 "紫 SCK/WS/SD→m35/m36/m37（GPIO4/5/6，J1 针同列 l–o 同网）；"
                 "黑 GND→下轨外轨 − 第 50 孔；L/R 与 GND 两针短接（选左声道）。"),
        ("pcb", "元件 2：MAX98357A I2S 功放（7 针单排 + 顶部 2P 喇叭端子），7 针插 Y 板 q 行"
                "（下半区）列 45–51，丝印左→右 = LRC/BCLK/DIN/GAIN/SD/GND/VIN；"
                "本体下沿距 ESP32 板身 2.5mm。"),
        ("note", "接线圈 8 根 + 1 根模块内短跳线（线端彩环 = 跳线落孔）："
                 "紫 LRC/BCLK/DIN→m40/m39/m38（GPIO16/15/7）；GAIN→GND、SD→VIN 两针短接"
                 "（用户决策，替代默认悬空）；黑 GND→下轨外轨 − 第 40 孔；"
                 "深红 VIN→下轨内轨 + 第 41 孔（5V 网——2026-10-19 用户决策：3V3 只带小电流，"
                 "功放峰值 0.4–1A 改由 5V 承担，颜色必须和实际电位一致，否则会把 5V 插进 3V3 轨、"
                 "ESP32 LDO 被喇叭峰值电流拖垮）；青 VO+/VO−→喇叭 4Ω 3W 示意符号。"),
        ("pcb", "元件 3：TB6612FNG 双路电机驱动（16 针双列，VM 4.5–10V、1.2A/路）。"
                "VM 电源与 1A+ 电机电流绝不上面包板——模块本体（方框）画在板外 y[63,87]，"
                "只把 9 根控制/电源线落孔；AO1/AO2、BO1/BO2 接两个电机示意符号（M1/M2），"
                "VM 接 7.4V 电池。"),
        ("note", "接线圈 9 根（橙 = 电机线）+ 板外 5 根：紫 STBY/PWMA/AIN1/AIN2/PWMB→l47–l51"
                 "（GPIO10–14，J1 针同列 l–o 同网；m/n/o 45–51 列被 MAX 本体挡死，l 行是唯一"
                 "合法行，斜线压 MAX 本体下插）；BIN1/BIN2→X 板 i48/i49（GPIO47/21，同列 f–i 同网）；"
                 "红 VCC→o32（3V3）；黑 GND→下轨外轨 − 第 44 孔。"),
        ("pcb", "元件 4 撤销（2026-10-19 用户决策 B）：TCRT5000×5 巡线阵列整体移除——TCRT 模块"
                "镜头在 PCB 正面，插上面包板后朝上照不到地板；量程仅 0.2–15mm，底盘一离地即超程。"
                "防跌落改由 VL53L0X 前倾 30° 承担，GPIO17/18/38/39/40 与对应孔位释放、转为预留"
                "（config.h 中 LINE_SENSOR 宏同轮删除，零代码引用）。"),
        ("note", "前倾几何（见下方侧视小图）：触发距离 d = 安装高度 h ÷ tan30° ≈ 1.7h——h=80mm 时"
                 "桌沿进约 140mm 内即停，并保留 140mm 内近距防撞；代价是 300mm 外的墙/人照不到"
                 "（光束已扎进地面），远距前向避障放弃。"),
        ("pcb", "元件 5：VL53L0X 激光测距（2026-10-19 改决策 B：支架前倾 30°，防跌落 + 近距防撞；"
                "XSHUT/GPIO 悬空）。Adafruit 板 27.24×17.78 单排 6 针 VIN/GND/SCL/SDA/GPIO/XSHUT；"
                "板高 17.78 超过任一半区行带（10.16）→ 本体悬板外上方，安装时整体支架前倾"
                "（见下方侧视小图）。"),
        ("note", "6 针横躺落 a 行列 35–40：SCL/SDA 走东竖带折线（中段贴 38/39 缝、39/40 缝，"
                 "避开 b–h 行同列孔）汇入 i37/i38，与 j37/j38（GPIO42/41）同列同网；"
                 "VIN/GND 绕本体西南角落 top_inner_25/top_outer_23（已由元件 9 接入 3V3/GND 网）；"
                 "GPIO/XSHUT 悬空（板载上拉，默认使能、I2C 0x29）；本体挡板上缘 2 轨 18 孔。"),
        ("pcb", "元件 6：OLED 1.3 寸 SH1106（2026-10-19 决策：本体放板外、屏幕朝上，9 根飞线直连"
                "目标孔）。模块实为 9 针单排 Gnd/Vin/3v3/CS/Rst/SA0/Clk/Data/DC——Rst 在正中第 5 针，"
                "而 GPIO48(Rst)→j47、GPIO1/2(Clk/Data)→j35/j36 首尾跨 12 列 30.5mm，远超模块 9 针"
                " 20.3mm，本体任何起列/翻转都插不进面包板，故本体悬在板外上方、焊盘用飞线接出。"),
        ("note", "飞线必穿上轨：目标孔全在两条上轨以南，9 根线自上而下穿过 top_outer/top_inner；"
                 "轨孔与列孔共用 2.54 栅格（每 5 孔一组、组间空档），正下方有轨孔的线改走列间缝"
                 "（离轨孔 1.27mm > 接触阈值 1.05），在外轨以北空档换道。"
                 "Gnd→top_outer_35、CS→top_outer_38、SA0→top_outer_40（外轨 = GND 网，"
                 "CS/SA0 接地固定片选与地址）；Vin→top_inner_36、3v3→top_inner_38（内轨 = 3V3 网）。"),
        ("note", "Rst→j47(GPIO48)、Clk→j35(GPIO1)、Data→j36(GPIO2) 沿列间缝竖下后在 j 行带空档"
                 "西行进孔，三者分走 y=−13.34 / −10.80 / −9.50 三层互不叠线；DC→k45(GPIO46) 跨拼接缝"
                 "（橙线）。丝印顺序 = 列号顺序（Gnd 在西、DC 在东），与实物朝上放置时一致。"),
        ("pcb", "元件 7：模式按键 + 10kΩ 上拉（GPIO9）。按键 6×6mm 跨中隔槽插 o29/p29"
                "（脚距 7.62mm 上下跨槽，两脚才分属不同网）；上拉电阻横跨 n29←n27（轴向 5.08mm）。"
                "平时 GPIO9 被 10k 拉到 3V3（高），按下接地（低）→ 固件取低电平 / 下降沿。"),
        ("note", "GPIO9 网原本只剩 l46 一个孔可用（k46 是 ESP32 针脚、m46/n46/o46 被 MAX98357A 本体"
                 "挡死），按键 + 上拉要占两个孔，插不下 → 用元件 9 的 GPIO9 扩展跳线把该网搬到列 29；"
                 "按键位置为此重排三次（列 41–43 撞麦克风弧线 → 列 31 撞电源横档丝印 → 定案列 27–29，"
                 "与所有电源横档隔开 ≥1.27mm）。"),
        ("pcb", "元件 8：MP1584EN 降压模块（7.4V → 5V · 3A）。用户 2026-10-19 决策：系统供电 = "
                "7.4V 电池 → 降压 → ESP32 5V 脚（k52）与 5V 轨；电机 VM 直取 7.4V 不经降压。"
                "模块 22.2×17.5 带大电感、走 1–3A → 本体放板外 x[70,92] y[76,93.5]，只把 4 根线落孔。"),
        ("note", "IN+/IN− 接电池（板外，与 TB6612 的 VM 并联取电）；OUT+→下轨内轨 + 第 38 孔"
                 "（5V 轨，给 MAX98357A 的 VIN 与 ESP32 5V 脚供电）；OUT−→下轨外轨 − 第 35 孔"
                 "（GND 轨，IN− 与 OUT− 在模块内部直通，电池负极自动并入系统地）。"
                 "OUT+ 落点三次调整（31 撞 TB6612 的 VCC 贝塞尔线 → 28 与 5V 负载不同轨组 → "
                 "38 只跨 1 个组界，还少两根桥接线）。"),
        ("pcb", "元件 9：电源区（无本体，一组网定义跳线）。轨功能：X 上内轨 = 3V3（只带小电流："
                "VL53 ~20mA + OLED ~20mA）、X 上外轨 = GND、Y 下内轨 = 5V（功放专用）、"
                "Y 下外轨 = GND；Y 板上轨与 X 板下轨被 ESP32 板身遮住，不可用。"),
        ("note", "每条导轨沿长度方向在 x=82.55 处断开、左右两段不互通，而 3V3/GND 的负载分布在两侧"
                 "（VL53 在西、OLED 在东）→ 3V3 与 GND 各画两根馈线（西段、东段各一）。从 Y 板去 X 板"
                 "上轨一律先西行到板身以西的列间缝（78.74/76.20/73.66/71.12），再沿 X 板行间空档东行"
                 "北上——整条线不压板身、不压 VL53 本体。另 3 根是网定义扩展线（GPIO9 / 3V3 / GND "
                 "各一，给元件 7 造网）。"),
        ("warn", "⚠ 血的教训（2026-10-19，wire_check 全绿也查不出）：面包板电源轨印成连续红线/蓝线，"
                 "金属条却是 5 孔一小组（组间 5.1mm 空隙）——轨孔只通电同组另外 4 个孔，左右邻居全不通。"
                 "9 根馈线画完仍 4 处'看着接了、实际没接'：3V3 馈线落 35 而 OLED 的 Vin/3v3 落 36/38；"
                 "GND 馈线落 36 而 OLED Gnd 落 35；MP1584 的 OUT+ 落 28 而 5V 负载落 41/42"
                 "（降压输出没接到功放和板子，整机不上电）；五个'地'分落 5 组（等于没有共地）。"
                 "修法 = 图中 7 根轨组短跳线（both_rings 两端彩环）把相邻小组两两搭通——实物面包板上"
                 "的标准操作。这类错误已固化为 tools/net_check.py 的连通性断言（当前 0 处断网/短接）。"),
        ("note", "地网走向（有意设计）：X 板地经 j32(GND_3)、Y 板地经 k53(GND_4)，仅靠 ESP32 板载"
                 "地平面（4 个 GND 针内部共地）合并，没有跨板地线；电机回流在 Y 板内闭环（电池− → "
                 "MP1584 IN− → 模块内地 → OUT− → 轨 → TB GND），不经过 ESP32；X 板只挂 VL53/OLED "
                 "两个小电流传感器。"),
        ("note", "孔三态：深灰 = 空闲，被 ESP32 针（44）或元件针孔/跳线落孔（%d）占用，合计 %d；"
                 "浅灰 = 被板身遮挡不可插（blocked %d 孔 = %s；OLED 本体整块在板外，不挡孔）。"
                 "空闲 %d 孔。" % (n_mod_occ, n_occ, n_blk, " + ".join(blk_parts),
                                   1660 - n_occ - n_blk)),
        ("warn", "⚠ 拼接缝处两板导轨仅物理相邻、电气不互通；每条电源轨沿长度方向中间断开（图中'断'）。"
                 "中隔槽将 a–e 与 f–j（Y 板 k–o 与 p–t）隔开：同列同半区 5 孔才互通。"
                 "飞线交叉处为绝缘跳线互跨、电气互不相通（wire_check 已把'两根不同网络共线重叠'这类"
                 "假短路查到 0 处）。本图为规划图，几何/电气均为近似。"),
    ]


def legend_layout(include_holes):
    """图例排版。返回 (svg 片段 list, 末行基线 y)。行高累加，末行回算画布高度。"""
    max_w = (CANVAS_X0 + CANVAS_W) - LEGEND_X - 2.0      # 右留 2mm
    out, y = [], 98.0
    for style, s in legend_items(include_holes):
        size, ckey, lh, bold = LEGEND_STYLES[style]
        for ln in (wrap_text(s, size, max_w) if s else [None]):
            if ln:
                out.append(text(LEGEND_X, y, ln, size, COLOR[ckey], "start", "doc-legend", bold))
            y += lh
    return out, y - 4.4                                    # 4.4 = 统一行高，回退一行即末行基线


def build(include_holes):
    legend_frags, legend_bottom = legend_layout(include_holes)   # 先排版：末行基线 → 画布高度
    canvas_h = max(CANVAS_H_MIN, legend_bottom + LEGEND_BOTTOM_PAD - CANVAS_Y0)
    p = ['<?xml version="1.0" encoding="UTF-8"?>']
    p.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s" width="%d" height="%d" font-family="%s">'
             % (fmt(CANVAS_X0), fmt(CANVAS_Y0), fmt(CANVAS_W), fmt(canvas_h),
                PX_W, int(round(PX_W * canvas_h / CANVAS_W)), FONT))
    if include_holes:
        p.append("<title>面包板 Z · 双 830 点并排拼接（28 行 × 63 列 · 共 1660 插孔 · 中央插 ESP32-S3 · "
                 "元件 9/9 全部接入：INMP441 + MAX98357A + TB6612FNG + VL53L0X + SH1106 OLED + "
                 "模式按键 + MP1584EN 降压 + 电源区）</title>")
        p.append("<desc>Two 830-point breadboards joined along their power-rail long edges "
                 "into board Z (28 rows x 63 columns), with an ESP32-S3 board plugged across "
                 "the seam and all 9 planned items wired in: INMP441 mic, MAX98357A amp, "
                 "TB6612FNG motor driver, VL53L0X rangefinder tilted 30 deg down for cliff "
                 "detection, SH1106 OLED, mode button with 10k pull-up, MP1584EN buck "
                 "(7.4V to 5V) and the power-net section (rail group bridge jumpers). "
                 "The TCRT5000 line-sensor array was removed on 10-19. Units: millimetres. "
                 "Planning drawing, not an electrical verification.</desc>")
    else:
        p.append("<title>面包板 Z · 轮廓 —— 双 830 点并排拼接（28 行 × 63 列）</title>")
        p.append("<desc>Board Z outline only (bodies, rails, gaps, labels, seam). "
                 "Units: millimetres. Planning drawing, not an electrical verification.</desc>")
    p.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#ffffff"/>'
             % (fmt(CANVAS_X0), fmt(CANVAS_Y0), fmt(CANVAS_W), fmt(canvas_h)))
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
    # 标题与图例（图例内容/换行/坐标全部由 legend_layout() 数据驱动，此处只贴片段）
    if include_holes:
        p.append(text(LEGEND_X, -61.84, "面包板 Z · 双 830 点并排拼接（28 × 63）",
                      3.0, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(LEGEND_X, -57.2, "X（a–j）+ Y（k–t）沿电源轨拼接 · 当前 9/9 元件已接入"
                                       "（含元件 9 电源区与 7 根轨组短跳线，明细见下方图例）",
                      1.7, COLOR["note"], "start", "doc-subtitle"))
        p.extend(legend_frags)
        p.append(vl53_side_view())
    else:
        p.append(text(LEGEND_X, -61.84, "面包板 Z · 轮廓 —— 双 830 点并排拼接（28 行 × 63 列）",
                      3.2, COLOR["title"], "start", "doc-title", bold=True))
        p.append(text(LEGEND_X, -57.2, "X（上板）+ Y（下板）沿电源轨长边拼接；本图为第一步：只画 Z 的轮廓，不含插孔。",
                      1.8, COLOR["note"], "start", "doc-subtitle"))
        p.extend(legend_frags)
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
    print("OK  元件 4 撤销：TCRT5000×5 移除（2026-10-19 B 方案——插板镜头朝上 + 0.2–15mm 量程超程）；"
          "GPIO17/18/38/39/40 释放，config.h LINE_SENSOR 宏同轮删除")
    print("OK  元件 5 VL53L0X：板外上方单排 6 针横躺落 a 行 35-40（板高 17.78 超半区行带），"
          "支架前倾 30° 防跌落 + 近距防撞（d = h/tan30° ≈ 1.7h）；"
          "SCL/SDA 走 38/39、39/40 竖带 pts 汇入 i37/i38（GPIO42/41 同列同网）；VIN → top_inner_25、"
          "GND → top_outer_23（已由元件 9 接入 3V3/GND 网）；GPIO/XSHUT 悬空（板载上拉、I2C 0x29）；"
          "本体挡板上缘 2 轨 18 孔")
    print("OK  元件 6 OLED 1.3\" SH1106：9 针单排本体放板外上方（y[-94.6,-59.0]，屏幕朝上），9 根飞线直连目标孔；"
          "Gnd→top_outer_35、CS→top_outer_38、SA0→top_outer_40、Vin→top_inner_36、3v3→top_inner_38，"
          "Rst→j47(GPIO48)、Clk→j35(GPIO1)、Data→j36(GPIO2)、DC→k45(GPIO46)；"
          "轨区一律走列间缝换道（离轨孔 1.27mm），竖带让开 TB6612 的 124.46/134.62；本体不挡孔、不占孔")
    print("OK  元件 7 模式按键：跨中隔槽插 o29(GPIO9 扩展网)/p29(GND 扩展网)，10k 上拉横跨 n29←n27(3V3 扩展网)；"
          "GPIO9 网由元件 9 的扩展跳线从 l46 搬到列 29（k46 是 ESP32 针、m/n/o46 被 MAX 本体挡死）；"
          "按键挡 0 孔（6×6 本体卡在 o/p 行间空档），电阻挡 n28 一孔")
    print("OK  元件 8 MP1584EN 降压：本体板外 x[70,92] y[76,93.5]（22.2×17.5，不占孔不挡孔）；"
          "IN+/IN− 接 7.4V 电池（与 TB6612 VM 并联），OUT+→bottom_inner_38（5V 轨）、"
          "OUT−→bottom_outer_35（GND 轨，IN−/OUT− 模块内直通）；系统供电 = 电池 → 5V → "
          "ESP32 5V 脚 k52 + MAX98357A VIN，电机 VM 直取 7.4V")
    print("OK  元件 9 电源区（无本体，16 根网线 = 9 根馈线 + 7 根轨组短跳线）："
          "X 上内轨=3V3 / X 上外轨=GND / Y 下内轨=5V / Y 下外轨=GND，"
          "每条轨 x=82.55 断开左右两段故 3V3/GND 各两根馈线；"
          "轨金属 5 孔一小组 → 7 根短跳线搭通相邻组（wire_check 查不出，net_check.py 固化为 0 处断网/短接）")


if __name__ == "__main__":
    main()
