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
    # VL53L0X 的 VIN/GND 线贴本体西南板身角走 0.3-1mm（针在板身下沿，物理同源）
    ("vl53l0x", 0, "vl53l0x_body"), ("vl53l0x", 1, "vl53l0x_body"),
    # OLED 4 根控制线（Rst/Clk/Data/DC）沿列间缝从 a 行竖下到 j/k 行：ESP32 板身横跨列 32–53
    # 全部宽度（80.65–137.8），目标列 35/36/45/47 都在板身正下方，**绕不开**——这是"线必过板身"
    # 的物理必然（同 TB6612 BIN 竖带先例）。缝中线离两侧孔 1.27mm，不扫孔，仅压裸板区。
    ("oled", 5, "dev_body"), ("oled", 6, "dev_body"),
    ("oled", 7, "dev_body"), ("oled", 8, "dev_body"),
    # 电源区 GPIO9 扩展线（power w6）：l46 → l31/l29 沿 y=14.60（= ESP32 板身南边界）
    # 西行 37mm。**这是唯一解，不是偷懒**：GPIO9 的网只有 l46 一个孔可用（k46 是板子自己的
    # 针脚、m46/n46/o46 全被 MAX98357A 本体挡死），从 l46 出发北上撞针脚、南下撞功放本体，
    # 只剩沿板身南缘横穿一条路——物理上就是"导线从板子底下穿过去"，与上面 TB/OLED 的
    # 竖带压板身是同一类先例。线画在板身边界线上，读作"压在板下"，不遮挡任何丝印。
    ("power", 6, "dev_body"),
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
        if comp["id"] == "vl53l0x":
            x0, y0, w, h = comp["body"]
            obs.append(("vl53l0x_body", "rect", (x0, y0, x0 + w, y0 + h)))
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
        raw = g.wire_segments(p0, w["ctrl"], p1, n=90)
    pts, s = [], 0.0
    for i, (x, y) in enumerate(raw):
        if i:
            s += math.hypot(x - raw[i - 1][0], y - raw[i - 1][1])
        pts.append((x, y, s))
    return pts


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


def bump(hits, key):
    hits[key] = hits.get(key, 0) + 1


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
            hits = {}
            for x, y, s in pts:
                near_end = s < END_OK or (total - s) < END_OK   # 沿程近端放过（落孔连接必然接触）
                for nm, (hx, hy) in holes.items():
                    if math.hypot(x - hx, y - hy) >= TOUCH:
                        continue
                    if nm in occ:
                        if not near_end:
                            bump(hits, "OCC:" + nm)
                    elif nm in blk:
                        bump(hits, "BLK:" + nm)
                for name, kind, data in obs:
                    if (comp["id"], wi, name) in WHITELIST:
                        continue
                    if near_end:
                        continue                  # 针连接必然贴板身角，物理同源
                    if kind == "circle":
                        if math.hypot(x - data[0], y - data[1]) < data[2] + 0.3:
                            bump(hits, "BODY:" + name)
                    else:
                        if data[0] - 0.3 <= x <= data[2] + 0.3 and data[1] - 0.3 <= y <= data[3] + 0.3:
                            bump(hits, "BODY:" + name)
                for cid, lab, tx, ty in tint:
                    if math.hypot(x - tx, y - ty) < 0.9 and abs(x - tx) < 1.2:
                        hits.setdefault("TXT:%s:%s" % (cid, lab), 0)
                        hits["TXT:%s:%s" % (cid, lab)] += 1
            # 本体白名单连带豁免：白名单矩形内的 blocked/occupied 孔
            wl_bodies = [d for cid, wi2, name in WHITELIST if cid == comp["id"] and wi2 == wi
                         for nm2, k, d in obs if nm2 == name]
            bad = {}
            for k, v in hits.items():
                # 注意：这里不能再判 "k in WHITELIST" —— hits 的键是 "OCC:/BODY:" 前缀字符串，
                # WHITELIST 的键是 (元件, 线序, 障碍名) 三元组，永远不相等，判了也是空转。
                # 白名单在上面采样循环里按三元组跳过，下面的 wl_bodies 负责连带豁免孔。
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
    n_short, n_cross = cross_check()
    print("净空校验完成：%d 根线有问题（白名单外）；跨线共线重叠（假短路）%d 处；交叉 %d 处"
          % (n_issue, n_short, n_cross))
    return n_issue + n_short


