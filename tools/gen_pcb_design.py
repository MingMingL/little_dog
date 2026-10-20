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
    (54.0, 47.0, 80.0, 62.0, "预留/指示", "#f0fdf4"),
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
    ("3V3", "R10", "R10-1"), ("3V3", "TP3", "TP3"),
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
    ("GND", "U9", "GND"),
    ("GND", "R2", "R2-2"), ("GND", "C1", "C1-2"), ("GND", "TP4", "TP4"),
    ("GND", "LED1", "LED1-K"),
    # ---- 充电回路（FS5080E：5V -> 8.4V；只有 SW1 拨到充电位才通电）----
    ("FS_SW", "U3", "SW"), ("FS_SW", "L1", "L1-1"),
    ("8V4", "U3", "BAT"), ("8V4", "L1", "L1-2"), ("8V4", "J3", "2"),
    # ---- 电池监测分压 ----
    ("ADC_BAT", "R1", "R1-2"), ("ADC_BAT", "R3", "R3-1"),
    ("ADC_BAT", "R2", "R2-1"), ("ADC_BAT", "C1", "C1-1"),
    ("ADC_BAT", "U1", "GPIO8"), ("ADC_BAT", "TP5", "TP5"),
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
    ("R10", "1kΩ", "0603", "1", "LED1 限流"),
    ("C1", "100nF", "0603", "1", "分压滤波"),
    ("C3/C4", "10uF+100nF", "0603", "2", "3V3 去耦（贴开发板 3V3 脚）"),
    ("C5/C6", "22uF", "0805", "2", "FS5080E 输入/输出"),
    ("Q1", "AO3401A PMOS", "SOT-23", "1", "电池反接保护（可不装，BAT+ 与 7V4 直连）"),
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


# ---------------------------------------------------------------- 焊盘与高度
# 焊盘：相对元件左上角 (dx, dy, 针名)，2.54 栅格。用于摆件/焊接对位，不是真实封装。
J1_LABELS = ["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17", "18",
             "8", "3", "46", "9", "10", "11", "12", "13", "14", "5V", "GND"]
J3_LABELS = ["GND", "TX", "RX", "1", "2", "42", "41", "40", "39", "38", "37",
             "36", "35", "0", "45", "48", "47", "21", "20", "19", "GND", "GND"]


def _row(n, dx, dy, labels, step=2.54, vertical=False):
    out = []
    for i in range(n):
        lab = labels[i] if i < len(labels) else ""
        out.append((dx + (0 if vertical else i * step), dy + (i * step if vertical else 0), lab))
    return out


PADS = {
    # 开发板：两排 22 针（J1 = k 行 / J3 = j 行），针距 2.54，距板身左右各 1.27
    "U1": (_row(22, 1.27, 2.0, J1_LABELS) + _row(22, 26.67, 2.0, J3_LABELS)),
    # 模块：平躺直焊，焊盘排在本体南缘外 1.27（顶面）/ 北缘（底面）
    "U6": _row(7, 1.27, 19.09 + 1.27, ["LRC", "BCLK", "DIN", "GAIN", "SD", "GND", "VIN"]),
    "U7": _row(6, 1.27, 15.0 + 1.27, ["SCK", "WS", "L/R", "SD", "VDD", "GND"]),
    "U4": _row(10, 1.27, 24.0 + 1.27, ["STBY", "PWMA", "AIN1", "AIN2", "PWMB",
                                       "VCC", "BIN1", "BIN2", "GND", "VM"]),
    "U5": _row(4, 1.27, 17.5 + 1.27, ["OUT+", "OUT-", "IN+", "IN-"]),
    # 连接器
    "J3": _row(2, 1.5, 10.5 + 1.0, ["1", "2"]),
    "J4": _row(2, 1.5, 5.0 + 1.0, ["1", "2"]),
    "J5": _row(2, 1.5, 5.0 + 1.0, ["1", "2"]),
    "J6": _row(2, 1.5, 5.0 + 1.0, ["1", "2"]),
    "SW1": _row(3, 1.5, 5.0 + 1.0, ["1", "2", "3"]),
    "U8": _row(9, -1.27, 1.27, ["Gnd", "Vin", "3v3", "CS", "Rst", "SA0", "Clk", "Data", "DC"], vertical=True),
    "U9": _row(4, -1.27, 1.27, ["VIN", "GND", "SCL", "SDA"], vertical=True),
}

