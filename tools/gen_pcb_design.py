#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器狗 V1 · PCB 设计基线生成器（2026-10-20 v2：开发板直插）
=====================================================
把「80×70mm 承载现有模块的迁移板」的全部设计数据收在这一个文件里（单一事实源）：

  docs/source/pcb-layout-v2.svg  顶层/底层合并布局图（mm 坐标；y=0 = 板北缘/前缘）
  docs/PCB网表-v2.csv             可导入 EasyEDA / KiCad 的网表（Net,Ref,针名,针号）
  docs/source/pcb-design-baseline-v2.json  机读设计基线（BOM + 网表 + 布局坐标 + 规则）

内置校验（与面包板 wire_check 同思路——先算账，后出图）：
  A. 同层元件两两不重叠；跨层叠放记入报告（垂直高度由底盘兜底，但要在文档里声明）
  B. 全部元件在板内 + 板边净距 ≥1.5mm（连接器豁免：它们本就贴板缘）
  C. 安装孔 keepout（⌀3.4 孔 + ⌀7 净空）不压元件
  D. WROOM-1 天线端 keepout 带内无顶层元件
退出码非 0 = 布局有硬冲突，不许出图。

约定（详见 docs/PCB设计方案-v1.html）：
  - 现有模块**平躺直焊**：模块自带 2.54 排针，弯直后焊在板上等距焊盘（可选排母）
  - 2 层板：顶 = 信号 + 部分电源；底 = GND 铺铜 + 电源区辅助铜
  - JLC 免费打样约束核对：80×70 ≤ 100×100 ✓；板厚 1.6；OSP
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "docs")
SRC_DIR = os.path.join(ROOT, "docs", "source")

# ---------------------------------------------------------------- 板框与规则
BOARD = {
    "name": "ai-dog-v1",
    "w": 80.0,          # mm，东西向（x：0=西缘）
    "h": 90.0,          # mm，南北向（y：0=北缘=车头/前缘）
    "thk": 1.6,         # 板厚（JLC 免费券固定 1.6）
    "layers": 2,
    "edge_clear": 1.5,  # 板边到铜/元件包络（连接器豁免）
}
HOLES = [  # M3 安装孔：中心 + ⌀3.4 孔 + ⌀7 keepout
    (6.0, 6.0), (74.0, 6.0), (6.0, 84.0), (74.0, 84.0),
]
HOLE_D = 3.4
HOLE_KEEP = 7.0

# WROOM-1 天线端 keepout：天线端朝板北缘（前缘），该带顶层净空。
# 依据：Espressif WROOM 系列通用准则（天线端建议外探或在板内净空，P1 级，
# 精确 keepout 尺寸以官方 Hardware Design Guidelines 天线图为准，列为待确认项）。
RF_KEEPOUT = {"x0": 24.0, "x1": 56.0, "y0": 0.0, "y1": 12.0}

