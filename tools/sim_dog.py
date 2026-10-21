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

V1.5 召唤仿真（SW-004，2026-10-15 新增）：
  - /api/status 增加 summon 字段对象（phase/doa_deg/energy_db/distance_mm/
    vision_used/vision_result/vision_tries/elapsed_ms/message），与固件
    ai_dog_control.cc 的 StatusHandler 一致；
  - Summon 期间 /api/mode 拒绝普通切换（{"ok":false,"error":"summon in progress"}），
    summon 不能由 App 直接切入（"summon requires wake word + passphrase"）；
  - POST /api/summon/wake 模拟"唤醒词命中"（真机上由 AFE WakeNet9 触发），
    用于无硬件时联调 App 的召唤状态页；
  - --scenario 模式：内置 S1-S5 状态机仿真（与 ai_dog_summon.cc 同构同参数），
    覆盖主流程 + 超时/丢源/口令失败/低压/避障/视觉无人都市分支，
    每条场景打印状态轨迹并给出 PASS/FAIL，无硬件也能验证抢占与降级逻辑。

用法：
  python tools/sim_dog.py 8080                                   # HTTP 模拟（原有）
  python tools/sim_dog.py --scenario happy                       # 场景回归（新增）
  python tools/sim_dog.py --list-scenarios                       # 列出场景
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOOT_TIME = time.monotonic()
STATE = {"mode": "idle"}
# moves=累计指令数；first=本轮"按住"的起始时间（用来算按住时长）；last_dir 供 /api/status 报 moving
STATS = {"moves": 0, "first": None, "last_dir": None}


def end_hold():
    """结束一轮"按住"：固件在任何停车/换模式时都会停电机，计时起点随之作废"""
    STATS["first"] = None
    STATS["last_dir"] = "stop"

# ---------------------------------------------------------------------------
# V1.5 召唤仿真（SW-004）——参数与 main/Kconfig.projbuild 的 AI Dog Summon 菜单一致
# ---------------------------------------------------------------------------

SUMMON_CFG = {
    "command_words": ["guo lai", "dao zhe li lai"],  # AI_DOG_SUMMON_COMMAND_WORDS
    "command_timeout_ms": 3000,     # AI_DOG_SUMMON_COMMAND_TIMEOUT_MS
    "listen_ms": 500,               # AI_DOG_SUMMON_LISTEN_MS
    "step_ms": 1500,                # AI_DOG_SUMMON_STEP_MS
    "cruise_duty": 35,              # AI_DOG_SUMMON_CRUISE_DUTY
    "align_max_ms": 3000,           # AI_DOG_SUMMON_ALIGN_MAX_MS
    "total_timeout_ms": 60000,      # AI_DOG_SUMMON_TOTAL_TIMEOUT_MS
    "deadzone_deg": 12,             # AI_DOG_DOA_DEADZONE_DEG
    "stable_deg": 25,               # AI_DOG_DOA_STABLE_DEG
    "near_energy_db": -30,          # AI_DOG_SUMMON_NEAR_ENERGY_DB
    "obstacle_mm": 400,             # AI_DOG_VL53L0X_THRESHOLD_MM
    "approach_mm": 1000,            # AI_DOG_SUMMON_APPROACH_STOP_MM
    "vision_max_tries": 3,          # AI_DOG_VISION_MAX_TRIES
    "low_battery_pct": 20,          # AI_DOG_SUMMON_LOW_BATTERY_PCT
    "guard_allow": True,            # AI_DOG_SUMMON_GUARD_ALLOW
}

# 阶段与固件 AiDogSummon::Phase 一致（字符串即协议字段）
PHASES = ["idle", "verifying", "listening", "aligning", "moving", "avoiding",
          "vision", "arrived", "rescan", "finishing"]

SIM_VISION = {"enabled": True}  # 模拟器摄像头开关

SUMMON = {
    "phase": "idle",
    "doa_deg": 0.0,
    "energy_db": -100.0,
    "distance_mm": -1,
    "vision_used": False,
    "vision_result": -1,
    "vision_tries": 0,
    "elapsed_ms": 0,
    "message": "",
}


