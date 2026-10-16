#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESP32-S3 N16R8 引脚 -> Z 面包板（行, 列）速查工具
====================================================
数据源：tools/data/esp32s3_n16r8_component.svg（元件 44 针，data-pin 标签）。
物理取向：USB 口朝右（2026-10-16 用户决定）。外层变换 translate(112.4 -13.34) rotate(90)：
    世界(x, y) = (112.4 - y_local, -13.34 + x_local)
  - 上排 22 针（局部 x=1.27）-> 世界 y=-12.07 = X 板 **j 行**，官方 pinout 属 **J3**
    （GND TX RX GPIO1/2/42-19/GND；与 CH343 同侧）
  - 下排 22 针（局部 x=26.67）-> 世界 y=13.33 = Y 板 **k 行**，属 **J1**
    （3V3 3V3 RST GPIO4-14 5V GND）
每排左->右 = 列 22..43（列 c 孔心 x = 3.81 + (c-1)*2.54）。

用法：
    python tools/esp32_pin_map.py            # 打印两排序列 + GPIO->(行,列) 表
    python tools/esp32_pin_map.py 4 5 6 15   # 查指定 GPIO 的落点

注意：本数据文件是无命名空间片段（外层直接 <g>，无 xmlns），标签解析不能加 SVG NS。
后续元件接线（INMP441/MAX98357A/TB6612/TCRT5000/VL53L0X/OLED/按键/分压/电源）
都以本表为唯一依据，不要再凭记忆猜行列。
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "data", "esp32s3_n16r8_component.svg")
COL_X0, PITCH = 3.81, 2.54
# 世界 y -> Z 板行（X 板行 a-j 在世界 y<0 侧，Y 板行 k-t 在 y>0 侧）
ROW_OF_WY = {-12.07: "j", 13.33: "k"}
# 官方 pinout（user guide v1.1）：上排=j 行=J3，下排=k 行=J1
CONNECTOR_OF_ROW = {"j": "J3", "k": "J1"}


def load_pins():
    """返回 [(world_x, world_y, 标签, 行, 列), ...]，共 44 针。"""
    root = ET.fromstring(open(PATH, encoding="utf-8").read())
    pins = []
    for el in root.iter():
        if el.tag != "circle" or not el.get("data-pin"):
            continue
        lx, ly = float(el.get("cx")), float(el.get("cy"))
        wx, wy = 112.4 - ly, -13.34 + lx
        label = el.get("data-pin").replace("esp32s3_n16r8_dual_usb_1.", "")
        row = ROW_OF_WY[round(wy, 2)]
        col = int(round((wx - COL_X0) / PITCH)) + 1
        pins.append((wx, wy, label, row, col))
    assert len(pins) == 44, "data-pin 圆 %d 个，应为 44" % len(pins)
    return sorted(pins, key=lambda p: (p[3], p[4]))


def main():
    pins = load_pins()
    by_row = {}
    for _, _, label, row, col in pins:
        by_row.setdefault(row, []).append((col, label))

    if len(sys.argv) > 1:  # 查指定 GPIO
        want = {int(a) for a in sys.argv[1:] if re.fullmatch(r"\d+", a)}
        for p in pins:
            m = re.fullmatch(r"GPIO(\d+)", p[2])
            if m and int(m.group(1)) in want:
                print("GPIO%-3d -> %s 行第 %d 列（%s 排针）"
                      % (int(m.group(1)), p[3], p[4], CONNECTOR_OF_ROW[p[3]]))
        missing = want - {int(re.fullmatch(r"GPIO(\d+)", p[2]).group(1))
                          for p in pins if re.fullmatch(r"GPIO(\d+)", p[2])}
        for g in sorted(missing):
            print("GPIO%-3d -> 不在 44 针上（该板未引出）" % g)
        return

    for row in ("j", "k"):
        seq = by_row[row]
        print("%s 行 = %s（22 针，左->右 = 列 %d..%d）："
              % (row, CONNECTOR_OF_ROW[row], seq[0][0], seq[-1][0]))
        print("   " + " ".join("%s@%d" % (lab, c) for c, lab in seq))
        print()
    print("GPIO -> (行,列)：")
    for p in pins:
        m = re.fullmatch(r"GPIO(\d+)", p[2])
        if m:
            print("   GPIO%-3d -> %s 行 %d 列" % (int(m.group(1)), p[3], p[4]))


if __name__ == "__main__":
    main()
