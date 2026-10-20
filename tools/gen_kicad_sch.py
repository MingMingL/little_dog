#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原理图生成器（KiCad 7 .kicad_sch + SVG 预览）
=====================================================
数据源：tools/gen_pcb_design.py 的 NETS / BOM_SELF / PINNO（单一事实源）。
画法：**网络标签式**——每个引脚拉 2.54mm 短线，端点放全局标签（网名）。
连接关系由标签名保证，不依赖导线几何，所以 108 条连接一条都不会漏；
符号一律"矩形 + 左侧引脚"，针号 1..N，针名取网表针名（实物针号见 CSV 的 PinNo 列）。

输出：
  docs/source/ai-dog-v1.kicad_sch    可导入嘉立创EDA（文件→导入→KiCad）
  docs/source/schematic-preview-v1.svg  版式预览（不导入也能看）
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_pcb_design as g  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "docs", "source")

PITCH = 2.54          # 引脚间距
STUB = 2.54           # 引脚引出短线
BODY_W = 15.24        # 符号本体宽
PAPER = (594.0, 420.0)  # A2

# 分栏布局：每栏一组功能，栏内从上往下排
COLUMNS = [
    ("电源", ["J3", "U5", "D1", "Q1", "U3", "L1", "SW1"], 40.0),
    ("主控", ["U1"], 150.0),
    ("音频", ["U6", "U7", "J6"], 265.0),
    ("电机", ["U4", "J4", "J5"], 350.0),
    ("传感器", ["U8", "U9"], 445.0),
    ("分压/指示/测试点", ["R1", "R2", "R3", "R4", "R10", "C1", "C3", "C4", "C5", "C6", "LED1",
                          "TP1", "TP2", "TP3", "TP4", "TP5", "TP6"], 525.0),
]


def uid():
    return str(uuid.uuid4())


def ref_pins():
    """{ref: [(pin_name, net)]}，按网表去重，保持首次出现顺序。"""
    out = {}
    for net, ref, pin in g.NETS:
        out.setdefault(ref, [])
        if all(p != pin for p, _ in out[ref]):
            out[ref].append((pin, net))
    return out


def symbol_def(ref, pins):
    """一个 ref 一个符号：矩形 + 左侧引脚（针号 1..N，针名取网表针名）。"""
    n = len(pins)
    h = (n - 1) * PITCH
    top = h / 2.0
    s = []
    s.append('    (symbol "aidog:%s"' % ref)
    s.append('      (pin_names (offset 1.016)) (in_bom yes) (on_board yes)')
    s.append('      (property "Reference" "%s" (at %.2f %.2f 0) (effects (font (size 1.27 1.27))))'
             % (ref[:1] if ref[0].isalpha() else "U", BODY_W / 2, top + 3.81))
    s.append('      (property "Value" "%s" (at %.2f %.2f 0) (effects (font (size 1.27 1.27))))'
             % (ref, BODY_W / 2, -top - 3.81))
    s.append('      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))')
    s.append('      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))')
    s.append('      (symbol "%s_0_1"' % ref)
    s.append('        (rectangle (start %.2f %.2f) (end %.2f %.2f)'
             % (0.0, top + 1.27, BODY_W, -top - 1.27))
    s.append('          (stroke (width 0.254) (type default)) (fill (type none)))')
    s.append('      )')
    s.append('      (symbol "%s_1_1"' % ref)
    for i, (name, _net) in enumerate(pins):
        y = top - i * PITCH
        s.append('        (pin passive line (at %.2f %.2f 0) (length %.2f)'
                 % (-STUB, y, STUB))
        s.append('          (name "%s" (effects (font (size 1.0 1.0))))' % name.replace('"', ''))
        s.append('          (number "%d" (effects (font (size 1.0 1.0)))))' % (i + 1))
    s.append('      )')
    s.append('    )')
    return "\n".join(s), top