class SummonSim:
    """S1-S5 状态机仿真：与 ai_dog_summon.cc 同构（停-听-走 + 抢占 + 降级）。

    传感器由场景脚本注入（doa/energy/distance/vision），执行器动作用日志表示。
    tick = 50ms，与固件 kTickMs 一致。
    """

    def __init__(self, sensors=None, log=print):
        self.cfg = dict(SUMMON_CFG)
        self.phase = "idle"
        self.sub_step = 0
        self.sub_deadline_ms = 0.0
        self.now_ms = 0.0
        self.start_ms = None
        self.vision_tries = 0
        self.rescan_count = 0
        self.consecutive_lost = 0
        self.last_angle_error = 0.0
        self.low_battery_announced = False
        self.battery = 85
        self.mode = STATE["mode"]
        self.trace = [(0.0, "idle", "")]  # (t_ms, phase, note)
        self.sensors = sensors or {}
        self.log = log
        self.actions = []        # 执行器动作（电机/提示），供断言
        self.done = False
        # 运动学模型：狗头朝向（度，0=初始朝向）与到声源距离（mm）。
        # 点动/巡航会改变它们，DOA/测程按模型输出——这样仿真能真正验证
        # 死区对准、步进接近与接近段触发，而不是恒值传感器。
        self.heading_deg = 0.0
        self.source_az_deg = 0.0     # 声源绝对方位（场景设定）
        self.distance_mm = 2500      # 到声源距离
        self.nudge_deg = 15.0        # 60ms 点动 ≈ 15°（标定值，M2 实测回写）

    # ---- 传感器注入接口 ----
    def sensor(self, name, default):
        v = self.sensors.get(name, default)
        if callable(v):
            return v(self.now_ms, self)
        return v

    def t(self):
        return self.now_ms

    # ---- 内部 ----
    def set_phase(self, phase, note=""):
        if self.phase != phase:
            self.log("[%7.1fms] %s -> %s  %s" % (self.now_ms, self.phase, phase, note))
            self.trace.append((self.now_ms, phase, note))
        self.phase = phase

    def doa_estimate(self):
        """返回 (valid, azimuth_deg, energy_db, quality)。

        默认按运动学模型：azimuth = (声源绝对方位 - 狗朝向) mod 360；
        场景可直接注入 doa_deg/doa_valid 覆盖（测丢源等分支）。
        """
        valid = self.sensor("doa_valid", True)
        if "doa_deg" in self.sensors:
            az = float(self.sensor("doa_deg", 0.0))
        else:
            az = (self.source_az_deg - self.heading_deg) % 360.0
        if "energy_db" in self.sensors:
            energy = float(self.sensor("energy_db", -45.0))
        else:
            # 能量随距离衰减（自由场近似：距离每翻倍 -6dB，基准 300mm/-45dB）
            import math
            energy = -45.0 - 6.0 * math.log2(max(self.distance_mm, 300.0) / 300.0)
        quality = float(self.sensor("doa_quality", 6.0))
        return valid, az, energy, quality

    def motor(self, what):
        self.actions.append((round(self.now_ms, 1), what))
        self.log("[%7.1fms]   motor: %s" % (self.now_ms, what))
        # 运动学：点动改朝向，巡航缩距离
        if what.startswith("nudge_right"):
            self.heading_deg = (self.heading_deg + self.nudge_deg) % 360.0
        elif what.startswith("nudge_left"):
            self.heading_deg = (self.heading_deg - self.nudge_deg) % 360.0
        elif what.startswith("cruise"):
            self.distance_mm = max(200, self.distance_mm - 300)

    # ---- 入口 ----
    def wake_word(self):
        if self.phase != "idle":
            return
        if self.battery < self.cfg["low_battery_pct"]:
            self.log("[%7.1fms] 唤醒词命中但电量 %d%% < %d%%：拒绝（Q-11 预检）"
                     % (self.now_ms, self.battery, self.cfg["low_battery_pct"]))
            return
        if self.mode == "guard" and not self.cfg["guard_allow"]:
            self.log("[%7.1fms] 看门时段禁用召唤（Q-08）：忽略" % self.now_ms)
            return
        self.log("[%7.1fms] 唤醒词命中：口令验证窗口 %dms"
                 % (self.now_ms, self.cfg["command_timeout_ms"]))
        self.set_phase("verifying", "验证口令")
        SUMMON.update({"phase": "verifying", "message": "验证口令"})

    def passphrase(self, word):
        """口令验证结果（场景直接给定真值）"""
        if self.phase != "verifying":
            return
        if word in self.cfg["command_words"]:
            self.log("[%7.1fms] 口令通过（%s）：进入 Summon，抢占 %s"
                     % (self.now_ms, word, self.mode))
            self.mode = "summon"
            STATE["mode"] = "summon"
            self.start_ms = self.now_ms
            self.rescan_count = 0
            self.set_phase("listening", "找声音")
            SUMMON.update({"phase": "listening", "message": "找声音"})
        else:
            self.log("[%7.1fms] 口令未通过（%s）：回监听，走原聊天链路（Q-02）"
                     % (self.now_ms, word or "超时"))
            self.set_phase("idle", "没听清")
            SUMMON.update({"phase": "idle", "message": "没听清"})

    def stop(self):
        """急停：任何阶段立即回 idle（模式也回 Idle，与 ControlServer::RequestEmergencyStop 一致）"""
        self.log("[%7.1fms] 急停：召唤中止" % self.now_ms)
        self.motor("stop")
        self.mode = "idle"
        STATE["mode"] = "idle"
        self.set_phase("idle", "已停止")
        SUMMON.update({"phase": "idle", "message": "已停止"})

    # ---- 主循环 ----
    def tick(self, dt_ms=50):
        self.now_ms += dt_ms
        SUMMON["elapsed_ms"] = int(self.now_ms - self.start_ms) if self.start_ms is not None else 0

        if self.phase == "idle":
            self.sub_step = 0
            return

        # 全局安全闸（S1 期间不检：模式还是原模式，且不动）
        if self.phase != "verifying" and self.mode != "summon":
            self.log("[%7.1fms] 模式变为 %s：召唤中止（Q-09 不自动恢复）" % (self.now_ms, self.mode))
            self.motor("stop")
            self.set_phase("idle", "已停止")
            return
        if self.battery < self.cfg["low_battery_pct"]:
            self.log("[%7.1fms] 过程中低压 %d%%：停车回 Idle 并播报（Q-11）"
                     % (self.now_ms, self.battery))
            self.motor("stop")
            self.mode = "idle"
            STATE["mode"] = "idle"
            self.set_phase("idle", "电量不足")
            return
        if SUMMON["elapsed_ms"] > self.cfg["total_timeout_ms"] and self.phase != "rescan":
            # 注意 != rescan：重搜阶段有自己的 8s 预算，不能被总时限
            # 每个 tick 反复打回重搜入口（此处与固件同构）。
            self.log("[%7.1fms] 召唤超时：进入 S5 重搜" % self.now_ms)
            self.set_phase("rescan", "再找找")
            self.sub_step = 0
            return

        if self.phase == "verifying":
            # 口令真值由场景在指定时刻调用 passphrase()；这里只兜底超时
            if self.now_ms - (self.start_ms or 0) > 0 and self.sensor("verify_timeout", False):
                self.passphrase("")
            return

        if self.phase == "listening":
            if self.sub_step == 0:
                self.motor("stop")
                self.sub_deadline_ms = self.now_ms + self.cfg["listen_ms"]
                self.sub_step = 1
            elif self.now_ms >= self.sub_deadline_ms:
                valid, az, energy, quality = self.doa_estimate()
                SUMMON.update({"doa_deg": az, "energy_db": energy})
                if not valid:
                    self.consecutive_lost += 1
                    self.log("[%7.1fms] 监听窗无有效 DOA（%d/3）" % (self.now_ms, self.consecutive_lost))
                    if self.consecutive_lost >= 3:
                        self.set_phase("rescan", "声源丢了")
                        self.sub_step = 0
                        return
                    self.motor("nudge_right %dms" % (self.cfg.get("nudge_ms", 60) * 4))
                    self.sub_step = 0
                    return
                self.consecutive_lost = 0
                err = az if az <= 180.0 else az - 360.0
                self.last_angle_error = err
                self.log("[%7.1fms] 监听窗：DOA=%.1f° 误差%+.1f° 能量=%.1fdB"
                         % (self.now_ms, az, err, energy))
                if abs(err) <= self.cfg["deadzone_deg"]:
                    self.set_phase("moving", "前进")
                else:
                    self.set_phase("aligning", "对准中")
                self.sub_step = 0
            return

        if self.phase == "aligning":
            if self.sub_step == 0:
                self.sub_deadline_ms = self.now_ms + self.cfg["align_max_ms"]
                self.sub_step = 1
            valid, az, energy, quality = self.doa_estimate()
            err = (az if az <= 180.0 else az - 360.0) if valid else 0.0
            if valid and abs(err) <= self.cfg["deadzone_deg"]:
                self.log("[%7.1fms] 对准完成：误差%+.1f°" % (self.now_ms, err))
                self.set_phase("moving", "前进")
                self.sub_step = 0
                return
            if self.now_ms >= self.sub_deadline_ms:
                self.log("[%7.1fms] 对准超时：按当前角度前进" % self.now_ms)
                self.set_phase("moving", "前进")
                self.sub_step = 0
                return
            self.motor("nudge_%s %dms" % ("right" if err >= 0 else "left", 60))
            return

        if self.phase == "moving":
            if self.sub_step == 0:
                self.sub_deadline_ms = self.now_ms + self.cfg["step_ms"]
                self.motor("cruise %d%%" % self.cfg["cruise_duty"])
                self.sub_step = 1
            if "distance_mm" in self.sensors:
                dist = int(self.sensor("distance_mm", 2000))
            else:
                dist = int(self.distance_mm)
            SUMMON["distance_mm"] = dist
            if dist < self.cfg["obstacle_mm"]:
                self.log("[%7.1fms] 避障：前向 %dmm < %dmm，重定向"
                         % (self.now_ms, dist, self.cfg["obstacle_mm"]))
                self.motor("stop")
                self.set_phase("avoiding", "绕一下")
                self.sub_step = 0
                return
            if self.now_ms >= self.sub_deadline_ms:
                self.motor("stop")
                if self.approach_triggered():
                    self.set_phase("vision", "看看是不是你")
                else:
                    self.set_phase("listening", "听听")
                self.sub_step = 0
            return

        if self.phase == "avoiding":
            if self.sub_step == 0:
                self.sub_deadline_ms = self.now_ms + 400
                self.motor("nudge_right 60ms")
                self.sub_step = 1
            elif self.now_ms >= self.sub_deadline_ms:
                self.motor("stop")
                self.set_phase("listening", "重新听")
                self.sub_step = 0
            return

        if self.phase == "vision":
            self.vision_check()
            return

        if self.phase == "arrived":
            if self.sub_step == 0:
                self.motor("stop")
                self.log("[%7.1fms] S4 到达：狗叫/问候 → 自动切 Chat（AT-S06）" % self.now_ms)
                self.actions.append((round(self.now_ms, 1), "bark+greet"))
                self.sub_deadline_ms = self.now_ms + 2500
                self.sub_step = 1
            elif self.now_ms >= self.sub_deadline_ms:
                self.mode = "chat"
                STATE["mode"] = "chat"
                self.set_phase("idle", "来啦")
                self.sub_step = 0
                self.done = True
            return

        if self.phase == "rescan":
            if self.sub_step == 0:
                self.sub_deadline_ms = self.now_ms + 8000
                self.sub_step = 1
                self.rescan_count += 1
                self.log("[%7.1fms] S5 重搜（第 %d 次，上限 2）：原地 360° 扫描"
                         % (self.now_ms, self.rescan_count))
            if self.now_ms % 150 < 50:
                self.motor("nudge_right 60ms")
            valid, az, energy, quality = self.doa_estimate()
            last_chance = self.rescan_count >= 2
            if valid and quality >= 3 and not last_chance:
                self.log("[%7.1fms] S5 命中：DOA=%.1f° → 回 S2" % (self.now_ms, az))
                self.set_phase("listening", "找到了")
                self.sub_step = 0
                return
            if self.now_ms >= self.sub_deadline_ms:
                self.log("[%7.1fms] S5 失败（第 %d 次）：回 Idle，App 提示\"没找到你\"（AT-S08）"
                         % (self.now_ms, self.rescan_count))
                self.motor("stop")
                self.mode = "idle"
                STATE["mode"] = "idle"
                self.set_phase("idle", "没找到你")
                self.done = True
            return

    def approach_triggered(self):
        _, _, energy, _ = self.doa_estimate()
        if "distance_mm" in self.sensors:
            dist = int(self.sensor("distance_mm", 2000))
        else:
            dist = int(self.distance_mm)
        if energy >= self.cfg["near_energy_db"] or dist < self.cfg["approach_mm"]:
            self.log("[%7.1fms] 接近段触发：能量=%.1fdB 测距=%dmm"
                     % (self.now_ms, energy, dist))
            return True
        return False

    def vision_check(self):
        if self.sub_step == 0:
            self.vision_tries += 1
            SUMMON["vision_used"] = True
            SUMMON["vision_tries"] = self.vision_tries
            self.sub_step = 1
        result = int(self.sensor("vision_result", 0))
        SUMMON["vision_result"] = result
        if result == 1:
            self.log("[%7.1fms] 视觉确认：有人形 → S4 到达" % self.now_ms)
            self.set_phase("arrived", "看到你啦")
            self.sub_step = 0
            return
        if self.vision_tries >= self.cfg["vision_max_tries"]:
            self.log("[%7.1fms] 视觉 %d 次无人形 → S5 降级" % (self.now_ms, self.vision_tries))
            self.set_phase("rescan", "没看到人")
            self.sub_step = 0
            return
        self.log("[%7.1fms] 视觉无人形（%d）：回 S2 再接近" % (self.now_ms, result))
        self.set_phase("listening", "再听听")
        self.sub_step = 0


