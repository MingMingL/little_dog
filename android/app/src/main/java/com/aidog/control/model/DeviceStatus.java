package com.aidog.control.model;

import com.google.gson.annotations.SerializedName;

/**
 * 设备状态，字段与设备端 GET /api/status 的 JSON 一一对应。
 * 见 docs/Android控制端设计.md 第 2 节。
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

    /** 转成界面上显示的中文模式名。界面提示语也要用同一个映射，避免两处各写一套 */
    public static String modeLabel(String mode) {
        if (mode == null) return "未知";
        switch (mode) {
            case "idle":   return "待机";
            case "chat":   return "聊天";
            case "patrol": return "巡逻";
            case "guard":  return "看门";
            case "remote": return "遥控";
            default:       return mode;
        }
    }

    public String modeLabel() {
        return modeLabel(mode);
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