def main():
    pins_by_ref = ref_pins()
    root_uuid = uid()

    sym_defs, placements = [], []
    for group, refs, x in COLUMNS:
        y = 40.0
        for ref in refs:
            if ref not in pins_by_ref:
                continue
            pins = pins_by_ref[ref]
            definition, top = symbol_def(ref, pins)
            sym_defs.append(definition)
            placements.append((group, ref, x, y, top, pins))
            y += (len(pins) - 1) * PITCH + 20.0

    body = []
    # 符号实例 + 每根针的短线与全局标签
    for group, ref, x, y0, top, pins in placements:
        body.append('  (symbol (lib_id "aidog:%s") (at %.2f %.2f 0) (unit 1)'
                    % (ref, x, y0))
        body.append('    (in_bom yes) (on_board yes) (dnp no) (uuid "%s")' % uid())
        body.append('    (property "Reference" "%s" (at %.2f %.2f 0)'
                    ' (effects (font (size 1.27 1.27)) (justify left)))'
                    % (ref, x + BODY_W + 1.5, y0 - top))
        body.append('    (property "Value" "%s" (at %.2f %.2f 0)'
                    ' (effects (font (size 1.27 1.27)) (justify left)))'
                    % (group, x + BODY_W + 1.5, y0 - top + 2.2))
        body.append('    (property "Footprint" "" (at %.2f %.2f 0)'
                    ' (effects (font (size 1.27 1.27)) hide))' % (x, y0))
        body.append('    (property "Datasheet" "" (at %.2f %.2f 0)'
                    ' (effects (font (size 1.27 1.27)) hide))' % (x, y0))
        for i, (name, net) in enumerate(pins):
            body.append('    (pin "%d" (uuid "%s"))' % (i + 1, uid()))
        body.append('    (instances (project "ai-dog" (path "/%s" (reference "%s") (unit 1))))'
                    % (root_uuid, ref))
        body.append('  )')

        # 每根针：短线 + 全局标签
        for i, (name, net) in enumerate(pins):
            py = y0 - (top - i * PITCH)      # 符号 Y 向上、图纸 Y 向下 → 取反
            px = x - STUB
            body.append('  (wire (pts (xy %.2f %.2f) (xy %.2f %.2f))'
                        ' (stroke (width 0) (type default)) (uuid "%s"))'
                        % (px, py, px - 3.81, py, uid()))
            body.append('  (global_label "%s" (shape input) (at %.2f %.2f 180) (fields_autoplaced)'
                        % (net, px - 3.81, py))
            body.append('    (effects (font (size 1.0 1.0)) (justify right))')
            body.append('    (uuid "%s")' % uid())
            body.append('    (property "Intersheetrefs" "${INTERSHEET_REFS}" (at 0 0 0)'
                        ' (effects (font (size 1.27 1.27)) hide))')
            body.append('  )')

    sch = []
    sch.append('(kicad_sch (version 20230121) (generator eeschema)')
    sch.append('  (uuid "%s")' % root_uuid)
    sch.append('  (paper "A2")')
    sch.append('  (title_block')
    sch.append('    (title "机器狗 V1 · ai-dog-v1")')
    sch.append('    (date "2026-10-20")')
    sch.append('    (rev "v2.3")')
    sch.append('    (comment 1 "网络标签式原理图：连接由标签名保证；实物针号见 PCB网表-v2.csv")')
    sch.append('  )')
    sch.append('  (lib_symbols')
    sch.extend(sym_defs)
    sch.append('  )')
    sch.extend(body)
    sch.append('  (sheet_instances (path "/" (page "1")))')
    sch.append(')')
    text = "\n".join(sch) + "\n"

    path = os.path.join(SRC, "ai-dog-v1.kicad_sch")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    # ---- SVG 版式预览（不导入也能看）----
    svg = []
    W, H = PAPER
    svg.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%dmm" height="%dmm">'
               % (W, H, W, H))
    svg.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
    svg.append('<text x="20" y="24" font-size="7" font-family="sans-serif" font-weight="bold">'
               '机器狗 V1 · 原理图版式（网络标签式）</text>')
    for group, ref, x, y0, top, pins in placements:
        h = (len(pins) - 1) * PITCH + 2.54
        svg.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="#f8fafc" '
                   'stroke="#0f172a" stroke-width="0.3"/>' % (x, y0 - top - 1.27, BODY_W, h))
        svg.append('<text x="%.2f" y="%.2f" font-size="2.6" font-family="sans-serif" '
                   'font-weight="bold">%s</text>' % (x + 1, y0 - top + 0.9, ref))
        for i, (name, net) in enumerate(pins):
            py = y0 - (top - i * PITCH)
            svg.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#0f172a" stroke-width="0.2"/>'
                       % (x - STUB, py, x, py))
            svg.append('<text x="%.2f" y="%.2f" font-size="1.9" font-family="sans-serif" '
                       'text-anchor="end" fill="#2563eb">%s</text>' % (x - STUB - 0.6, py + 0.6, net))
            svg.append('<text x="%.2f" y="%.2f" font-size="1.7" font-family="sans-serif">%s</text>'
                       % (x + 0.8, py + 0.6, name))
    svg.append('</svg>')
    with open(os.path.join(SRC, "schematic-preview-v1.svg"), "w", encoding="utf-8") as f:
        f.write("\n".join(svg))

    # ---- 自检 ----
    placed = set(r for _g, r, _x, _y, _t, _p in placements)
    missing = sorted(set(r for _n, r, _p in g.NETS) - placed)
    if missing:
        print("!! 网表里有位号没被放置：%s" % ", ".join(missing))
    n_labels = sum(len(p) for _g, _r, _x, _y, _t, p in placements)
    n_parts = len(placements)
    depth, bad = 0, 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                bad += 1
    print("元件 %d 个，引脚/标签 %d 个，网表连接 %d 条" % (n_parts, n_labels, len(g.NETS)))
    print("括号平衡: %s（结束深度 %d，负深度 %d）" % ("OK" if depth == 0 and bad == 0 else "!!", depth, bad))
    print("生成 %s（%d 字节）" % (os.path.join("docs", "source", "ai-dog-v1.kicad_sch"), len(text)))
    return 0 if depth == 0 and bad == 0 and not missing and n_labels == len(g.NETS) else 1


if __name__ == "__main__":
    sys.exit(main())