# ---------------------------------------------------------------------------
# 场景回归（SW-004 DoD：S1-S5 主流程 + 超时/丢源/口令失败分支）
# ---------------------------------------------------------------------------

def scenario_happy():
    """主流程：唤醒 → 口令通过 → 对准 → 前进 → 视觉有人 → 到达切 Chat"""
    sim = SummonSim({"vision_result": 1})
    sim.source_az_deg = 60.0      # 声源在右前方 60°
    sim.distance_mm = 2500        # 起步 2.5m
    sim.wake_word()
    sim.passphrase("guo lai")
    # 跑 120s 或到 done
    for _ in range(2400):
        if sim.done:
            break
        sim.tick()
    return sim, ["verifying", "aligning", "moving", "vision", "arrived", "idle"], sim.mode == "chat"


def scenario_wrong_passphrase():
    """口令失败：唤醒 → 口令未命中 → 回 Idle（原聊天链路继续）"""
    sim = SummonSim({"doa_valid": True})
    sim.wake_word()
    sim.passphrase("nihao")   # 不在词表
    for _ in range(200):
        if sim.done or sim.phase == "idle":
            break
        sim.tick()
    return sim, ["verifying", "idle"], sim.mode != "summon"


def scenario_verify_timeout():
    """口令超时：唤醒后不说话 → 3s 超时回 Idle"""
    sim = SummonSim({"verify_timeout": True})
    sim.wake_word()
    for _ in range(200):
        if sim.done or sim.phase == "idle":
            break
        sim.tick()
    return sim, ["verifying", "idle"], sim.mode != "summon"


