#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电源网连通性校验（2026-10-19 元件 9 收尾时新增）
====================================================
为什么要有这个工具：wire_check 查的是"线有没有压到孔/本体/别的线"（几何净空），
**查不出电气断网**。2026-10-19 元件 9 的 9 根馈线画完、wire_check 全绿之后，逐组查账
仍发现 4 处"图画了、电不通"——因为面包板电源轨印成连续红线/蓝线，金属条却是 5 孔一小组：

    轨孔只通电同组另外 4 个孔，组间 5.1mm 空隙，左右邻居全部不通。

    ① zx.top_inner 东段：3V3 馈线落 35（第2组），OLED Vin/3v3 落 36/38（第3组）→ 不断
    ② zx.top_outer 东段：GND 馈线落 36（第3组），OLED Gnd 落 35（第2组）     → 不断
    ③ zy.bottom_inner：MP1584 OUT+ 落 28（第1组），MAX VIN/ESP32 5V 落 41/42（第4组）
       → **降压输出没接到功放和板子，整机不上电**
    ④ zy.bottom_outer：按键地26 / OUT−35 / 功放地40 / 电机地+板子地44 / 麦克风地50
       分落 5 个不同小组 → **5 个"地"互不相通，等于没有共地**

修法即图中 7 根"轨组短跳线"（gen_breadboard_z.rail_bridge）。本工具把这类错误固化成断言：

    面包板电气连通 = 板内铜条 + 跳线
      - 终端孔：同板、同列、同半区（X: a–e / f–j；Y: k–o / p–t）5 孔互通
      - 导轨孔：同板、同轨、同 5 孔小组互通（x=82.55 断开左右两段）
      - 跳线：每根 wire 的 from/to 两端联通（板上针换算成终端孔；板外 xy 针不入板）

    断言 A：同一网络的落孔必须落在同一个连通分量里（否则就是断网）
    断言 B：不同网络不得共占一个连通分量（否则就是图上/实物短接）
    断言 C：GND 允许且只允许两块分量（X 板 / Y 板），且二者仅经 ESP32 板载地平面
            （GND_1..4 四针内部共地）连通——这是本设计**有意**的走向，不是漏画：
            电机回流在 Y 板内闭环（电池− → MP1584 IN− → OUT− → 轨 → TB GND），
            不经过 ESP32；X 板只挂 VL53/OLED 两个小电流传感器，回流走板载地平面没问题。

用法：python tools/net_check.py（退出码非 0 = 有断网/短接）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_breadboard_z as g  # noqa: E402

# (元件id, 线序) -> 网名。必须逐条列全：同一种颜色可以载不同电位（如 wire_vcc 同时
# 载 3V3 与 7.4V 电池），所以不能按 cls 自动归类，只能显式登记。
NET_OF = {
    # ---- 3V3（ESP32 板载 LDO，只带小电流）----
    ("inmp441", 3): "3V3",          # VDD -> m32（与 k32/k33 3V3 脚同列同半区）
    ("tb6612fng", 7): "3V3",        # VCC -> o32（同上，TB 逻辑电源）            # n32 -> zx.top_inner_21（西段馈线）            # o32 -> zx.top_inner_35（东段馈线）            # l32 -> l27（按键上拉取电）            # 轨组短跳线 top_inner 35->36
    ("vl53l0x", 0): "3V3",          # VIN -> zx.top_inner_25
    ("oled", 1): "3V3",             # Vin  -> zx.top_inner_36
    ("oled", 2): "3V3",             # 3v3  -> zx.top_inner_38
    # ---- 5V（MP1584EN 输出，功放专用）----
    ("max98357a", 4): "5V",         # SD_MODE -> VIN 模块内短接（SD 脚被拉到 5V）
    ("max98357a", 6): "5V",         # VIN -> zy.bottom_inner_41             # bottom_inner_42 -> l52（ESP32 5V 脚）            # 轨组短跳线 bottom_inner 40->41
    ("mp1584en", 0): "5V",          # OUT+ -> zy.bottom_inner_38
    # ---- GND ----
    ("inmp441", 4): "GND", ("inmp441", 5): "GND",
    ("max98357a", 3): "GND", ("max98357a", 5): "GND",
    ("tb6612fng", 8): "GND",
    ("vl53l0x", 1): "GND",
    ("oled", 0): "GND", ("oled", 3): "GND", ("oled", 4): "GND",   # Gnd/CS/SA0 接地            # i32 -> zx.top_outer_24（西段馈线，源 = j32 GND_3）            # h32 -> zx.top_outer_36（东段馈线）            # l53 -> zy.bottom_outer_44（源 = k53 GND_4）            # 按键地 p29 -> zy.bottom_outer_26           # 轨组短跳线 top_outer 35->36           # 轨组短跳线 bottom_outer 30->31           # 轨组短跳线 bottom_outer 35->36           # 轨组短跳线 bottom_outer 40->41           # 轨组短跳线 bottom_outer 45->46
    ("mp1584en", 1): "GND",         # OUT− -> zy.bottom_outer_35（模块内地与 IN− 共地）
    # ---- 7.4V 电池（不上面包板，走模块 VM / 降压 IN+）----
    ("tb6612fng", 9): "7V4",        # 电池正极 -> TB VM
    ("mp1584en", 2): "7V4",         # 电池正极分接 -> MP1584 IN+
    ("mp1584en", 3): "BAT-",        # 电池负极 -> MP1584 IN−（经模块内地并入 GND 网）
}

