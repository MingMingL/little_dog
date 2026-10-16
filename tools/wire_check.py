# -*- coding: utf-8 -*-
"""走线净空校验（一次性/复用工具）：对每个元件的每根跳线采样，检查
  1) 压占孔（occupied，除端点附近=必然连接）——最严重，电气误导
  2) 压挡孔（blocked，板身遮挡孔）——提示级
  3) 压其他元件本体（INMP 圆板 / MAX 板身 / ESP32 板身 / 喇叭符号）
  4) 压其他元件的文字标签（wire label / 针丝印）
  5) 跨元件跳线两两交叉计数（白底分层可读，仅统计；同色交叉重点看）
用法：python wire_check.py [component_id ...]（无参=全部元件）
白名单：WHITELIST 登记"已知压本体"的合规决策（如 TB 信号线压 MAX 本体左上无丝印区）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_breadboard_z as g  # noqa: E402

# (元件id, 线序号, 障碍名) 白名单：已论证"压线不压体/压孔不可避免"的合规决策
WHITELIST = {
    # TB 5 根 l 信号线 + BIN 竖带：压 MAX98357A 本体（"压线不压体"先例，丝印区已避开）
    ("tb6612fng", 0, "max_body"), ("tb6612fng", 1, "max_body"),
    ("tb6612fng", 2, "max_body"), ("tb6612fng", 3, "max_body"),
    ("tb6612fng", 4, "max_body"), ("tb6612fng", 5, "max_body"),
    ("tb6612fng", 6, "max_body"),
    # BIN 竖带上段压 ESP32 板身 USB 座/按钮之间的裸板区（无丝印，深色底橙线可读）
    ("tb6612fng", 5, "dev_body"), ("tb6612fng", 6, "dev_body"),
    # TCRT 各探头 GND/DO 线从板身内针孔出发，压自身本体一小段（针焊盘在板内，物理同源）
    ("tcrt1", 1, "tcrt1_body"), ("tcrt1", 2, "tcrt1_body"),
    ("tcrt2", 1, "tcrt2_body"), ("tcrt2", 2, "tcrt2_body"),
    ("tcrt3", 1, "tcrt3_body"), ("tcrt3", 2, "tcrt3_body"),
    ("tcrt4", 1, "tcrt4_body"),
    ("tcrt5", 1, "tcrt5_body"),
    # TCRT4 的 DO 长跳线斜穿 TCRT5 本体 6mm（TCRT5 本体该段无丝印）
    ("tcrt4", 2, "tcrt4_body"), ("tcrt4", 2, "tcrt5_body"),
    # TCRT2 的 VCC 弧线末端擦 TCRT1 本体右上角 0.6mm（物理贴板角走线）
    ("tcrt2", 0, "tcrt1_body"),
}
HOLE_R = 0.5          # 孔圆半径
WIRE_HALF = 0.5       # 线白底半宽
TOUCH = HOLE_R + WIRE_HALF + 0.05   # 视觉接触阈值 1.05
END_OK = 1.8          # 端点附近放过（落孔彩环 r=0.85 + 富余）


def all_holes():
    """全部 1660 孔 -> {name: (x, y)}（含导轨孔）。"""
    out = {}
    for prefix, rows in (("zx", g.X_ROWS), ("zy", g.Y_ROWS)):
        ty = g.X_TY if prefix == "zx" else g.Y_TY
        for c in range(1, g.N_COLS + 1):
            x = g.col_x(c)
            for i, letter in enumerate(rows):
                y = (g.ROW_Y_TOP[i] if i < 5 else g.ROW_Y_BOT[i - 5]) + ty
                out["%s.%s%d" % (prefix, letter, c)] = (x, y)
        for rname, ry in g.RAIL_YS:
            for i in range(1, 2 * g.RAIL_HOLES_PER_SIDE + 1):
                out["%s.%s_%d" % (prefix, rname, i)] = (g.rail_hole_x(i), ry + ty)
    return out


def state_sets():
    occ, blk = g.component_hole_marks()
    dev_occ, dev_blk = set(), set()
    for rk, rl in g.DEV_OCCUPIED.items():
        for c in range(g.DEV_COL_FIRST, g.DEV_COL_LAST + 1):
            dev_occ.add("%s.%s%d" % (rk, rl, c))
    dev_blk = g.dev_blocked_rail_names()
    for rk, rl in g.DEV_OCCUPIED.items():
        pass
    return (set().union(*occ.values()) | dev_occ,
            set().union(*blk.values()) | dev_blk)


def obstacles():
    """其他元件本体/符号的近似几何 -> [(name, kind, data)]。"""
    obs = [
        ("inmp_body", "circle", (156.19, 27.30, 7.5)),
        ("max_body", "rect", (114.30, 17.10, 132.08, 36.19)),
        ("dev_body", "rect", (80.65, -13.34, 137.80, 14.60)),
        ("spk_symbol", "rect", (139.60, 16.10, 143.40, 19.90)),
        ("spk_text", "rect", (143.90, 20.50, 153.00, 21.50)),
    ]
    for comp in g.COMPONENTS:
        if comp["id"].startswith("tcrt"):
            x0, y0, w, h = comp["body"]
            obs.append(("%s_body" % comp["id"], "rect", (x0, y0, x0 + w, y0 + h)))
    return obs


def wire_points(comp, w):
    """返回 [(x, y, s)]：s = 沿路径累计距离（mm），供近端判定用。"""
    raw = []
    if "pts" in w:
        for a, b in zip(w["pts"], w["pts"][1:]):
            n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.25))
            for k in range(n + (1 if b is not w["pts"][-1] else 0)):
                t = k / float(n)
                raw.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    else:
        p0 = g.endpoint_xy(comp, w["from"])
        p1 = g.endpoint_xy(comp, w["to"])
        raw = g.wire_segments(p0, g_ctrl(w), p1, n=90)
    pts, s = [], 0.0
    for i, (x, y) in enumerate(raw):
        if i:
            s += math.hypot(x - raw[i - 1][0], y - raw[i - 1][1])
        pts.append((x, y, s))
    return pts


def g_ctrl(w):
    return w["ctrl"]


def text_spots(comp_skip):
    """其他元件的文字标签位置 -> [(元件id, 文本)]（中心点近似）。"""
    spots = []
    for comp in g.COMPONENTS:
        if comp["id"] == comp_skip:
            continue
        for w in comp["wires"]:
            if not w.get("label"):
                continue
            if w["to"][0] == "hole":
                hx, hy = g.hole_xy(w["to"][1])
                spots.append((comp["id"], w["label"], hx + w.get("label_dx", 0.9), hy + w.get("label_dy", 0.55)))
        for pin in comp["pins"]:
            lab = g.pin_label(pin)
            if not lab:
                continue
            ref = g.pin_ref(pin)
            if ref[0] == "xy":
                px, py = ref[1], ref[2]
                spots.append((comp["id"], lab, px, py - 0.85))
            else:
                px, py = g.col_x(ref[2]), g.row_y(comp["board"], ref[1])
                rows = g.X_ROWS if comp["board"] == "zx" else g.Y_ROWS
                upper = rows.index(ref[1]) < 5
                spots.append((comp["id"], lab, px, py - 1.55 if upper else py + 1.95))
    return spots


def main(ids):
    holes = all_holes()
    occ, blk = state_sets()
    obs = obstacles()
    n_issue = 0
    for comp in g.COMPONENTS:
        if ids and comp["id"] not in ids:
            continue
        tint = text_spots(comp["id"])
        for wi, w in enumerate(comp["wires"]):
            pts = wire_points(comp, w)
            total = pts[-1][2]
            wl = {(comp["id"], wi, name) for name, kind, data in obs if kind == "rect"}
            hits = {}
            for idx, (x, y, s) in enumerate(pts):
                near_end = s < END_OK or (total - s) < END_OK   # 沿程近端放过（落孔连接必然接触）
                for nm, (hx, hy) in holes.items():
                    if math.hypot(x - hx, y - hy) >= TOUCH:
                        continue
                    if nm in occ:
                        if not near_end:
                            hits.setdefault("OCC:" + nm, 0)
                            hits["OCC:" + nm] += 1
                    elif nm in blk:
                        hits.setdefault("BLK:" + nm, 0)
                        hits["BLK:" + nm] += 1
                for name, kind, data in obs:
                    if (comp["id"], wi, name) in WHITELIST:
                        continue
                    if kind == "circle":
                        if math.hypot(x - data[0], y - data[1]) < data[2] + 0.3:
                            hits.setdefault("BODY:" + name, 0)
                            hits["BODY:" + name] += 1
                    else:
                        if data[0] - 0.3 <= x <= data[2] + 0.3 and data[1] - 0.3 <= y <= data[3] + 0.3:
                            hits.setdefault("BODY:" + name, 0)
                            hits["BODY:" + name] += 1
                for cid, lab, tx, ty in tint:
                    if math.hypot(x - tx, y - ty) < 0.9 and abs(x - tx) < 1.2:
                        hits.setdefault("TXT:%s:%s" % (cid, lab), 0)
                        hits["TXT:%s:%s" % (cid, lab)] += 1
            # 本体白名单连带豁免：白名单矩形内的 blocked/occupied 孔
            wl_bodies = [d for cid, wi2, name in WHITELIST if cid == comp["id"] and wi2 == wi
                         for nm2, k, d in obs if nm2 == name]
            bad = {}
            for k, v in hits.items():
                if k in WHITELIST:
                    continue
                if k.startswith("BLK:") or k.startswith("OCC:"):
                    nm = k[4:]
                    hx, hy = holes[nm]
                    if any(d[0] - 0.3 <= hx <= d[2] + 0.3 and d[1] - 0.3 <= hy <= d[3] + 0.3
                           for d in wl_bodies):
                        continue                 # 孔在本体白名单区内=压体已批准
                bad[k] = v
            if bad:
                n_issue += 1
                print("%s w%-2d %s" % (comp["id"], wi, str(sorted(hits))))
                for k in sorted(bad):
                    print("    !! %s x%d" % (k, hits[k]))
    print("净空校验完成：%d 根线有问题（白名单外）" % n_issue)
    return n_issue


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