# 高度预算（mm，板面以上 / 板面以下；ESTIMATE：模块按常见成品尺寸取整，实测为准）
HEIGHTS = {
    "U1": (10.0, 0.0),    # 排母 8.5 + 开发板 1.6（含排针）
    "U6": (12.0, 0.0), "U7": (3.0, 0.0), "U8": (8.5, 0.0), "U9": (8.5, 0.0),
    "J6": (7.0, 0.0), "SW1": (5.0, 0.0),
    "U5": (0.0, 12.0), "U4": (0.0, 12.0), "U3": (0.0, 3.5), "D1": (0.0, 2.5),
    "J3": (0.0, 10.0), "J4": (0.0, 7.0), "J5": (0.0, 7.0),
}

# 网表针号：开发板按 J1-/J3- 列号（与面包板 j/k 行列一致）；模块/连接器按丝印针名
U1_PINNO = {
    "3V3": "J1-32", "5V": "J1-52", "GND": "J1-53",
    "GPIO4": "J1-35", "GPIO5": "J1-36", "GPIO6": "J1-37", "GPIO7": "J1-38",
    "GPIO15": "J1-39", "GPIO16": "J1-40", "GPIO8": "J1-43", "GPIO46": "J1-45",
    "GPIO9": "J1-46", "GPIO10": "J1-47", "GPIO11": "J1-48", "GPIO12": "J1-49",
    "GPIO13": "J1-50", "GPIO14": "J1-51",
    "GPIO1": "J3-35", "GPIO2": "J3-36", "GPIO42": "J3-37", "GPIO41": "J3-38",
    "GPIO48": "J3-47", "GPIO47": "J3-48", "GPIO21": "J3-49",
    "GPIO43": "J3-33", "GPIO44": "J3-34", "GPIO0": "J3-45",
}
PINNO = {"U1": U1_PINNO}
for _ref, _pads in PADS.items():
    if _ref == "U1":
        continue
    PINNO[_ref] = {}
    for _i, (_dx, _dy, _lab) in enumerate(_pads):
        if _lab:
            PINNO[_ref][_lab] = str(_i + 1)

# 针名别名：模块丝印与网表名不一致、或焊盘未单独画出的引脚
ALIAS = {
    ("U6", "SD_MODE"): "5", ("U6", "VO+"): "8", ("U6", "VO-"): "9",
    ("U4", "AO1"): "11", ("U4", "AO2"): "12",
    ("U4", "BO1"): "13", ("U4", "BO2"): "14",
    ("U3", "IN"): "IN", ("U3", "BAT"): "BAT", ("U3", "SW"): "SW", ("U3", "GND"): "GND",
}

