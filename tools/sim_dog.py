#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AiDog 设备端模拟器 —— 只用于在真机上验证 App 遥控链路（无硬件时）。

实现 docs/Android控制端设计.md 第 2 节的接口：
  GET  /api/status
  POST /api/mode      {"mode": "idle|chat|patrol|guard|remote"}
  POST /api/move      {"dir": "...", "speed": 0..100}
  POST /api/stop

与真机固件一致的关键行为：
  - /api/move 只在 remote 模式被接受，否则回 {"ok":false,"error":"not in remote mode"}
  - 每条 move 都打到日志，方便和 App 的"按住 10Hz 发送"对账
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"mode": "idle"}
STATS = {"moves": 0, "first": None, "last": None, "last_dir": None, "last_speed": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音默认日志，自己控制
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self._json({
                "mode": STATE["mode"],
                "moving": STATE["mode"] == "remote" and STATS["last_dir"] not in (None, "stop"),
                "battery": -1,
                "charging": False,
                "wifi": {"ssid": "SimDog", "rssi": -55},
                "fault": None,
                "uptime": int(time.time()),
            })
            return
        self._json({"ok": False, "error": "unknown path"}, 404)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception:
            return None, "invalid json"

    def do_POST(self):
        data, err = self._body()
        if err:
            self._json({"ok": False, "error": err})
            return

        if self.path.startswith("/api/mode"):
            mode = data.get("mode")
            if mode not in ("idle", "chat", "patrol", "guard", "remote"):
                self._json({"ok": False, "error": "invalid mode"})
                return
            STATE["mode"] = mode
            if mode != "remote":
                STATS["moves"] = STATS.get("moves", 0)
            print("[mode] -> %s" % mode, flush=True)
            self._json({"ok": True, "mode": mode})
            return

        if self.path.startswith("/api/stop"):
            STATE["mode"] = "idle"
            STATS["last_dir"] = "stop"
            print("[stop] 急停：回到 idle", flush=True)
            self._json({"ok": True})
            return

        if self.path.startswith("/api/move"):
            d = data.get("dir")
            speed = data.get("speed", 60)
            valid = ("stop", "forward", "backward", "left", "right",
                     "rotate_left", "rotate_right")
            if d not in valid:
                self._json({"ok": False, "error": "invalid dir"}, )
                return
            if STATE["mode"] != "remote":
                # 和真机一致：非遥控模式拒绝，让 App 弹提示
                print("[move] 拒绝 %s speed=%s（当前 %s）" % (d, speed, STATE["mode"]), flush=True)
                self._json({"ok": False, "error": "not in remote mode"})
                return
            STATS["moves"] += 1
            now = time.time()
            if STATS["first"] is None:
                STATS["first"] = now
            STATS["last"] = now
            STATS["last_dir"] = d
            STATS["last_speed"] = speed
            hold = (now - STATS["first"]) if STATS["moves"] > 1 else 0.0
            print("[move] #%-3d %-13s speed=%-3s  按住 %.2fs" % (STATS["moves"], d, speed, hold), flush=True)
            self._json({"ok": True})
            return

        self._json({"ok": False, "error": "unknown path"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print("AiDog 设备模拟器监听 127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