def scenario_lost_source():
    """丢源：口令通过后一直没有有效 DOA → S5 重搜 → 回 Idle"""
    sim = SummonSim({"doa_valid": False})
    sim.wake_word()
    sim.passphrase("dao zhe li lai")
    for _ in range(2400):
        if sim.done:
            break
        sim.tick()
    return sim, ["listening", "rescan", "idle"], sim.mode != "summon"


def scenario_obstacle():
    """避障：前进途中测距 < 400mm → 重定向后继续"""
    def dist_fn(t, sim):
        # 前 1s 正常，1-2s 进入 300mm（障碍），绕行后接近到 900mm
        if 1000 < t < 2000:
            return 300
        if t >= 2000:
            return 900
        return 2500

    sim = SummonSim({"distance_mm": dist_fn, "vision_result": 1})
    sim.source_az_deg = 0.0
    sim.distance_mm = 2500
    sim.wake_word()
    sim.passphrase("guo lai")
    for _ in range(2400):
        if sim.done:
            break
        sim.tick()
    return sim, ["moving", "avoiding", "vision", "arrived", "idle"], sim.mode == "chat"


def scenario_vision_no_human():
    """视觉三次无人形 → S5 降级（R-F6 之前的正常路径）"""
    sim = SummonSim({"vision_result": 0, "distance_mm": 900})
    sim.source_az_deg = 0.0
    sim.distance_mm = 900        # 已在接近段
    sim.wake_word()
    sim.passphrase("guo lai")
    for _ in range(2400):
        if sim.done:
            break
        sim.tick()
    return sim, ["vision", "rescan", "idle"], sim.mode != "summon"