def pin_no(ref, pin):
    """网表针号：先查显式表，再按器件类型推断（无源件 1/2、二极管 A/K、测试点 1）。"""
    if (ref, pin) in ALIAS:
        return ALIAS[(ref, pin)]
    m = PINNO.get(ref, {})
    if pin in m:
        return m[pin]
    if ref[:1] in "RCL" and pin.endswith(("-1", "-2")):
        return pin[-1]
    if ref.startswith("TP"):
        return "1"
    if ref in ("D1", "LED1", "Q1"):
        return pin
    return ""


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
    vw, vh = W + 26.0, H + 56.0
    S.append('<?xml version="1.0" encoding="UTF-8"?>')
    S.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%smm" height="%smm">' % (fmt(vw), fmt(vh), fmt(vw), fmt(vh)))
    S.append('<rect x="0" y="0" width="%s" height="%s" fill="#ffffff"/>' % (fmt(vw), fmt(vh)))
    S.append('<g transform="translate(%s %s)">' % (fmt(pad_l), fmt(pad_t)))
    # 标题
    S.append(t(0, -5.5, "机器狗 V1 · PCB 顶层布局  v2.2（开发板直插 · 焊盘级）", 3.2, "#0f172a", bold=True))
    S.append(t(W, -5.5, "%dx%dmm · 2层 · y=0 为板北缘(车头/前缘)" % (W, H), 2.2, "#475569", anchor="end"))
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
    S.append(t(W / 2, H + 3.4, "%.2f" % W, 2.2, "#64748b", anchor="middle"))
    S.append('<line x1="0" y1="%s" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/><line x1="0" y1="%s" x2="0" y2="%s" stroke="#64748b" stroke-width="0.15"/><line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#64748b" stroke-width="0.15"/>'
             % (fmt(H + 2.6), fmt(W), fmt(H + 2.6), fmt(H + 1.6), fmt(H + 3.6), fmt(W), fmt(H + 1.6), fmt(W), fmt(H + 3.6)))
    S.append(t(W + 3.4, H / 2, "%.2f" % H, 2.2, "#64748b", rotate=-90, anchor="middle"))
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
            # 焊盘（数据驱动）：小圆 + 针名 + 1 脚方标
            for i, (dx, dy, lab) in enumerate(PADS.get(c["ref"], [])):
                px, py = x + dx, y + dy
                if i == 0:      # 1 脚：方焊盘
                    S.append('<rect x="%s" y="%s" width="1.3" height="1.3" fill="#ffffff" stroke="%s" stroke-width="0.15"/>'
                             % (fmt(px - 0.65), fmt(py - 0.65), col["stroke"]))
                else:
                    S.append('<circle cx="%s" cy="%s" r="0.55" fill="#ffffff" stroke="%s" stroke-width="0.12"/>'
                             % (fmt(px), fmt(py), col["stroke"]))
                if lab and c["ref"] in ("U1", "U6", "U7", "U4", "U5", "U8", "U9"):
                    if c["ref"] in ("U8", "U9"):
                        S.append(t(px - 1.0, py + 0.4, lab, 0.95, "#475569", anchor="end"))
                    else:
                        S.append(t(px, py + 2.2, lab, 0.95, "#475569", anchor="middle"))
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
    # 高度剖面（侧视，1:1；板面为 0 线）
    sy = H + 6.0
    S.append(t(0, sy - 1.4, "高度剖面（侧视 1:1，单位 mm；ESTIMATE 以实测为准）", 2.0, "#0f172a", bold=True))
    S.append('<line x1="0" y1="%s" x2="%s" y2="%s" stroke="#0f172a" stroke-width="0.6"/>' % (fmt(sy), fmt(W), fmt(sy)))
    for c in COMPONENTS:
        up, dn = HEIGHTS.get(c["ref"], (0.0, 0.0))
        if up <= 0 and dn <= 0:
            continue
        x, y, w, h = rect_of(c)
        col = COLORS[c["kind"]]
        if up > 0:
            S.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="0.25"/>'
                     % (fmt(x), fmt(sy - up), fmt(max(w, 2.0)), fmt(up), col["fill"], col["stroke"]))
            S.append(t(x + max(w, 2.0) / 2, sy - up / 2 + 0.5, "%s %.1f" % (c["ref"], up), 1.15, col["text"], anchor="middle"))
        if dn > 0:
            S.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="0.25" stroke-dasharray="1 0.7"/>'
                     % (fmt(x), fmt(sy), fmt(max(w, 2.0)), fmt(dn), col["fill"], col["stroke"]))
            S.append(t(x + max(w, 2.0) / 2, sy + dn / 2 + 0.5, "%s %.1f" % (c["ref"], dn), 1.15, col["text"], anchor="middle"))
    _max_up = max([HEIGHTS.get(c["ref"], (0, 0))[0] for c in COMPONENTS] or [0])
    _max_dn = max([HEIGHTS.get(c["ref"], (0, 0))[1] for c in COMPONENTS] or [0])
    S.append(t(W + 1.0, sy - _max_up - 0.8, "上 %.1f" % _max_up, 1.6, "#b91c1c", bold=True))
    S.append(t(W + 1.0, sy + _max_dn + 1.4, "下 %.1f" % _max_dn, 1.6, "#b91c1c", bold=True))

    S.append('</g>')

    # 图例（板体下方，两列）
    ly = H + 30.0
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


