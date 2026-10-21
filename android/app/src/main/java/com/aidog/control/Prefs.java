package com.aidog.control;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * 设备配置的本地存储。
 *
 * MainActivity 和 SetupActivity 都要读写同一个"设备 IP"，以前两边各自定义
 * 了一套 PREFS/KEY_IP 常量——改一处漏一处就会读不到对方写的值。这里统一收口。
 */
public final class Prefs {

    private static final String FILE = "ai_dog";
    private static final String KEY_IP = "device_ip";

    private final SharedPreferences prefs;

    public Prefs(Context context) {
        prefs = context.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    /** 已保存的设备 IP；没配置过返回空串 */
    public String getDeviceIp() {
        return prefs.getString(KEY_IP, "");
    }

    public boolean hasDeviceIp() {
        return !getDeviceIp().isEmpty();
    }

    public void setDeviceIp(String ip) {
        prefs.edit().putString(KEY_IP, ip == null ? "" : ip.trim()).apply();
    }
}