def scenario_low_battery():
    """低压：进入前电量 15% < 20% → 拒绝进入"""
    sim = SummonSim({})
    sim.battery = 15
    sim.wake_word()
    ok = sim.phase == "idle"
    return sim, ["idle"], ok


def scenario_guard_block():
    """看门禁用（Q-08 关）：guard 模式下唤醒词被忽略"""
    sim = SummonSim({})
    sim.cfg["guard_allow"] = False
    sim.mode = "guard"
    STATE["mode"] = "guard"
    sim.wake_word()
    ok = sim.phase == "idle"
    STATE["mode"] = "idle"
    return sim, ["idle"], ok


def scenario_preempt_remote():
    """抢占（AT-S05）：remote 模式中被唤醒词打断，≤2s 进入验证"""
    sim = SummonSim({"doa_deg": 0.0, "vision_result": 1})
    sim.mode = "remote"
    STATE["mode"] = "remote"
    sim.wake_word()
    t0 = sim.now_ms
    entered = False
    for _ in range(100):
        sim.tick()
        if sim.phase == "verifying":
            entered = True
            break
    ok = entered and (sim.now_ms - t0) <= 2000
    STATE["mode"] = "idle"
    return sim, ["verifying"], ok


def scenario_emergency_stop():
    """急停始终有效（AT-S05）：summon 进行中急停 → 立即回 Idle"""
    sim = SummonSim({"vision_result": 1})
    sim.source_az_deg = 45.0
    sim.distance_mm = 2500
    sim.wake_word()
    sim.passphrase("guo lai")
    for _ in range(40):
        sim.tick()
        if sim.phase in ("aligning", "moving", "listening"):
            break
    sim.stop()
    ok = sim.phase == "idle" and sim.mode != "summon"
    return sim, ["idle"], ok