# ---------------------------------------------------------------- 元件布局
# kind: module(现有模块) / ic(自研贴片) / conn(连接器) / key(按键) / pass(无源)
# side: top / bottom；矩形 (x, y, w, h)；keepout 由组件本体表达（含引脚）
COMPONENTS = [
    # ---- 顶层：开发板座 + 信号/外设（开发板纵向 57.15mm，USB 端朝南=车尾）----
    {"ref": "U1", "name": "ESP32-S3 开发板座 2x22P", "kind": "module", "side": "top",
     "x": 26.03, "y": 4.0, "w": 27.94, "h": 57.15, "note": "排母可插拔, USB 端朝南(车尾)"},
    {"ref": "U6", "name": "MAX98357A 模块", "kind": "module", "side": "top",
     "x": 56.0, "y": 10.0, "w": 17.78, "h": 19.09, "note": "I2S 功放 5V"},
    {"ref": "U7", "name": "INMP441", "kind": "module", "side": "top",
     "x": 54.0, "y": 30.0, "w": 15.0, "h": 15.0, "note": "I2S 麦克风 Ø15"},
    {"ref": "K3", "name": "模式按键", "kind": "key", "side": "top",
     "x": 56.0, "y": 48.0, "w": 6.0, "h": 6.0, "note": "GPIO9 低有效"},
    {"ref": "R1/R2/R3", "name": "电池分压 100k/47k/1k", "kind": "pass", "side": "top",
     "x": 64.0, "y": 46.0, "w": 6.0, "h": 3.0, "note": "ADC_BAT@GPIO8 + C1 100nF"},
    {"ref": "C1-C6", "name": "去耦电容组", "kind": "pass", "side": "top",
     "x": 64.0, "y": 51.0, "w": 6.0, "h": 3.0, "note": "10uF/100nF 若干"},
    {"ref": "LED1/R10", "name": "电源指示灯", "kind": "pass", "side": "top",
     "x": 56.0, "y": 57.0, "w": 4.0, "h": 4.0, "note": "3V3->1k->LED->GND"},
    {"ref": "Q1", "name": "AO3401 PMOS", "kind": "ic", "side": "top",
     "x": 64.0, "y": 57.0, "w": 3.0, "h": 2.5, "note": "电池反接保护(可选)"},
    {"ref": "U9", "name": "VL53L0X 4P 排针", "kind": "conn", "side": "chassis",
     "x": 77.5, "y": 26.0, "w": 2.5, "h": 7.62, "note": "壳上支架前倾30°"},
    {"ref": "U8", "name": "OLED 9P 排针", "kind": "conn", "side": "chassis",
     "x": 77.5, "y": 48.0, "w": 2.5, "h": 23.3, "note": "壳上屏幕朝上"},
    {"ref": "SW1", "name": "充电开关", "kind": "key", "side": "top",
     "x": 71.0, "y": 35.0, "w": 9.0, "h": 5.0, "edge_ok": True,
     "note": "拨到充电位才给 FS5080E 供电（东缘侧拨）"},
    {"ref": "J6", "name": "XH2.54-2P 喇叭", "kind": "conn", "side": "top",
     "x": 75.0, "y": 41.0, "w": 5.0, "h": 5.0, "note": "4Ω3W 喇叭 东缘"},
    {"ref": "TP1-TP6", "name": "测试点", "kind": "pass", "side": "top",
     "x": 12.0, "y": 4.0, "w": 8.0, "h": 5.0, "note": "TX/RX/3V3/5V/GND/ADC"},
    # ---- 底层：电源（插座针排在 x≈26/54，底层器件必须避开这两列或落在 y>61.15）----
    {"ref": "U5", "name": "MP1584EN 模块", "kind": "module", "side": "bottom",
     "x": 28.0, "y": 20.0, "w": 22.2, "h": 17.5, "note": "7.4V->5V 3A(插座内侧空档)"},
    {"ref": "D1", "name": "SS34 肖特基", "kind": "ic", "side": "bottom",
     "x": 28.0, "y": 39.0, "w": 4.5, "h": 2.5, "note": "防 USB 5V 与电池 5V 互灌"},
    {"ref": "U3", "name": "FS5080E 2S充电", "kind": "ic", "side": "bottom",
     "x": 30.0, "y": 42.0, "w": 12.0, "h": 12.0, "note": "ESOP8+L1 4.7uH, 5V->8.4V/1A"},
    {"ref": "U4", "name": "TB6612FNG 模块", "kind": "module", "side": "bottom",
     "x": 10.0, "y": 63.0, "w": 34.0, "h": 24.0, "note": "双电机 1.2A/路, VM=7.4V"},
    {"ref": "J3", "name": "XT30 母座", "kind": "conn", "side": "bottom",
     "x": 0.0, "y": 62.0, "w": 8.0, "h": 10.5, "note": "电池 7.4V 输入 西缘"},
    {"ref": "J4", "name": "XH2.54-2P M1", "kind": "conn", "side": "bottom",
     "x": 50.0, "y": 80.0, "w": 5.0, "h": 5.0, "note": "左轮电机 南缘"},
    {"ref": "J5", "name": "XH2.54-2P M2", "kind": "conn", "side": "bottom",
     "x": 58.0, "y": 80.0, "w": 5.0, "h": 5.0, "note": "右轮电机 南缘"},
]
# 顶层功能区底色（x0,y0,x1,y1,name,color）
ZONES = [
    (54.0, 4.0, 80.0, 47.0, "射频/音频", "#eff6ff"),
    (26.0, 61.15, 54.0, 72.0, "USB 插线净空", "#fee2e2"),
    (0.0, 72.0, 80.0, 90.0, "电源区(底层)", "#fef3c7"),
    (54.0, 47.0, 80.0, 62.0, "人机区", "#f0fdf4"),
]
COLORS = {
    "module": {"fill": "#dbeafe", "stroke": "#2563eb", "text": "#1e3a8a"},
    "ic": {"fill": "#dcfce7", "stroke": "#16a34a", "text": "#14532d"},
    "conn": {"fill": "#1e293b", "stroke": "#0f172a", "text": "#f8fafc"},
    "key": {"fill": "#e2e8f0", "stroke": "#475569", "text": "#0f172a"},
    "pass": {"fill": "#f1f5f9", "stroke": "#64748b", "text": "#334155"},
}