def draw_bottom_svg():
    """底层视图（从板底看，x 已镜像）。底层器件实线、顶层贯穿孔只画焊盘，用于装配对位。"""
    S = []
    W, H = BOARD["w"], BOARD["h"]
    pad_l, pad_t = 6.0, 10.0
    vw, vh = W + 26.0, H + 20.0
    S.append('<?xml version="1.0" encoding="UTF-8"?>')
    S.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%smm" height="%smm">' % (fmt(vw), fmt(vh), fmt(vw), fmt(vh)))
    S.append('<rect x="0" y="0" width="%s" height="%s" fill="#ffffff"/>' % (fmt(vw), fmt(vh)))
    S.append('<g transform="translate(%s %s)">' % (fmt(pad_l), fmt(pad_t)))
    S.append(t(0, -5.5, "机器狗 V1 · PCB 底层视图（从板底看，左右已镜像）", 3.0, "#0f172a", bold=True))
    S.append(t(W, -5.5, "底面 = GND 铺铜 + 电源区辅助铜", 2.0, "#475569", anchor="end"))
    S.append('<rect x="0" y="0" width="%s" height="%s" fill="none" stroke="#0f172a" stroke-width="0.5"/>' % (fmt(W), fmt(H)))
    for (hx, hy) in HOLES:
        S.append('<circle cx="%s" cy="%s" r="%s" fill="#ffffff" stroke="#0f172a" stroke-width="0.3"/>'
                 % (fmt(W - hx), fmt(hy), fmt(HOLE_D / 2)))
    # 底层器件（实线）+ 焊盘
    for c in COMPONENTS:
        if c["side"] != "bottom":
            continue
        x, y, w, h = rect_of(c)
        col = COLORS[c["kind"]]
        S.append('<rect x="%s" y="%s" width="%s" height="%s" rx="0.4" fill="%s" stroke="%s" stroke-width="0.45"/>'
                 % (fmt(W - x - w), fmt(y), fmt(w), fmt(h), col["fill"], col["stroke"]))
        S.append(t(W - x - w + 0.8, y + 2.2, "%s %s" % (c["ref"], c["name"]), 1.8, col["text"], bold=True))
        for i, (dx, dy, lab) in enumerate(PADS.get(c["ref"], [])):
            px, py = W - x - w + dx, y + dy
            S.append('<circle cx="%s" cy="%s" r="0.55" fill="#ffffff" stroke="%s" stroke-width="0.12"/>'
                     % (fmt(px), fmt(py), col["stroke"]))
            if lab:
                S.append(t(px, py + 2.2, lab, 0.95, "#475569", anchor="middle"))
    # 顶层贯穿孔（只画焊盘，表示针从这里穿过）
    for c in COMPONENTS:
        if c["side"] == "bottom":
            continue
        col = COLORS[c["kind"]]
        for i, (dx, dy, lab) in enumerate(PADS.get(c["ref"], [])):
            px, py = W - (c["x"] + dx), c["y"] + dy
            S.append('<circle cx="%s" cy="%s" r="0.45" fill="none" stroke="%s" stroke-width="0.18" stroke-dasharray="0.8 0.5"/>'
                     % (fmt(px), fmt(py), col["stroke"]))
    S.append(t(0, H + 4.0, "虚线圆 = 顶层器件贯穿孔（开发板排母/模块排针/连接器）；镜像后左右与顶层视图相反", 1.8, "#64748b"))
    S.append('</g>')
    S.append('</svg>')
    return "\n".join(S)