def scenario_timeout():
    """总时限 60s：一直有 DOA 但到不了（距离恒远）→ 超时进 S5"""
    sim = SummonSim({"doa_deg": 10.0, "distance_mm": 5000, "vision_result": 0})
    sim.wake_word()
    sim.passphrase("guo lai")
    for _ in range(3000):
        if sim.done:
            break
        sim.tick()
    return sim, ["rescan", "idle"], sim.mode != "summon"


SCENARIOS = {
    "happy": ("主流程：唤醒+口令→寻声→视觉有人→到达切Chat", scenario_happy),
    "wrong-passphrase": ("口令失败：未命中回 Idle", scenario_wrong_passphrase),
    "verify-timeout": ("口令超时：3s 未说话回 Idle", scenario_verify_timeout),
    "lost-source": ("丢源：无有效 DOA → S5 重搜 → Idle", scenario_lost_source),
    "obstacle": ("避障：测距<400mm 重定向后继续", scenario_obstacle),
    "vision-no-human": ("视觉 3 次无人形 → S5 降级", scenario_vision_no_human),
    "low-battery": ("低压预检：拒绝进入（Q-11）", scenario_low_battery),
    "guard-block": ("看门禁用召唤：忽略唤醒词（Q-08）", scenario_guard_block),
    "preempt-remote": ("抢占 remote：≤2s 进入验证（AT-S05）", scenario_preempt_remote),
    "emergency-stop": ("急停始终有效（AT-S05）", scenario_emergency_stop),
    "timeout": ("总时限 60s 超时 → S5（AT-S04）", scenario_timeout),
}


