package com.aidog.control.api;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.aidog.control.model.DeviceStatus;
import com.google.gson.Gson;
import com.google.gson.JsonObject;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.TimeUnit;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/**
 * 设备通信封装：局域网直连 HTTP + 可选 WebSocket 推送。
 *
 * 设计要点：
 * 1. 所有回调都在主线程执行，调用方可以直接更新 UI；
 * 2. 优先用 WebSocket 收推送，连不上自动降级为定时轮询（设备端可以先不实现 WS）；
 * 3. 超时统一 3 秒——局域网内不该慢，慢了基本等于设备不在。
 */
public class DeviceApi {

    private static final String TAG = "DeviceApi";
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");
    private static final long POLL_INTERVAL_MS = 2000L;

    public interface StatusCallback {
        void onStatus(DeviceStatus status);
        void onError(String message);
    }

    public interface SimpleCallback {
        void onResult(boolean ok, String message);
    }

    private final OkHttpClient client;
    private final Gson gson = new Gson();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private String baseUrl;          // 形如 http://192.168.1.50
    private WebSocket webSocket;
    private boolean polling = false;
    private StatusCallback pushCallback;

    public DeviceApi() {
        client = new OkHttpClient.Builder()
                .connectTimeout(3, TimeUnit.SECONDS)
                .readTimeout(3, TimeUnit.SECONDS)
                .writeTimeout(3, TimeUnit.SECONDS)
                .build();
    }

    public void setDeviceIp(String ip) {
        String normalized = ip.trim();
        if (!normalized.startsWith("http")) {
            normalized = "http://" + normalized;
        }
        this.baseUrl = normalized;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    // ------------------------------------------------------------------
    // 状态获取
    // ------------------------------------------------------------------

    /** 单次查询状态 */
    public void getStatus(StatusCallback callback) {
        if (baseUrl == null) {
            callback.onError("未设置设备 IP");
            return;
        }
        Request request = new Request.Builder()
                .url(baseUrl + "/api/status")
                .get()
                .build();

        client.newCall(request).enqueue(new okhttp3.Callback() {
            @Override
            public void onFailure(okhttp3.Call call, java.io.IOException e) {
                postError(callback, "连接失败：" + e.getMessage());
            }

            @Override
            public void onResponse(okhttp3.Call call, Response response) {
                try {
                    if (!response.isSuccessful()) {
                        postError(callback, "设备返回 " + response.code());
                        return;
                    }
                    String body = response.body() != null ? response.body().string() : "";
                    DeviceStatus status = gson.fromJson(body, DeviceStatus.class);
                    mainHandler.post(() -> callback.onStatus(status));
                } catch (Exception e) {
                    postError(callback, "解析失败：" + e.getMessage());
                } finally {
                    response.close();
                }
            }
        });
    }

    // ------------------------------------------------------------------
    // 状态推送：WebSocket 优先，失败降级轮询
    // ------------------------------------------------------------------

    public void startStatusPush(StatusCallback callback) {
        stopStatusPush();
        this.pushCallback = callback;

        String wsUrl = baseUrl.replace("http://", "ws://").replace("https://", "wss://") + "/ws";
        Request request = new Request.Builder().url(wsUrl).build();

        webSocket = client.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onMessage(WebSocket ws, String text) {
                try {
                    DeviceStatus status = gson.fromJson(text, DeviceStatus.class);
                    mainHandler.post(() -> callback.onStatus(status));
                } catch (Exception e) {
                    Log.w(TAG, "状态推送解析失败", e);
                }
            }

            @Override
            public void onFailure(WebSocket ws, Throwable t, Response response) {
                Log.i(TAG, "WebSocket 不可用，降级为轮询：" + t.getMessage());
                startPolling(callback);
            }
        });
    }

    private void startPolling(StatusCallback callback) {
        polling = true;
        pollOnce(callback);
    }

    private void pollOnce(StatusCallback callback) {
        getStatus(new StatusCallback() {
            @Override
            public void onStatus(DeviceStatus status) {
                if (polling) {
                    callback.onStatus(status);
                    mainHandler.postDelayed(() -> pollOnce(callback), POLL_INTERVAL_MS);
                }
            }

            @Override
            public void onError(String message) {
                if (polling) {
                    callback.onError(message);
                    mainHandler.postDelayed(() -> pollOnce(callback), POLL_INTERVAL_MS);
                }
            }
        });
    }

    public void stopStatusPush() {
        polling = false;
        pushCallback = null;
        if (webSocket != null) {
            webSocket.cancel();
            webSocket = null;
        }
    }

    // ------------------------------------------------------------------
    // 控制指令
    // ------------------------------------------------------------------

    /** 切换模式：idle / chat / patrol / guard */
    public void setMode(String mode, SimpleCallback callback) {
        JsonObject json = new JsonObject();
        json.addProperty("mode", mode);
        post("/api/mode", json.toString(), callback);
    }

    /** 急停：最高优先级，设备应立即停电机并回到 idle */
    public void emergencyStop(SimpleCallback callback) {
        post("/api/stop", "{}", callback);
    }

    /**
     * 配网：把 Wi-Fi 凭据下发给设备。
     * 成功时 message 里带回设备的新 IP（{"ok":true,"ip":"192.168.1.50"}）。
     */
    public void provisionWifi(String ssid, String password, SimpleCallback callback) {
        JsonObject json = new JsonObject();
        json.addProperty("ssid", ssid);
        json.addProperty("password", password);
        post("/api/wifi", json.toString(), (ok, message) -> {
            if (ok) {
                try {
                    JSONObject resp = new JSONObject(message);
                    String newIp = resp.optString("ip", "");
                    if (!newIp.isEmpty()) {
                        callback.onResult(true, newIp);
                        return;
                    }
                } catch (JSONException ignored) {
                    // 设备没回 IP 也当作成功
                }
            }
            callback.onResult(ok, message);
        });
    }

    private void post(String path, String body, SimpleCallback callback) {
        if (baseUrl == null) {
            callback.onResult(false, "未设置设备 IP");
            return;
        }
        Request request = new Request.Builder()
                .url(baseUrl + path)
                .post(RequestBody.create(body, JSON))
                .build();

        client.newCall(request).enqueue(new okhttp3.Callback() {
            @Override
            public void onFailure(okhttp3.Call call, java.io.IOException e) {
                mainHandler.post(() -> callback.onResult(false, "连接失败：" + e.getMessage()));
            }

            @Override
            public void onResponse(okhttp3.Call call, Response response) {
                try {
                    String respBody = response.body() != null ? response.body().string() : "";
                    boolean ok = response.isSuccessful() && !respBody.contains("\"ok\":false");
                    String msg = respBody.isEmpty() ? ("HTTP " + response.code()) : respBody;
                    mainHandler.post(() -> callback.onResult(ok, msg));
                } catch (Exception e) {
                    mainHandler.post(() -> callback.onResult(false, "解析失败：" + e.getMessage()));
                } finally {
                    response.close();
                }
            }
        });
    }

    private void postError(StatusCallback callback, String message) {
        mainHandler.post(() -> callback.onError(message));
    }
}