# ---------------------------------------------------------------- DXF 板框
def write_dxf(path):
    """导出板框 DXF（mm，R12 ASCII）：外轮廓 R3 圆角矩形 + 4 个安装孔。
    嘉立创EDA Pro：文件 → 导入 → DXF，选 Top 层作板框，再手动指定安装孔。"""
    W, H = BOARD["w"], BOARD["h"]
    r = 3.0
    out = ["0", "SECTION", "2", "ENTITIES"]

    # 文档坐标 y 向下、DXF 的 y 向上 → 统一翻成 y' = H - y，导入后与布局图同向
    def line(x1, y1, x2, y2):
        out.extend(["0", "LINE", "8", "OUTLINE", "10", "%.3f" % x1, "20", "%.3f" % (H - y1),
                    "30", "0.0", "11", "%.3f" % x2, "21", "%.3f" % (H - y2), "31", "0.0"])

    def arc(cx, cy, rad, a0, a1):
        # 翻转后圆弧起止角互换并取补：(a0,a1) -> (360-a1, 360-a0)
        out.extend(["0", "ARC", "8", "OUTLINE", "10", "%.3f" % cx, "20", "%.3f" % (H - cy),
                    "30", "0.0", "40", "%.3f" % rad,
                    "50", "%.1f" % ((360.0 - a1) % 360.0), "51", "%.1f" % ((360.0 - a0) % 360.0)])

    def circle(cx, cy, rad, layer="HOLES"):
        out.extend(["0", "CIRCLE", "8", layer, "10", "%.3f" % cx, "20", "%.3f" % (H - cy),
                    "30", "0.0", "40", "%.3f" % rad])

    # 四边（内缩 r 的直段）
    line(r, 0, W - r, 0)
    line(W, r, W, H - r)
    line(W - r, H, r, H)
    line(0, H - r, 0, r)
    # 四角圆弧
    arc(W - r, r, r, 270, 360)
    arc(W - r, H - r, r, 0, 90)
    arc(r, H - r, r, 90, 180)
    arc(r, r, r, 180, 270)
    # 安装孔（⌀3.4 = R1.7）
    for (hx, hy) in HOLES:
        circle(hx, hy, HOLE_D / 2.0)
    out.extend(["0", "ENDSEC", "0", "EOF"])
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(out) + "\n")
    return len(HOLES)

# ---------------------------------------------------------------- EDA 建图清单
# EDA 里"搜什么 / 怎么建"：模块没有现成符号，一律用 2.54 单排座 + 自定义针名
EDA_SEARCH = {
    "U1": ("排母 2.54 1×22P ×2", "直插 2.54", "开发板直插：放 J1/J3 两排，针名按 22 针表逐个命名"),
    "U3": ("FS5080E", "ESOP-8（带散热焊盘）", "物理脚号按 datasheet；Iset 电阻待定"),
    "L1": ("4.7uH 功率电感 10×10", "CD54/CD75", "FS5080E 的 SW 节点，走线要短粗"),
    "D1": ("SS34", "SMA (DO-214AC)", "阳极朝 MP1584EN 输出，阴极朝 5V"),
    "SW1": ("拨动开关 2 档 侧拨", "SS-12D00 类", "串在 5V 与 FS5080E IN 之间"),
    "Q1": ("AO3401A", "SOT-23", "S=电池侧，D=7V4 侧，G 经 100k 回 S"),
    "J3": ("XT30 母座 2P", "直插", "1=+，2=−（丝印必标极性）"),
    "J4": ("XH2.54-2P 直针", "直插", "M1 左轮"),
    "J5": ("XH2.54-2P 直针", "直插", "M2 右轮"),
    "J6": ("XH2.54-2P 直针", "直插", "4Ω3W 喇叭"),
    "U8": ("排针 2.54 1×9P", "直插", "OLED 9 针：Gnd/Vin/3v3/CS/Rst/SA0/Clk/Data/DC"),
    "U9": ("排针 2.54 1×4P", "直插", "VL53：VIN/GND/SCL/SDA"),
    "U4": ("排母 2.54 2×5P", "直插", "TB6612 模块 10 针：STBY/PWMA/AIN1/AIN2/PWMB/VCC/BIN1/BIN2/GND/VM"),
    "U5": ("排母 2.54 1×4P", "直插", "MP1584EN：OUT+/OUT-/IN+/IN-"),
    "U6": ("排母 2.54 1×7P + 2 焊盘", "直插", "MAX98357A：LRC/BCLK/DIN/GAIN/SD/GND/VIN，另 VO+/VO- 两个焊盘"),
    "U7": ("排母 2.54 1×6P", "直插", "INMP441：SCK/WS/L/R/SD/VDD/GND"),
    "R1": ("100kΩ 0603", "0603", "分压上臂"), "R2": ("47kΩ 0603", "0603", "分压下臂"),
    "R3": ("1kΩ 0603", "0603", "ADC 串保护"), "R4": ("100kΩ 0603", "0603", "Q1 栅极"),
    "R10": ("1kΩ 0603", "0603", "LED 限流"),
    "C1": ("100nF 0603", "0603", "分压滤波"),
    "C3/C4": ("10µF + 100nF 0603", "0603", "3V3 去耦，贴开发板 3V3 脚"),
    "C5/C6": ("22µF 0805", "0805", "FS5080E 输入/输出"),
    "LED1": ("LED 绿 0805", "0805", "电源指示"),
    "TP1-TP6": ("测试点 Ø1.5", "焊盘/TP", "TX/RX/3V3/5V/GND/ADC_BAT"),
}