# ---------------------------------------------------------------- 网表
# 每行 = (网名, 器件位号, 针名)。针号按实物丝印/数据手册导入 EDA 时再对。
# 3V3/5V/GND = 电源网；GPIOxxx = ESP32 功能名（与 config.h 一致）。
NETS = [
    # ---- 3V3：来自开发板板载 LDO（3V3 脚），带小电流传感器 ----
    ("3V3", "U1", "3V3"), ("3V3", "U7", "VDD"), ("3V3", "U4", "VCC"),
    ("3V3", "U8", "Vin"), ("3V3", "U8", "3v3"), ("3V3", "U9", "VIN"),
    ("3V3", "R6", "R6-1"), ("3V3", "R10", "R10-1"), ("3V3", "TP3", "TP3"),
    # ---- 5V：MP1584EN 输出经 SS34 -> 开发板 5V 脚 / 功放 / 充电开关 ----
    ("5V_BUCK", "U5", "OUT+"), ("5V_BUCK", "D1", "A"),
    ("5V", "D1", "K"), ("5V", "U1", "5V"), ("5V", "U6", "VIN"), ("5V", "U6", "SD_MODE"),
    ("5V", "SW1", "1"), ("5V", "TP6", "TP6"),
    ("CHG_IN", "SW1", "2"), ("CHG_IN", "U3", "IN"),
    # ---- 7V4：电池正（经 Q1 反接保护）-> 电机 VM + 降压输入（电机不改 5V）----
    ("7V4", "U4", "VM"), ("7V4", "U5", "IN+"), ("7V4", "Q1", "D"), ("7V4", "R1", "R1-1"),
    ("BAT+", "J3", "1"), ("BAT+", "Q1", "S"),
    # ---- GND ----
    ("GND", "U1", "GND"), ("GND", "U5", "IN-"), ("GND", "U5", "OUT-"),
    ("GND", "U4", "GND"), ("GND", "U3", "GND"),
    ("GND", "U6", "GND"), ("GND", "U6", "GAIN"),
    ("GND", "U7", "GND"), ("GND", "U7", "L/R"),
    ("GND", "U8", "Gnd"), ("GND", "U8", "CS"), ("GND", "U8", "SA0"),
    ("GND", "U9", "GND"), ("GND", "K3", "b"),
    ("GND", "R2", "R2-2"), ("GND", "C1", "C1-2"), ("GND", "TP4", "TP4"),
    ("GND", "LED1", "LED1-K"),
    # ---- 充电回路（FS5080E：5V -> 8.4V；只有 SW1 拨到充电位才通电）----
    ("FS_SW", "U3", "SW"), ("FS_SW", "L1", "L1-1"),
    ("8V4", "U3", "BAT"), ("8V4", "L1", "L1-2"), ("8V4", "J3", "2"),
    # ---- 电池监测分压 ----
    ("ADC_BAT", "R1", "R1-2"), ("ADC_BAT", "R3", "R3-1"),
    ("ADC_BAT", "R2", "R2-1"), ("ADC_BAT", "C1", "C1-1"),
    ("ADC_BAT", "U1", "GPIO8"), ("ADC_BAT", "TP5", "TP5"),
    # ---- 模式按键 GPIO9（低有效，10k 上拉 3V3）----
    ("GPIO9", "U1", "GPIO9"), ("GPIO9", "K3", "a"), ("GPIO9", "R6", "R6-2"),
    # ---- I2S 麦克风 ----
    ("GPIO4_SCK", "U1", "GPIO4"), ("GPIO4_SCK", "U7", "SCK"),
    ("GPIO5_WS", "U1", "GPIO5"), ("GPIO5_WS", "U7", "WS"),
    ("GPIO6_SD", "U1", "GPIO6"), ("GPIO6_SD", "U7", "SD"),
    # ---- I2S 功放 ----
    ("GPIO7_DIN", "U1", "GPIO7"), ("GPIO7_DIN", "U6", "DIN"),
    ("GPIO15_BCLK", "U1", "GPIO15"), ("GPIO15_BCLK", "U6", "BCLK"),
    ("GPIO16_LRC", "U1", "GPIO16"), ("GPIO16_LRC", "U6", "LRC"),
    ("SPK+", "U6", "VO+"), ("SPK+", "J6", "1"),
    ("SPK-", "U6", "VO-"), ("SPK-", "J6", "2"),
    # ---- 电机 TB6612FNG ----
    ("GPIO10", "U1", "GPIO10"), ("GPIO10", "U4", "STBY"),
    ("GPIO11", "U1", "GPIO11"), ("GPIO11", "U4", "PWMA"),
    ("GPIO12", "U1", "GPIO12"), ("GPIO12", "U4", "AIN1"),
    ("GPIO13", "U1", "GPIO13"), ("GPIO13", "U4", "AIN2"),
    ("GPIO14", "U1", "GPIO14"), ("GPIO14", "U4", "PWMB"),
    ("GPIO47", "U1", "GPIO47"), ("GPIO47", "U4", "BIN1"),
    ("GPIO21", "U1", "GPIO21"), ("GPIO21", "U4", "BIN2"),
    ("MOTO_A", "U4", "AO1"), ("MOTO_A", "U4", "AO2"), ("MOTO_A", "J4", "1"), ("MOTO_A", "J4", "2"),
    ("MOTO_B", "U4", "BO1"), ("MOTO_B", "U4", "BO2"), ("MOTO_B", "J5", "1"), ("MOTO_B", "J5", "2"),
    # ---- I2C VL53L0X ----
    ("GPIO42_SCL", "U1", "GPIO42"), ("GPIO42_SCL", "U9", "SCL"),
    ("GPIO41_SDA", "U1", "GPIO41"), ("GPIO41_SDA", "U9", "SDA"),
    # ---- OLED SPI ----
    ("GPIO48_RES", "U1", "GPIO48"), ("GPIO48_RES", "U8", "Rst"),
    ("GPIO1_SCLK", "U1", "GPIO1"), ("GPIO1_SCLK", "U8", "Clk"),
    ("GPIO2_MOSI", "U1", "GPIO2"), ("GPIO2_MOSI", "U8", "Data"),
    ("GPIO46_DC", "U1", "GPIO46"), ("GPIO46_DC", "U8", "DC"),
    # ---- 调试/指示 ----
    ("GPIO43_TX", "U1", "GPIO43"), ("GPIO43_TX", "TP1", "TP1"),
    ("GPIO44_RX", "U1", "GPIO44"), ("GPIO44_RX", "TP2", "TP2"),
    ("LED1", "R10", "R10-2"), ("LED1", "LED1", "LED1-A"),
]
# BOM：自研部分（现有模块不重复列，见 HTML 文档）
BOM_SELF = [
    ("U1", "2x22P 2.54 排母座", "THT 44P", "2", "开发板可插拔；本体不焊死，坏了一拔就换"),
    ("U3", "FS5080E", "ESOP-8", "1", "2S 升压充电 5V->8.4V，1A（Iset 待查表）"),
    ("L1", "4.7uH 功率电感", "10x10 CD43", "1", "FS5080E 配套"),
    ("D1", "SS34 肖特基", "SMA/DO-214AC", "1", "5V 隔离：电池与 USB 互不倒灌（压降 0.3V）"),
    ("SW1", "拨动开关 2 档", "THT 侧拨", "1", "充电位/运行位；不拨就电池自充（见风险表）"),
    ("R1", "100kΩ", "0603", "1", "电池分压上臂"),
    ("R2", "47kΩ", "0603", "1", "电池分压下臂"),
    ("R3", "1kΩ", "0603", "1", "ADC 串保护"),
    ("R4", "100kΩ", "0603", "1", "Q1 栅极电阻"),
    ("R6", "10kΩ", "0603", "1", "GPIO9 上拉"),
    ("R10", "1kΩ", "0603", "1", "LED1 限流"),
    ("C1", "100nF", "0603", "1", "分压滤波"),
    ("C3/C4", "10uF+100nF", "0603", "2", "3V3 去耦（贴开发板 3V3 脚）"),
    ("C5/C6", "22uF", "0805", "2", "FS5080E 输入/输出"),
    ("Q1", "AO3401A PMOS", "SOT-23", "1", "电池反接保护（可不装，BAT+ 与 7V4 直连）"),
    ("K3", "轻触按键 6x6", "THT", "1", "模式键 GPIO9（低有效）"),
    ("J3", "XT30 母座 2P", "THT", "1", "电池输入 7.4V（注意极性丝印）"),
    ("J4/J5", "XH2.54-2P 直针", "THT", "2", "电机 M1/M2"),
    ("J6", "XH2.54-2P 直针", "THT", "1", "喇叭"),
    ("U8", "2.54 直针 9P", "THT", "1", "OLED 排线（屏上壳）"),
    ("U9", "2.54 直针 4P", "THT", "1", "VL53L0X 排线（支架前倾30°）"),
    ("TP1-TP6", "测试点 Ø1.5 焊盘", "-", "6", "TX/RX/3V3/5V/GND/ADC_BAT"),
    ("LED1", "LED 绿", "0805", "1", "3V3 电源指示"),
]
MODULES_EXISTING = [
    ("DEV", "ESP32-S3 N16R8 开发板", "1", "插 2x22P 排母，USB 端朝南(车尾)"),
    ("M-INMP441", "INMP441 麦克风", "7", "Ø15 2x3 针，平躺直焊 6P"),
    ("M-MAX98357A", "MAX98357A 功放", "6", "7P，平躺直焊"),
    ("M-TB6612", "TB6612FNG 模块", "3", "10P(信号+VM)，平躺直焊"),
    ("M-MP1584", "MP1584EN 模块", "5", "4P(IN+/IN-/OUT+/OUT-)"),
    ("M-VL53", "VL53L0X 模块", "9", "壳上前倾30°，4P 排线"),
    ("M-OLED", "OLED 1.3寸 SH1106", "8", "壳上屏幕朝上，9P 排线"),
    ("BAT", "18650 x2 串联", "-", "机架内，XT30 出线"),
    ("SPK", "4Ω 3W 喇叭", "-", "J6 接线"),
    ("MOTOR", "N20 电机 x2", "-", "J4/J5 接线"),
]


