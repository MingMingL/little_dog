package com.aidog.control.model;

import com.google.gson.annotations.SerializedName;

/**
 * 设备状态，字段与设备端 GET /api/status 的 JSON 一一对应。
 * 见 docs/Android控制端设计.md 第 2 节。
 */
public class DeviceStatus {

    /** idle | chat | patrol | guard | remote | summon（V1.5 起） */
    @SerializedName("mode")
    public String mode;

    @SerializedName("moving")
    public boolean moving;

    /** 电量百分比；-1 表示设备无电池采样 */
    @SerializedName("battery")
    public int battery = -1;

    @SerializedName("charging")
    public boolean charging;

    @SerializedName("wifi")
    public WifiInfo wifi;

    /** null 表示无故障 */
    @SerializedName("fault")
    public String fault;

    /** 运行时长（秒） */
    @SerializedName("uptime")
    public long uptime;

    /**
     * V1.5 召唤状态（SW-401）。只在召唤进行中（或 summon 模式）出现，字段与
     * 设备端 ai_dog_control.cc StatusHandler 的 summon 对象一致：
     * phase / doa_deg / energy_db / distance_mm / vision_used /
     * vision_result / vision_tries / elapsed_ms / message。
     */
    @SerializedName("summon")
    public SummonInfo summon;

    public static class WifiInfo {
        @SerializedName("ssid")
        public String ssid;

        @SerializedName("rssi")
        public int rssi;
    }

    /** 召唤阶段（与设备端 AiDogSummon::PhaseToString 一致） */
    public static class SummonInfo {
        /** idle | verifying | listening | aligning | moving | avoiding | vision | arrived | rescan | finishing */
        @SerializedName("phase")
        public String phase;

        /** DOA 方位角 0..360（+Y=0° 顺时针） */
        @SerializedName("doa_deg")
        public float doaDeg;

        /** 全带能量 dBFS */
        @SerializedName("energy_db")
        public float energyDb;

        /** 前向测程 mm，-1 无效 */
        @SerializedName("distance_mm")
        public int distanceMm;

        @SerializedName("vision_used")
        public boolean visionUsed;

        /** -1 未用 / 0 无人形 / 1 有人形 / 2 超时失败 */
        @SerializedName("vision_result")
        public int visionResult;

        @SerializedName("vision_tries")
        public int visionTries;

        @SerializedName("elapsed_ms")
        public int elapsedMs;

        @SerializedName("message")
        public String message;

        public String phaseLabel() {
            if (phase == null) return "";
            switch (phase) {
                case "verifying": return "验证口令";
                case "listening": return "听声音";
                case "aligning":  return "对准方向";
                case "moving":    return "接近中";
                case "avoiding":  return "避障绕行";
                case "vision":    return "视觉确认";
                case "arrived":   return "到达";
                case "rescan":    return "重搜";
                case "finishing": return "收尾";
                default:          return phase;
            }
        }

        /** 能量条进度 0..100（-80..0 dBFS 映射） */
        public int energyPercent() {
            int pct = (int) ((energyDb + 80f) / 80f * 100f);
            return Math.max(0, Math.min(100, pct));
        }

        public String distanceLabel() {
            if (distanceMm < 0) return "—";
            if (distanceMm < 1000) return distanceMm + "mm";
            return String.format("%.2fm", distanceMm / 1000f);
        }

        public String visionLabel() {
            switch (visionResult) {
                case 0:  return "无人形";
                case 1:  return "有人形 ✓";
                case 2:  return "超时";
                default: return visionUsed ? "确认中…" : "未启用";
            }
        }
    }

    /** 是否在召唤流程中（用于模式按钮置灰） */
    public boolean isSummoning() {
        return "summon".equals(mode) || (summon != null && summon.phase != null
                && !"idle".equals(summon.phase));
    }

    /** 转成界面上显示的中文模式名 */
    public String modeLabel() {
        if (mode == null) return "未知";
        switch (mode) {
            case "idle":   return "待机";
            case "chat":   return "聊天";
            case "patrol": return "巡逻";
            case "guard":  return "看门";
            case "remote": return "遥控";
            case "summon": return "召唤";
            default:       return mode;
        }
    }

    public String batteryLabel() {
        if (battery < 0) return "无采样";
        return battery + "%" + (charging ? "（充电中）" : "");
    }

    public String wifiLabel() {
        if (wifi == null || wifi.ssid == null) return "未知";
        return wifi.ssid + "  " + wifi.rssi + "dBm";
    }

    public String faultLabel() {
        return (fault == null || fault.isEmpty()) ? "无" : fault;
    }
}