def run_scenarios(only=None):
    passed = failed = 0
    for name, (desc, fn) in SCENARIOS.items():
        if only and name not in only:
            continue
        print("=" * 72)
        print("场景 %-18s %s" % (name, desc))
        print("-" * 72)
        sim, expect_phases, ok = fn()
        seen = []
        for _, p, _ in sim.trace:
            if not seen or seen[-1] != p:
                seen.append(p)
        for want in expect_phases:
            if want not in seen:
                ok = False
                print("  !! 期望阶段缺失: %s" % want)
        verdict = "PASS" if ok else "FAIL"
        print("-" * 72)
        print("场景 %-18s %s（轨迹: %s）" % (name, verdict, " → ".join(seen)))
        if ok:
            passed += 1
        else:
            failed += 1
    print("=" * 72)
    print("场景回归：%d PASS / %d FAIL" % (passed, failed))
    return failed == 0


# ---------------------------------------------------------------------------
# HTTP 模拟（原有行为 + summon 扩展）
# ---------------------------------------------------------------------------

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
            resp = {
                "mode": STATE["mode"],
                "moving": STATE["mode"] == "remote" and STATS["last_dir"] not in (None, "stop"),
                "battery": -1,
                "charging": False,
                "wifi": {"ssid": "SimDog", "rssi": -55},
                "fault": None,
                "uptime": int(time.monotonic() - BOOT_TIME),
            }
            # V1.5：summon 进行中（或 summon 模式）才带 summon 字段，与固件一致
            if STATE["mode"] == "summon" or SUMMON["phase"] != "idle":
                resp["summon"] = dict(SUMMON)
            self._json(resp)
            return
        self._json({"ok": False, "error": "unknown path"}, 404)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                return None, "invalid json"
            return data, None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid json"

    def do_POST(self):
        data, err = self._body()
        if err:
            self._json({"ok": False, "error": err})
            return

        if self.path.startswith("/api/mode"):
            mode = data.get("mode")
            if mode not in ("idle", "chat", "patrol", "guard", "remote", "summon"):
                self._json({"ok": False, "error": "invalid mode"})
                return
            # 与固件 ai_dog_control.cc ModeHandler 一致：
            # summon 进行中拒绝普通切换；summon 不能由 App 直接切入
            if STATE["mode"] == "summon" and mode != "summon":
                print("[mode] 拒绝 %s（summon in progress）" % mode, flush=True)
                self._json({"ok": False, "error": "summon in progress"})
                return
            if STATE["mode"] != "summon" and mode == "summon":
                print("[mode] 拒绝 summon（requires wake word + passphrase）", flush=True)
                self._json({"ok": False, "error": "summon requires wake word + passphrase"})
                return
            if mode != STATE["mode"]:
                # 切模式时固件会停车；再次进入 remote 不能沿用上一轮的 moving 状态。
                end_hold()
            STATE["mode"] = mode
            print("[mode] -> %s" % mode, flush=True)
            self._json({"ok": True, "mode": mode})
            return

        if self.path.startswith("/api/stop"):
            STATE["mode"] = "idle"
            end_hold()
            SUMMON.update({"phase": "idle", "message": "已停止"})
            print("[stop] 急停：回到 idle（召唤中止）", flush=True)
            self._json({"ok": True})
            return

        if self.path.startswith("/api/summon/wake"):
            # V1.5：模拟唤醒词命中（真机由 AFE WakeNet9 触发），
            # 打开 S1 口令验证窗口。App 联调召唤状态页用。
            if STATE["mode"] == "summon" or SUMMON["phase"] != "idle":
                self._json({"ok": False, "error": "already in summon"})
                return
            SUMMON.update({"phase": "verifying", "message": "验证口令",
                           "elapsed_ms": 0, "vision_tries": 0, "vision_result": -1})
            print("[summon] 唤醒词命中（模拟）：口令验证窗口 %dms"
                  % SUMMON_CFG["command_timeout_ms"], flush=True)
            self._json({"ok": True, "phase": "verifying"})
            return

        if self.path.startswith("/api/summon/passphrase"):
            # 模拟口令结果：{"word": "guo lai"} 通过，其他未通过
            word = data.get("word", "")
            if word in SUMMON_CFG["command_words"]:
                STATE["mode"] = "summon"
                SUMMON.update({"phase": "listening", "message": "找声音"})
                print("[summon] 口令通过（%s）：进入 Summon" % word, flush=True)
                self._json({"ok": True, "phase": "listening"})
            else:
                SUMMON.update({"phase": "idle", "message": "没听清"})
                print("[summon] 口令未通过（%s）：回监听" % (word or "超时"), flush=True)
                self._json({"ok": True, "phase": "idle"})
            return

        if self.path.startswith("/api/summon/vision"):
            # 摄像头开关（与固件 /api/summon/vision 一致）；
            # 模拟器始终拥有“摄像头”（available=true）。
            if self.command == "POST":
                SIM_VISION["enabled"] = bool(data.get("enabled", False))
                print("[summon] 摄像头开关 -> %s" % SIM_VISION["enabled"], flush=True)
                self._json({"ok": True, "enabled": SIM_VISION["enabled"]})
            else:
                self._json({"ok": True, "available": True, "enabled": SIM_VISION["enabled"]})
            return

        if self.path.startswith("/api/summon/step"):
            # 手动推进召唤阶段（无硬件联调用）：推进到指定阶段
            phase = data.get("phase")
            if phase in PHASES and phase != "idle":
                STATE["mode"] = "summon"
                SUMMON.update({"phase": phase, "message": phase})
                if phase == "vision":
                    SUMMON.update({"doa_deg": 5.0, "energy_db": -25.0, "distance_mm": 900})
                print("[summon] 手动阶段 -> %s" % phase, flush=True)
                self._json({"ok": True, "phase": phase})
                return
            self._json({"ok": False, "error": "invalid phase"})
            return

        if self.path.startswith("/api/move"):
            d = data.get("dir")
            speed = data.get("speed", 60)
            valid = ("stop", "forward", "backward", "left", "right",
                     "rotate_left", "rotate_right")
            if d not in valid:
                self._json({"ok": False, "error": "invalid dir"})
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
            STATS["last_dir"] = d
            hold = now - STATS["first"]
            if d == "stop":
                end_hold()
            print("[move] #%-3d %-13s speed=%-3s  按住 %.2fs" % (STATS["moves"], d, speed, hold), flush=True)
            self._json({"ok": True})
            return

        self._json({"ok": False, "error": "unknown path"}, 404)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list-scenarios" in args:
        print("内置场景（SW-004 Summon 仿真）：")
        for name, (desc, _) in SCENARIOS.items():
            print("  %-18s %s" % (name, desc))
        sys.exit(0)
    if "--scenario" in args:
        i = args.index("--scenario")
        only = args[i + 1].split(",") if i + 1 < len(args) else None
        unknown = [s for s in (only or []) if s not in SCENARIOS]
        if unknown:
            print("未知场景: %s（--list-scenarios 查看）" % ", ".join(unknown))
            sys.exit(2)
        sys.exit(0 if run_scenarios(only) else 1)
    if args and args[0].isdigit():
        port = int(args[0])
    else:
        port = 8080
    print("AiDog 设备模拟器监听 127.0.0.1:%d" % port, flush=True)
    print("召唤联调端点：POST /api/summon/wake → /api/summon/passphrase {\"word\":\"guo lai\"}"
          " → /api/summon/step {\"phase\":\"vision\"}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