# ---------------------------------------------------------------- 校验
def rect_of(c):
    return (c["x"], c["y"], c["w"], c["h"])


def overlap(a, b, margin=0.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    if margin == 0.0:
        return ox > 1e-6 and oy > 1e-6, ox, oy
    return ox > margin and oy > margin, ox, oy


def run_checks():
    """返回 (问题数, 报告行列表)。同层重叠=硬冲突；跨层叠放=报告不拦截。"""
    problems, report = 0, []
    comps = COMPONENTS
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            hit, ox, oy = overlap(rect_of(a), rect_of(b))
            if not hit:
                continue
            if a["side"] == b["side"]:
                problems += 1
                report.append("!! 同层重叠 %s(%s) x %s(%s): %.1f x %.1f mm"
                              % (a["ref"], a["side"], b["ref"], b["side"], ox, oy))
            else:
                report.append("~~ 跨层叠放(不拦截,底盘高度兜底) %s(%s) x %s(%s): %.1f x %.1f mm"
                              % (a["ref"], a["side"], b["ref"], b["side"], ox, oy))
    for c in comps:
        x, y, w, h = rect_of(c)
        if c["kind"] == "conn" or c.get("edge_ok"):
            if x < -0.01 or y < -0.01 or x + w > BOARD["w"] + 0.01 or y + h > BOARD["h"] + 0.01:
                problems += 1
                report.append("!! 连接器越板 %s" % c["ref"])
            continue
        if (x < BOARD["edge_clear"] - 0.01 or y < BOARD["edge_clear"] - 0.01
                or x + w > BOARD["w"] - BOARD["edge_clear"] + 0.01
                or y + h > BOARD["h"] - BOARD["edge_clear"] + 0.01):
            problems += 1
            report.append("!! 板边净距不足(%s 距缘 %.1f/%.1f/%.1f/%.1f)"
                          % (c["ref"], x, y, BOARD["w"] - x - w, BOARD["h"] - y - h))
        for (hx, hy) in HOLES:
            hit, ox, oy = overlap(rect_of(c), (hx - HOLE_KEEP / 2, hy - HOLE_KEEP / 2,
                                               HOLE_KEEP, HOLE_KEEP))
            if hit:
                problems += 1
                report.append("!! %s 压安装孔 keepout (%s)" % (c["ref"], (hx, hy)))
    k = RF_KEEPOUT
    for c in comps:
        if c["side"] != "top" or c["ref"] == "U1":
            continue  # U1 自身即带天线模组：其天线端落在净空带内，带内禁止的是其他元件/铜
        hit, ox, oy = overlap(rect_of(c), (k["x0"], k["y0"], k["x1"] - k["x0"], k["y1"] - k["y0"]))
        if hit:
            problems += 1
            report.append("!! %s 侵入 WROOM 天线 keepout 带" % c["ref"])
    used = set(n for n, _r, _p in NETS)
    report.append("-- 网络数 %d；元件数 %d；BOM 自研项 %d；保留模块 %d"
                  % (len(used), len(comps), len(BOM_SELF), len(MODULES_EXISTING)))
    return problems, report


# ---------------------------------------------------------------- SVG
def fmt(v):
    s = ("%.2f" % float(v)).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def t(x, y, s, size, fill, anchor="start", bold=False, rotate=None):
    tr = ' transform="rotate(%s %s %s)"' % (rotate, fmt(x), fmt(y)) if rotate else ""
    b = ' font-weight="bold"' if bold else ""
    return ('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s"%s%s font-family="system-ui,sans-serif">%s</text>'
            % (fmt(x), fmt(y), fmt(size), fill, anchor, b, tr, esc(s)))


def draw_svg():
    S = []
    W, H = BOARD["w"], BOARD["h"]
    pad_l, pad_t = 6.0, 10.0
    vw, vh = W + 24.0, H + 34.0
    S.append('<?xml version="1.0" encoding="UTF-8"?>')
    S.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%smm" height="%smm">' % (fmt(vw), fmt(vh), fmt(vw), fmt(vh)))
    S.append('<rect x="0" y="0" width="%s" height="%s" fill="#ffffff"/>' % (fmt(vw), fmt(vh)))
    S.append('<g transform="translate(%s %s)">' % (fmt(pad_l), fmt(pad_t)))
    # 标题
    S.append(t(0, -5.5, "机器狗 V1 · PCB 顶层布局  v2（开发板直插）", 3.2, "#0f172a", bold=True))
    S.append(t(W, -5.5, "80×70mm · 2层 · y=0 为板北缘(车头/前缘)", 2.2, "#475569", anchor="end"))
    # 功能区底色
    for (x0, y0, x1, y1, name, color) in ZONES:
        S.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="#cbd5e1" stroke-width="0.1"/>' % (fmt(x0), fmt(y0), fmt(x1 - x0), fmt(y1 - y0), color))
        S.append(t(x0 + 0.8, y0 + 2.2, name, 1.6, "#94a3b8"))
    # 天线 keepout 带
    k = RF_KEEPOUT
    S.append('<rect x="%s" y="%s" width="%s" height="%s" fill="none" stroke="#dc2626" stroke-width="0.35" stroke-dasharray="1.4 1"/>' % (fmt(k["x0"]), fmt(k["y0"]), fmt(k["x1"] - k["x0"]), fmt(k["y1"] - k["y0"])))
    S.append(t(k["x0"] + 1, k["y1"] - 0.8, "WROOM 天线 keepout（顶层净空）", 1.5, "#dc2626"))
    # 板体
    S.append('<rect x="0" y="0" width="%s" height="%s" fill="none" stroke="#0f172a" stroke-width="0.5"/>' % (fmt(W), fmt(H)))
    # 安装孔
    for (hx, hy) in HOLES:
        S.append('<circle cx="%s" cy="%s" r="%s" fill="#ffffff" stroke="#0f172a" stroke-width="0.3"/>' % (fmt(hx), fmt(hy), fmt(HOLE_D / 2)))
        S.append('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="#dc2626" stroke-width="0.12" stroke-dasharray="0.8 0.6"/>' % (fmt(hx), fmt(hy), fmt(HOLE_KEEP / 2)))
    # 尺寸标注
    S.append(t(W / 2, H + 3.4, "80.00", 2.2, "#64748b", anchor="middle"))
    S.append('<line x1="0" y1="%s" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/><line x1="0" y1="%s" x2="0" y2="%s" stroke="#64748b" stroke-width="0.15"/><line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/>'
             % (fmt(H + 2.6), fmt(W), fmt(H + 2.6), fmt(H + 1.6), fmt(H + 3.6), fmt(W), fmt(H + 1.6), fmt(W), fmt(H + 3.6)))
    S.append(t(W + 3.4, H / 2, "70.00", 2.2, "#64748b", rotate=-90, anchor="middle"))
    S.append('<line x1="%s" y1="0" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/><line x1="%s" y1="0" x2="%s" y2="0" stroke="#64748b" stroke-width="0.15"/><line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/>'
             % (fmt(W + 2.6), fmt(W + 2.6), fmt(H), fmt(W + 1.6), fmt(W + 3.6), fmt(W + 1.6), fmt(H), fmt(W + 3.6), fmt(H)))

    # 元件：本体 → 标签 → 针脚 → 流向箭头
    for side in ("bottom", "top", "chassis"):
        for c in COMPONENTS:
            if c["side"] != side:
                continue
            col = COLORS[c["kind"]]
            x, y, w, h = rect_of(c)
            dash = ' stroke-dasharray="1.2 0.8"' if side == "bottom" else ""
            lw = 0.45 if side != "bottom" else 0.35
            if side == "bottom":
                # 渲染器不支持 fill-opacity：底层专用填充预混 70% 白，直接给实色
                f = col["fill"].lstrip("#")
                fr, fg, fb = (int(f[0:2], 16), int(f[2:4], 16), int(f[4:6], 16))
                fill = "#%02x%02x%02x" % (fr + (255 - fr) * 7 // 10, fg + (255 - fg) * 7 // 10, fb + (255 - fb) * 7 // 10)
            else:
                fill = col["fill"]
            S.append('<rect x="%s" y="%s" width="%s" height="%s" rx="0.4" fill="%s" stroke="%s" stroke-width="%s"%s/>'
                     % (fmt(x), fmt(y), fmt(w), fmt(h), fill, col["stroke"], fmt(lw), dash))
            # 针焊盘：模块平躺直焊的 2.54 焊盘沿南缘画一排小圆
            if c["kind"] == "module" and c["side"] == "top" and c.get("pads"):
                n = 0
                try:
                    n = int(c["pads"].split("x")[0])
                except Exception:
                    n = 0
                if n:
                    for i in range(n):
                        S.append('<circle cx="%s" cy="%s" r="0.35" fill="#ffffff" stroke="%s" stroke-width="0.12"/>'
                                 % (fmt(x + 2.0 + i * 2.54), fmt(y + h + 1.27), col["stroke"]))
            # 标签
            if c["ref"] not in ("U8", "U9"):
                S.append(t(x + 0.8, y + 2.2, "%s %s" % (c["ref"], c["name"]), 1.9, col["text"], bold=True))
                if c.get("note"):
                    S.append(t(x + 0.8, y + h - 0.6, c["note"], 1.35, col["text"]))
            else:
                S.append(t(x - 1.0, y + h / 2, "%s→%s" % (c["ref"], c["name"].split(" ")[0]), 1.5,
                           col["text"], anchor="end", rotate=-90))
    # 底层器件再补一层虚线轮廓（填充已被顶层盖住时仍可见）
    for c in COMPONENTS:
        if c["side"] != "bottom":
            continue
        col = COLORS[c["kind"]]
        x, y, w, h = rect_of(c)
        S.append('<rect x="%s" y="%s" width="%s" height="%s" rx="0.4" fill="none" stroke="%s" stroke-width="0.35" stroke-dasharray="1.2 0.8"/>'
                 % (fmt(x), fmt(y), fmt(w), fmt(h), col["stroke"]))
    # 壳上器件的引出箭头
    for ref, x1, y1 in (("U8", 74.0, 30.0), ("U9", 73.0, 17.0)):
        S.append('<line x1="%s" y1="%s" x2="80.5" y2="%s" stroke="#0891b2" stroke-width="0.4" stroke-dasharray="1.5 1"/>' % (fmt(x1), fmt(y1), fmt(y1)))
    S.append(t(81.5, 30.0, "OLED 9P 排线", 1.5, "#0891b2"))
    S.append(t(81.5, 17.0, "VL53 4P 排线(前倾30°支架)", 1.5, "#0891b2"))
    S.append('</g>')

    # 图例（板体下方，两列）
    ly = H + 12.0
    S.append(t(0, ly, "图例", 2.4, "#0f172a", bold=True))
    ly += 3.0
    items = [
        ("module", "现有模块（平躺直焊 2.54 焊盘）"),
        ("ic", "自研 IC / 分立件"),
        ("conn", "连接器（贴板缘）"),
        ("key", "轻触按键"),
        ("pass", "无源/测试点区域"),
    ]
    for i, (kind, label) in enumerate(items):
        col = COLORS[kind]
        yy = ly + i * 3.2
        S.append('<rect x="0" y="%s" width="3.0" height="2.0" rx="0.4" fill="%s" fill-opacity="0.9" stroke="%s" stroke-width="0.4"/>' % (fmt(yy - 1.6), col["stroke"], col["stroke"]))
        S.append(t(4.0, yy, label, 1.9, "#334155"))
    notes = [
        "虚线框 = 底层元件；实线框 = 顶层元件；跨层叠放由底盘高度兜底（见文档 §5）。",
        "底层 = GND 铺铜 + 电源区辅助铜；顶层 = 信号 + 局部电源。电源主干：7V4≥2.0mm / 5V≥1.0mm / 3V3≥0.6mm。",
        "U8/U9 为壳上器件（OLED 屏幕朝上、VL53 前倾30°），经排线连板，不占板面。",
        "针脚圆 = 平躺直焊焊盘（2.54 等距）。测试点 TP1-TP6：TX/RX/3V3/5V/GND/ADC_BAT。",
    ]
    ny = ly
    for i, s in enumerate(notes):
        S.append(t(52.0, ly + i * 3.2, s, 1.7, "#475569"))
    S.append(t(0, ny + 17.5, "docs/PCB设计方案-v2.html · 本图由 tools/gen_pcb_design.py 生成 · 2026-10-20", 1.6, "#94a3b8"))
    S.append('</svg>')
    return "\n".join(S)


# ---------------------------------------------------------------- 输出
def write_csv(path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Net", "Ref", "PinName", "PinNo", "Note"])
        for net, ref, pin in sorted(NETS):
            w.writerow([net, ref, pin, "", "针号导入 EDA 时按实物丝印/数据手册对齐"])
    return len(NETS)


def write_json(path):
    data = {
        "version": "v2",
        "date": "2026-10-20",
        "board": BOARD,
        "holes": HOLES,
        "rf_keepout": RF_KEEPOUT,
        "components": COMPONENTS,
        "nets": [{"net": n, "ref": r, "pin": p} for n, r, p in NETS],
        "bom_self": [{"ref": r, "name": n, "footprint": fp, "qty": q, "note": note}
                     for r, n, fp, q, note in BOM_SELF],
        "modules": [{"family": fam, "name": n, "qty": q, "mount": m}
                    for fam, n, q, m in MODULES_EXISTING],
        "rules": {
            "layers": 2, "trace_signal": 0.2, "trace_power_min": 0.6,
            "trace_7v4": 2.0, "trace_5v": 1.0, "trace_3v3": 0.6,
            "clearance": 0.2, "via": "0.60/0.30",
            "edge_clear": 1.5, "pour_clear": 0.25,
            "silk_to_pad": 0.15,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def main():
    problems, report = run_checks()
    print("== 布局校验 ==")
    for line in report:
        print(line)
    print("硬冲突 %d 处" % problems)
    if problems:
        sys.exit(1)
    if not os.path.isdir(SRC_DIR):
        os.makedirs(SRC_DIR)
    svg = draw_svg()
    with open(os.path.join(SRC_DIR, "pcb-layout-v2.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
    n = write_csv(os.path.join(OUT_DIR, "PCB网表-v2.csv"))
    data = write_json(os.path.join(SRC_DIR, "pcb-design-baseline-v2.json"))
    print("已生成 docs/source/pcb-layout-v2.svg（%d 字节）" % len(svg))
    print("已生成 docs/PCB网表-v2.csv（%d 条 pin-net）" % n)
    print("已生成 docs/source/pcb-design-baseline-v2.json（%d 个网络）" % len(set(x["net"] for x in data["nets"])))


if __name__ == "__main__":
    main()