# 连线顺序：先电源、再地、再总线、最后控制（连错能立刻被地网/电源网暴露）
NET_GROUPS = [
    ("① 电源主干", ["3V3", "5V_BUCK", "5V", "CHG_IN", "7V4", "BAT+", "FS_SW", "8V4"]),
    ("② 地", ["GND"]),
    ("③ 电池监测", ["ADC_BAT"]),
    ("④ I2S 麦克风", ["GPIO4_SCK", "GPIO5_WS", "GPIO6_SD"]),
    ("⑤ I2S 功放 + 喇叭", ["GPIO7_DIN", "GPIO15_BCLK", "GPIO16_LRC", "SPK+", "SPK-"]),
    ("⑥ 电机控制 + 输出", ["GPIO10", "GPIO11", "GPIO12", "GPIO13", "GPIO14",
                          "GPIO47", "GPIO21", "MOTO_A", "MOTO_B"]),
    ("⑦ I2C（VL53）", ["GPIO42_SCL", "GPIO41_SDA"]),
    ("⑧ SPI（OLED）", ["GPIO48_RES", "GPIO1_SCLK", "GPIO2_MOSI", "GPIO46_DC"]),
    ("⑨ 调试 / 指示", ["GPIO43_TX", "GPIO44_RX", "LED1"]),
]

PITFALLS = [
    ("GPIO9 不接任何东西", "模式按键已从 PCB 与面包板移除，网络表里没有 GPIO9 网——EDA 里别手痒补上。"),
    ("5V 必须经 D1(SS34)", "MP1584EN 的 OUT+ 不能直连开发板 5V 脚；中间串 SS34，否则插 USB 时倒灌。"),
    ("SW1 在 FS5080E 的 IN 之前", "顺序是 5V → SW1 → U3.IN。接反等于开关失效，电池会自己给自己充电。"),
    ("电机 VM 走 7V4 不是 5V", "U4.VM 接 7V4；接 5V 会让 MP1584EN 峰值 3.67A 超载。"),
    ("MAX98357A：GAIN→GND、SD→VIN", "模块上这两根是模块内短接的等效；VIN 是 5V。"),
    ("INMP441：L/R→GND", "选左声道；悬空会出双声道叠加的噪声。"),
    ("OLED：CS/SA0→GND，Vin 与 3v3 都→3V3", "CS 固定片选、SA0 固定地址；Vin/3v3 同时给 3V3（模块内部 LDO 直通）。"),
    ("开发板 3V3 是输出", "J1-32/33 是板载 LDO 输出，往传感器送电；别当成需要外部供电的输入。"),
]


def _members(net):
    out = []
    for n, ref, pin in NETS:
        if n != net:
            continue
        no = pin_no(ref, pin)
        out.append("%s.%s%s" % (ref, pin, ("(%s)" % no) if no else ""))
    return out