OVERLAP_MIN = 0.4      # 共线重叠超过 0.4mm 才算"看起来连通"


def all_segments():
    """全部元件的全部线段 -> [(元件id, 线序, 颜色类, (x0,y0), (x1,y1))]。"""
    out = []
    for comp in g.COMPONENTS:
        for wi, w in enumerate(comp["wires"]):
            pts = w["pts"] if "pts" in w else g.wire_segments(
                g.endpoint_xy(comp, w["from"]), w["ctrl"], g.endpoint_xy(comp, w["to"]), n=60)
            for a, b in zip(pts, pts[1:]):
                out.append((comp["id"], wi, w.get("cls", ""), a, b))
    return out


def cross_check():
    """跨线几何关系：共线重叠 = 假短路（严重，必须改线）；单点交叉 = 绝缘飞线互跨（可接受，仅统计）。

    为什么要单独做这一项（2026-10-19 OLED 引入时发现）：净空校验只看"线 vs 孔/本体/文字"，
    看不出两根**不同网络**的线画在同一条竖带上——图上就是一段实打实的短接，比压孔严重得多。
    """
    segs = all_segments()
    n_short, n_cross = 0, 0
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            c1, w1, k1, p, p2 = segs[i]
            c2, w2, k2, q, q2 = segs[j]
            if c1 == c2 and w1 == w2:
                continue                       # 同一根线的相邻段在拐点相接，正常
            if (c1, w1, c2, w2) in CROSS_OK:
                continue
            res = _rel(p, p2, q, q2)
            if res is None:
                continue
            kind, data = res
            if kind == "overlap":
                n_short += 1
                print("!! 假短路：%s w%d(%s) 与 %s w%d(%s) 共线重叠 %.2fmm @ %s"
                      % (c1, w1, k1, c2, w2, k2, data[0], data[1]))
            else:
                n_cross += 1
                print("   交叉：%s w%d x %s w%d @ (%.2f, %.2f)"
                      % (c1, w1, c2, w2, data[0], data[1]))
    return n_short, n_cross


# 已知且论证过的交叉（绝缘飞线互跨，电气上互不相通）
CROSS_OK = set()


def _rel(p, p2, q, q2):
    """两线段关系 -> ("overlap", (长度, 中点串)) / ("cross", (x, y)) / None。"""
    def d(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2, d3, d4 = d(p, p2, q), d(p, p2, q2), d(q, q2, p), d(q, q2, p2)
    EPS = 1e-6
    collinear = abs(d1) < EPS and abs(d2) < EPS
    if collinear:
        # 投影到较长方向求重叠长度
        vx, vy = p2[0] - p[0], p2[1] - p[1]
        L2 = vx * vx + vy * vy
        if L2 < EPS:
            return None
        t = [((a[0] - p[0]) * vx + (a[1] - p[1]) * vy) / L2 for a in (q, q2)]
        lo, hi = max(0.0, min(t)), min(1.0, max(t))
        if hi - lo <= 1e-9:
            return None
        ov = (hi - lo) * math.sqrt(L2)
        if ov < OVERLAP_MIN:
            return None
        mx = p[0] + (lo + hi) / 2.0 * vx
        my = p[1] + (lo + hi) / 2.0 * vy
        return ("overlap", (ov, "(%.2f, %.2f)" % (mx, my)))
    if d1 * d2 < 0 and d3 * d4 < 0:
        a1, b1 = p2[1] - p[1], p[0] - p2[0]
        c1 = a1 * p[0] + b1 * p[1]
        a2, b2 = q2[1] - q[1], q[0] - q2[0]
        c2 = a2 * q[0] + b2 * q[1]
        det = a1 * b2 - a2 * b1
        return ("cross", ((b2 * c1 - b1 * c2) / det, (a1 * c2 - a2 * c1) / det))
    return None


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
