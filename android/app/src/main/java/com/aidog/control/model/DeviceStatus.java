package com.aidog.control.model;

import android.content.Context;

import com.aidog.control.R;
import com.google.gson.annotations.SerializedName;

/**
 * 设备状态，字段与设备端 GET /api/status 的 JSON 一一对应。
 * 见 docs/Android控制端设计.md 第 2 节。
 *
 * 展示文案一律走 strings.xml（所以方法要带 Context）：模式名在布局按钮和状态面板
 * 里都要显示，各自硬编码一份中文，改按钮文案时状态面板就会漏改。
 */
public class DeviceStatus {

    /** idle | chat | patrol | guard | remote */
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
     * V1.5 召唤状态（SW-401）。只在召唤进行中（或 summon 模式）出现；
     * 字段与设备端 ai_dog_control.cc 的 summon 对象一致：
     * phase / doa_deg / energy_db / distance_mm / vision_used /
     * vision_result / vision_tries / elapsed_ms / message。
     * 老固件没这个字段 → summon == null → App 自动隐藏召唤页。
     */
    @SerializedName("summon")
    public SummonInfo summon;

    public static class WifiInfo {
        @SerializedName("ssid")
        public String ssid;

        @SerializedName("rssi")
        public int rssi;
    }

    /** 模式名 -> 显示文案。界面按钮（mode_*）和界面提示语共用同一份资源 */
    public static String modeLabel(Context context, String mode) {
        if (mode == null) return context.getString(R.string.value_unknown);
        switch (mode) {
            case "idle":   return context.getString(R.string.mode_idle);
            case "chat":   return context.getString(R.string.mode_chat);
            case "patrol": return context.getString(R.string.mode_patrol);
            case "guard":  return context.getString(R.string.mode_guard);
            case "remote": return context.getString(R.string.mode_remote);
            case "summon": return context.getString(R.string.mode_summon);
            default:       return mode;
        }
    }

    public String modeLabel(Context context) {
        return modeLabel(context, mode);
    }

    public String batteryLabel(Context context) {
        if (battery < 0) return context.getString(R.string.battery_no_sample);
        return battery + "%" + (charging ? context.getString(R.string.battery_charging) : "");
    }

    public String wifiLabel(Context context) {
        if (wifi == null || wifi.ssid == null) return context.getString(R.string.value_unknown);
        return context.getString(R.string.wifi_value, wifi.ssid, wifi.rssi);
    }

    public String faultLabel(Context context) {
        return (fault == null || fault.isEmpty()) ? context.getString(R.string.value_none) : fault;
    }

    /** 是否在召唤流程中（用于模式按钮置灰） */
    public boolean isSummoning() {
        return "summon".equals(mode) || (summon != null && summon.phase != null
                && !"idle".equals(summon.phase));
    }

    /**
     * 召唤阶段与视觉结果（与设备端 AiDogSummon::PhaseToString 一致）。
     */
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

        public String phaseLabel(Context context) {
            if (phase == null) return context.getString(R.string.value_unknown);
            switch (phase) {
                case "verifying": return context.getString(R.string.summon_phase_verifying);
                case "listening": return context.getString(R.string.summon_phase_listening);
                case "aligning":  return context.getString(R.string.summon_phase_aligning);
                case "moving":    return context.getString(R.string.summon_phase_moving);
                case "avoiding":  return context.getString(R.string.summon_phase_avoiding);
                case "vision":    return context.getString(R.string.summon_phase_vision);
                case "arrived":   return context.getString(R.string.summon_phase_arrived);
                case "rescan":    return context.getString(R.string.summon_phase_rescan);
                case "finishing": return context.getString(R.string.summon_phase_finishing);
                default:          return phase;
            }
        }

        /** 能量条进度 0..100（-80..0 dBFS 映射） */
        public int energyPercent() {
            int pct = (int) ((energyDb + 80f) / 80f * 100f);
            return Math.max(0, Math.min(100, pct));
        }

        public String distanceLabel(Context context) {
            if (distanceMm < 0) return context.getString(R.string.value_unknown);
            if (distanceMm < 1000) return distanceMm + context.getString(R.string.unit_mm);
            return String.format(context.getString(R.string.format_meters), distanceMm / 1000f);
        }

        public String visionLabel(Context context) {
            switch (visionResult) {
                case 0:  return context.getString(R.string.summon_vision_no_human);
                case 1:  return context.getString(R.string.summon_vision_human);
                case 2:  return context.getString(R.string.summon_vision_timeout);
                default: return visionUsed ? context.getString(R.string.summon_vision_confirming)
                                            : context.getString(R.string.summon_vision_off);
            }
        }
    }
}
