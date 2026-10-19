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
}