def write_checklist(path):
    """生成《EDA 原理图建图顺序表》：元件清单（搜什么）+ 按序连线表 + 自检点 + 易错点。"""
    rows_bom = []
    for ref, name, fp, qty, note in BOM_SELF:
        search, foot, tip = EDA_SEARCH.get(ref, ("—", fp, note))
        rows_bom.append("<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                        % (ref, name, search, foot, qty, tip))

    rows_net, total_nets, total_members = [], 0, 0
    for group, nets in NET_GROUPS:
        for net in nets:
            mem = _members(net)
            if not mem:
                continue
            total_nets += 1
            total_members += len(mem)
            rows_net.append(
                "<tr><td>%s</td><td><code>%s</code></td><td>%d</td><td>%s</td><td><input type=\"checkbox\"></td></tr>"
                % (group, net, len(mem), " → ".join(mem)))

    rows_pit = "".join("<tr><td>%d</td><td><b>%s</b></td><td>%s</td></tr>" % (i + 1, a, b)
                       for i, (a, b) in enumerate(PITFALLS))

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>机器狗 V1 · EDA 原理图建图顺序表</title>
<style>
 body{font-family:system-ui,-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;margin:0;background:#f8fafc;color:#0f172a;line-height:1.6}
 .wrap{max-width:1080px;margin:0 auto;padding:26px 20px 60px}
 header{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:18px 22px;margin-bottom:16px}
 h1{font-size:21px;margin:0 0 6px} h2{font-size:16px;margin:24px 0 8px;padding-left:10px;border-left:4px solid #2563eb}
 h3{font-size:14px;margin:16px 0 6px}
 .meta{color:#475569;font-size:13px}
 table{border-collapse:collapse;width:100%%;background:#fff;font-size:12.5px;margin:8px 0 14px}
 th,td{border:1px solid #e2e8f0;padding:5px 7px;text-align:left;vertical-align:top}
 th{background:#f1f5f9;position:sticky;top:0;z-index:1}
 code{background:#f1f5f9;padding:1px 4px;border-radius:4px;font-size:12px}
 .card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px;margin-bottom:14px}
 .warn{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:9px 12px;font-size:13px;margin:8px 0}
 .ok{background:#dcfce7;border-left:4px solid #16a34a;padding:8px 12px;border-radius:0 8px 8px 0;font-size:13px}
 ol,ul{margin:6px 0 10px;padding-left:22px} li{margin:3px 0}
 input[type=checkbox]{width:16px;height:16px}
 @media print{body{background:#fff}.card,header{border:none;padding:0}}
</style></head><body><div class="wrap">
<header>
 <h1>机器狗 V1 · EDA 原理图建图顺序表 <span style="font-size:12px;background:#dbeafe;color:#1e3a8a;padding:1px 8px;border-radius:999px">照此顺序点</span></h1>
 <div class="meta">对应工程：<code>tools/gen_pcb_design.py</code>（单一事实源）· 网表：<code>docs/PCB网表-v2.csv</code> ·
 共 <b>%d</b> 个网络 / <b>%d</b> 个引脚连接 · 生成于 2026-10-20。改网表后重跑生成器，本表自动同步。</div>
</header>

<div class="card">
 <h2 style="margin-top:0">0. 开图前 3 分钟</h2>
 <ol>
  <li>新建工程（名称 <code>ai-dog-v1</code>）→ 原理图页 1 张；单位 mm，栅格 1.27（按 2.54 布针脚）。</li>
  <li>库：优先「立创商城」（有库存与价格），搜不到的用「系统库」；模块类没有现成符号 → 一律用 2.54 排母座自建，针名按模块丝印。</li>
  <li>先画<b>开发板两排 22 针</b>（J1/J3），把它们当"总线端子"，后面所有连线都从这两排出发。</li>
 </ol>
</div>

<div class="card">
 <h2 style="margin-top:0">1. 元件清单（按这个顺序放）</h2>
 <table>
  <tr><th style="width:70px">位号</th><th>器件</th><th style="width:24%%">EDA 里搜什么</th><th style="width:14%%">封装</th><th style="width:44px">数量</th><th>放置要点</th></tr>
  %s
 </table>
 <div class="warn"><b>模块符号的做法</b>：TB6612 / MP1584EN / MAX98357A / INMP441 都是成品模块，EDA 里没有对应符号。
 正确做法是放 <b>2.54 排母座</b>（1×4 / 1×7 / 2×5 / 1×6），双击改针名为模块丝印上的名字（STBY、PWMA…），
 这样原理图与实物、与 PCB 焊盘三处同名，后面查线不用翻译。</div>
</div>

<div class="card">
 <h2 style="margin-top:0">2. 连线顺序表（共 %d 个网络 / %d 个连接）</h2>
 <p style="font-size:13px;margin:4px 0">顺序是刻意的：<b>先电源、再地、再总线、最后控制</b>。电源与地连完就能用万用表验，后面任何一根控制线接错都会立刻显形。</p>
 <table>
  <tr><th style="width:120px">阶段</th><th style="width:130px">网络</th><th style="width:56px">成员</th><th>成员（位号.针名(针号) → …）</th><th style="width:40px">✓</th></tr>
  %s
 </table>
</div>

<div class="card">
 <h2 style="margin-top:0">3. 连完自检（必须全绿才转 PCB）</h2>
 <ul>
  <li>「设计 → 检查 DRC」：<b>未连接引脚 = 0</b>、重复位号 = 0、单端网络 = 0。</li>
  <li>网络数应 = <b>%d</b>；逐个点网络看高亮成员数是否与上表"成员"列一致（不一致就是漏连/多连）。</li>
  <li>把原理图与 <code>docs/PCB网表-v2.csv</code> 并排：CSV 的 108 行应能一一对上。</li>
  <li>GND 网络成员最多（19 个），最容易漏；点 GND 高亮，数一遍。</li>
  <li>最后：设计 → 更新/转换到 PCB，飞线数应为 108 条（每条 pin-net 一根）。</li>
 </ul>
</div>

<div class="card">
 <h2 style="margin-top:0">4. 这 8 个地方最容易点错</h2>
 <table><tr><th style="width:36px">#</th><th style="width:34%%">易错点</th><th>为什么 / 怎么点</th></tr>%s</table>
</div>

<div class="card">
 <h2 style="margin-top:0">5. 画完之后</h2>
 <ol>
  <li>原理图 → PCB：导入 <code>docs/source/pcb-outline-v2.dxf</code> 作板框（y 已翻转，方向与布局图一致）。</li>
  <li>按 <code>docs/source/pcb-layout-v2.svg</code> 摆件（坐标、焊盘、1 脚方标都在图上）。</li>
  <li>按 PCB 文档 §4.1 的 7 条通道布线 → 底层铺 GND → DRC 0/0 → JLCDFM → Gerber。</li>
 </ol>
 <div class="ok"><b>一句话：</b>这张表把"画原理图"变成"按 36 行顺序点 108 次"——顺序对了，错只会在最后一步暴露，而不是在打样之后。</div>
</div>
</div></body></html>""" % (total_nets, total_members, "\n  ".join(rows_bom), total_nets, total_members,
                            "\n  ".join(rows_net), total_nets, rows_pit)

    html = html.replace("GND 网络成员最多（19 个）",
                        "GND 网络成员最多（%d 个）" % len(_members("GND")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return total_nets, total_members


# ---------------------------------------------------------------- 输出
def write_csv(path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Net", "Ref", "PinName", "PinNo", "Note"])
        for net, ref, pin in sorted(NETS):
            no = pin_no(ref, pin)
            w.writerow([net, ref, pin, no, "" if no else "未映射：按实物丝印核对"])
    return len(NETS)


def write_json(path):
    data = {
        "version": "v2",
        "date": "2026-10-20",
        "board": BOARD,
        "holes": HOLES,
        "rf_keepout": RF_KEEPOUT,
        "components": COMPONENTS,
        "nets": [{"net": n, "ref": r, "pin": p,
                  "pin_no": pin_no(r, p)} for n, r, p in NETS],
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
    bot = draw_bottom_svg()
    with open(os.path.join(SRC_DIR, "pcb-layout-v2-bottom.svg"), "w", encoding="utf-8") as f:
        f.write(bot)
    n = write_csv(os.path.join(OUT_DIR, "PCB网表-v2.csv"))
    nh = write_dxf(os.path.join(SRC_DIR, "pcb-outline-v2.dxf"))
    nn, nm = write_checklist(os.path.join(OUT_DIR, "EDA-schematic-checklist-v1.html"))
    data = write_json(os.path.join(SRC_DIR, "pcb-design-baseline-v2.json"))
    print("已生成 docs/source/pcb-layout-v2.svg（%d 字节）" % len(svg))
    print("已生成 docs/source/pcb-layout-v2-bottom.svg（%d 字节）" % len(bot))
    print("已生成 docs/PCB网表-v2.csv（%d 条 pin-net）" % n)
    print("已生成 docs/source/pcb-outline-v2.dxf（板框 + %d 个安装孔，R12 ASCII/mm）" % nh)
    print("已生成 docs/EDA-schematic-checklist-v1.html（%d 网 / %d 连接）" % (nn, nm))
    print("已生成 docs/source/pcb-design-baseline-v2.json（%d 个网络）" % len(set(x["net"] for x in data["nets"])))


if __name__ == "__main__":
    main()