# ESP32 板载地平面：四个 GND 针在开发板内部共地（user guide v1.1）。
# 图中没有这根跳线，是靠板子自己连的——显式登记，免得被误判成"断网"。
DEV_GND_PINS = ["zx.j32", "zx.j52", "zx.j53", "zy.k53"]
DEV_3V3_PINS = ["zy.k32", "zy.k33"]      # 两个 3V3 脚内部共地
DEV_5V_PIN = ["zy.k52"]


class UF(object):
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def terminal_node(name):
    """孔名 -> 它在板内铜条上的"节点键"（同键即互通）。"""
    prefix, rest = name.split(".")
    if "_" in rest:                                  # 轨孔：同板同轨同 5 孔小组
        rname, idx = rest.rsplit("_", 1)
        idx = int(idx)
        j = (idx - 1) if idx <= g.RAIL_HOLES_PER_SIDE else (idx - 1) - g.RAIL_HOLES_PER_SIDE
        return ("rail", prefix, rname, "L" if idx <= g.RAIL_HOLES_PER_SIDE else "R", j // 5)
    letter, c = rest[0], int(rest[1:])             # 终端孔：同板同列同半区（行字母均单字符）
    rows = g.X_ROWS if prefix == "zx" else g.Y_ROWS
    region = 0 if rows.index(letter) < 5 else 1
    return ("term", prefix, c, region)


def pin_hole(comp, pin):
    """板上针 -> 孔名；板外 xy 针 / 非孔端点 -> None。"""
    ref = g.pin_ref(pin)
    if ref[0] == "rc":
        return "%s.%s%d" % (comp["board"], ref[1], ref[2])
    return None


def endpoint_keys(comp, w):
    """一根跳线的两端"节点键"（三类键都能直接进并查集）：
      板上孔 -> 孔名（str）；板外针 -> ("pin", 元件id, 针号)；板外坐标 -> ("xy", x, y)。
    断言只统计孔名与 xy 两类（板外针是模块焊盘，电位由它所连的那根线决定，单独登记无校验意义）。"""
    out = []
    for end in (w["from"], w["to"]):
        if end[0] == "hole":
            out.append(end[1])
        elif end[0] == "pin":
            h = pin_hole(comp, comp["pins"][end[1]])
            out.append(h if h else ("pin", comp["id"], end[1]))
        else:
            out.append(("xy", end[1][0], end[1][1]))
    return out


def build(dev_links=True):
    """建图（并查集）。电气连通 = 板内铜条 + ESP32 板载内部连接 + 跳线 + 板外同坐标接点。
    dev_links=False 时**不建** ESP32 板载内部连接——用来验证"X/Y 两个地在面包板层面
    本来就是两块、只靠板载地平面合并"（断言 C 的前半）。"""
    uf = UF()
    # 1) 板内铜条：所有孔按 terminal_node 归并（同节点 = 同一条金属条）
    all_holes = []
    for prefix, rows in (("zx", g.X_ROWS), ("zy", g.Y_ROWS)):
        for c in range(1, g.N_COLS + 1):
            for letter in rows:
                all_holes.append("%s.%s%d" % (prefix, letter, c))
        for rname, _ry in g.RAIL_YS:
            for i in range(1, 2 * g.RAIL_HOLES_PER_SIDE + 1):
                all_holes.append("%s.%s_%d" % (prefix, rname, i))
    by_node = {}
    for name in all_holes:
        by_node.setdefault(terminal_node(name), []).append(name)
    for names in by_node.values():
        for nm in names[1:]:
            uf.union(names[0], nm)
    # 2) ESP32 板载内部连接（不在面包板上，但电气真实存在）
    if dev_links:
        for pins in (DEV_GND_PINS, DEV_3V3_PINS, DEV_5V_PIN):
            for p in pins[1:]:
                uf.union(pins[0], p)
    # 3) 跳线：from/to 两端联通（板外针/坐标也入图）；顺带登记各网络的落孔/坐标
    # 4) 板外同坐标接点：两根线的端点画在同一个坐标上 = 焊在同一个点
    #    （如电池正极 (32,73) 同时分出 TB6612 的 VM 与 MP1584 的 IN+）
    net_holes = {}
    xy_at = {}
    for comp in g.COMPONENTS:
        for wi, w in enumerate(comp["wires"]):
            ka, kb = endpoint_keys(comp, w)
            if ka and kb:
                uf.union(ka, kb)
            for k in (ka, kb):
                if k[0] == "xy":
                    xy_at.setdefault((k[1], k[2]), []).append(k)
            net = w.get("net") or NET_OF.get((comp["id"], wi))
            if net:
                for k in (ka, kb):
                    if isinstance(k, str) or k[0] == "xy":
                        net_holes.setdefault(net, set()).add(k)
    for group in xy_at.values():
        for k in group[1:]:
            uf.union(group[0], k)
    return uf, net_holes, by_node


def fmt_key(k):
    """节点键 -> 可读字符串（连通分量的根可能是孔名 / xy / 板外针）。"""
    if isinstance(k, str):
        return k
    if k[0] == "xy":
        return "xy(%.1f,%.1f)" % (k[1], k[2])
    return "%s.pin%d" % (k[1], k[2])


def check(uf, net_holes, gnd_expect, title):
    """一轮断言。gnd_expect = GND 应有的连通分量数（面包板层面 2 / 整机层面 1）。"""
    bad = 0
    print("== %s ==" % title)
    # 断言 A：同一网络必须同一个连通分量（GND 在面包板层面允许 2：X 板 / Y 板各一块）
    comp_of_net = {}
    for net, holes in sorted(net_holes.items()):
        if not holes:                      # 全是板外针的网（当前没有），无从校验
            print("OK  %-5s 无落孔节点（板外互连）" % net)
            comp_of_net[net] = set()
            continue
        expect = gnd_expect if net == "GND" else 1
        roots = {}
        for h in sorted(holes, key=fmt_key):
            roots.setdefault(uf.find(h), []).append(h)
        comp_of_net[net] = set(roots)
        status = "OK " if len(roots) == expect else "!! "
        if len(roots) != expect:
            bad += 1
        print("%s%-5s 节点 %2d 个 / 连通分量 %d 个" % (status, net, len(holes), len(roots)))
        for r, hs in roots.items():
            tag = ""
            if net == "GND" and len(roots) == 2:
                tag = "  <- X / Y 两块，尚未合并"
                boards = {h.split(".")[0] for h in hs if isinstance(h, str)}
                if len(boards) != 1:       # 两块地必须各自纯在一块板上（否则有跨板地线）
                    bad += 1
                    print("!! GND 分量 %s 跨板 %s（不应有跨板地线）" % (fmt_key(r), sorted(boards)))
            print("      [%s] %s%s" % (fmt_key(r), " ".join(fmt_key(h) for h in hs), tag))
    # 断言 B：不同网络不得共占一个分量
    owner = {}
    for net, holes in net_holes.items():
        for h in holes:
            owner.setdefault(uf.find(h), set()).add(net)
    for r, nets in sorted(owner.items(), key=lambda kv: fmt_key(kv[0])):
        if len(nets) > 1:
            bad += 1
            print("!! 共占同一连通分量 %s：%s（图上/实物短接）" % (fmt_key(r), "/".join(sorted(nets))))
    # 断言 C：GND 分量数符合预期（面包板层面必须 2 且只经板载地平面合并；整机层面 1）
    gcomp = comp_of_net.get("GND", set())
    if len(gcomp) != gnd_expect:
        bad += 1
        print("!! GND 连通分量数 = %d（预期 %d）" % (len(gcomp), gnd_expect))
    return bad


def main():
    _uf, net_holes, _by = build(dev_links=False)      # 面包板本身
    bad = check(_uf, net_holes, 2,
                "面包板层面（不含 ESP32 板载内部连接）")
    uf, _nh, _by2 = build(dev_links=True)            # 整机电气
    bad += check(uf, net_holes, 1,
                 "整机层面（含 ESP32 板载内部连接：4×GND / 2×3V3 / 1×5V 共面）")
    # 汇总：每个网络实际覆盖的轨组/列条，人工可核对
    print("---- 各网络节点 ----")
    for net, holes in sorted(net_holes.items()):
        print("%-5s %s" % (net, " ".join(fmt_key(h) for h in sorted(holes, key=fmt_key))))
    print("连通性校验完成：%d 处断网/短接" % bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
