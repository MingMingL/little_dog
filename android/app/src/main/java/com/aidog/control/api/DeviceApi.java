package com.aidog.control.api;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.aidog.control.model.DeviceStatus;
import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonSyntaxException;

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
    /** 没配 IP 时所有接口的错误文案；以前三个方法各写一遍字面量，改一处会漏两处 */
    private static final String MSG_NO_IP = "未设置设备 IP";

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
    // 每次启停监听都会递增代数，旧 WebSocket / 轮询回调不得启动新轮询。
    private long statusGeneration = 0;

    /** 遥控指令只允许一条在途；停车是安全指令，不能像运动指令一样丢弃。仅主线程访问。 */
    private boolean moveInFlight = false;
    private SimpleCallback pendingStopCallback;

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

    // ------------------------------------------------------------------
    // 状态获取
    // ------------------------------------------------------------------

    /** 没配 IP 时所有接口都走同一句报错；判空只在 ipReady() 一处 */
    private boolean ipReady() {
        return baseUrl != null;
    }

    /** 读响应体；空体按空串处理，调用方统一当成"设备没说话" */
    private static String readBody(Response response) throws java.io.IOException {
        return response.body() != null ? response.body().string() : "";
    }

    /** 单次查询状态。只给降级轮询用（对外只暴露 startStatusPush），收为私有避免两套接法 */
    private void getStatus(StatusCallback callback) {
        if (!ipReady()) {
            callback.onError(MSG_NO_IP);
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
                    DeviceStatus status = gson.fromJson(readBody(response), DeviceStatus.class);
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
        final long generation = statusGeneration;

        String wsUrl = baseUrl.replace("http://", "ws://").replace("https://", "wss://") + "/ws";
        Request request = new Request.Builder().url(wsUrl).build();

        webSocket = client.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onMessage(WebSocket ws, String text) {
                try {
                    DeviceStatus status = gson.fromJson(text, DeviceStatus.class);
                    mainHandler.post(() -> {
                        if (generation == statusGeneration) callback.onStatus(status);
                    });
                } catch (Exception e) {
                    Log.w(TAG, "状态推送解析失败", e);
                }
            }

            @Override
            public void onFailure(WebSocket ws, Throwable t, Response response) {
                Log.i(TAG, "WebSocket 不可用，降级为轮询：" + t.getMessage());
                mainHandler.post(() -> {
                    if (generation == statusGeneration && !polling) {
                        polling = true;
                        pollOnce(callback, generation);
                    }
                });
            }
        });
    }

    private void pollOnce(StatusCallback callback, long generation) {
        if (!polling || generation != statusGeneration) return;
        getStatus(new StatusCallback() {
            private void scheduleNext() {
                mainHandler.postDelayed(() -> pollOnce(callback, generation), POLL_INTERVAL_MS);
            }

            @Override
            public void onStatus(DeviceStatus status) {
                if (polling && generation == statusGeneration) {
                    callback.onStatus(status);
                    scheduleNext();
                }
            }

            @Override
            public void onError(String message) {
                if (polling && generation == statusGeneration) {
                    callback.onError(message);
                    scheduleNext();
                }
            }
        });
    }

    public void stopStatusPush() {
        ++statusGeneration;
        polling = false;
        if (webSocket != null) {
            webSocket.cancel();
            webSocket = null;
        }
    }

    // ------------------------------------------------------------------
    // 控制指令
    // ------------------------------------------------------------------

    /** 切换模式：idle / chat / patrol / guard / remote（summon 只能由唤醒词+口令进入） */
    public void setMode(String mode, SimpleCallback callback) {
        JsonObject json = new JsonObject();
        json.addProperty("mode", mode);
        post("/api/mode", json.toString(), callback);
    }

    /**
     * V1.5 召唤摄像头开关（SW-401）：POST /api/summon/vision {"enabled": bool}。
     * 设备编译期关闭视觉（AI_DOG_VISION_ENABLE=n）时返回 ok:false，App 提示不可用。
     */
    public void setSummonVision(boolean enabled, SimpleCallback callback) {
        JsonObject json = new JsonObject();
        json.addProperty("enabled", enabled);
        post("/api/summon/vision", json.toString(), callback);
    }

    /** 查询召唤摄像头开关状态（GET /api/summon/vision） */
    public void getSummonVision(SimpleCallback callback) {
        get("/api/summon/vision", callback);
    }

    /** 急停：最高优先级，设备应立即停电机并回到 idle */
    public void emergencyStop(SimpleCallback callback) {
        post("/api/stop", "{}", callback);
    }

    // ------------------------------------------------------------------
    // 遥控运动指令
    // ------------------------------------------------------------------

    /**
     * 遥控运动：dir = stop|forward|backward|left|right|rotate_left|rotate_right，speed = 0..100。
     *
     * 设备只在 remote 模式接受，其它模式会回 ok:false（界面据此提示用户）。
     *
     * 关键取舍：这条指令是"按住持续走"语义，App 按 10Hz 重复发同一条。
     * 如果网络一慢就让请求在 OkHttp 队列里堆着，等恢复瞬间设备会连着执行一批
     * 旧指令 —— 狗会突然自己动起来。所以运动指令允许丢包、不允许堆积：
     * 上一条还没回来就丢掉新的运动指令，反正下 100ms 还会再发一次。
     * 停车指令例外：保留一条待发送的 stop，等在途请求结束后优先下发。
     */
    public void move(String dir, int speed, SimpleCallback callback) {
        if (!ipReady()) {
            callback.onResult(false, MSG_NO_IP);
            return;
        }
        if (moveInFlight) {
            // 松手/onPause 的停车不能被 10Hz 运动请求吞掉；只保留最后一条停车，
            // 保持最多一个请求在途，避免网络恢复后批量执行过期运动指令。
            if ("stop".equals(dir)) pendingStopCallback = callback;
            return;
        }
        moveInFlight = true;

        JsonObject json = new JsonObject();
        json.addProperty("dir", dir);
        json.addProperty("speed", speed);

        enqueuePost("/api/move", json.toString(), (ok, message) -> finishMove(callback, ok, message));
    }

    private void finishMove(SimpleCallback callback, boolean ok, String message) {
        moveInFlight = false;
        SimpleCallback stopCallback = pendingStopCallback;
        pendingStopCallback = null;
        // 先下发积压的停车，再通知旧运动指令的回调，避免回调又发新运动抢在停车前面。
        if (stopCallback != null) move("stop", 0, stopCallback);
        callback.onResult(ok, message);
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
                String newIp = parseResponseIp(message);
                if (!newIp.isEmpty()) {
                    callback.onResult(true, newIp);
                    return;
                }
            }
            callback.onResult(ok, message);
        });
    }

    /**
     * 从响应体里取 ip 字段：{"ok":true,"ip":"192.168.1.50"}。
     * 解析失败或字段缺失都返回空串——设备没回 IP 也算下发成功，由调用方按原样提示。
     * 用 Gson 而不是再引一个 org.json：整个类只有这一处要取字段，两套 JSON 库并存是纯负担。
     */
    private String parseResponseIp(String body) {
        try {
            JsonObject resp = gson.fromJson(body, JsonObject.class);
            if (resp == null) {
                return "";
            }
            JsonElement ip = resp.get("ip");
            return (ip == null || ip.isJsonNull()) ? "" : ip.getAsString();
        } catch (JsonSyntaxException e) {
            return "";
        }
    }

    private void post(String path, String body, SimpleCallback callback) {
        if (!ipReady()) {
            callback.onResult(false, MSG_NO_IP);
            return;
        }
        enqueuePost(path, body, callback);
    }

    /** 真正发 POST 的地方：成功判定和回调上主线程只有这一份，别再复制一份给 /api/move */
    private void enqueuePost(String path, String body, SimpleCallback callback) {
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
                    String respBody = readBody(response);
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

    /** GET 请求（召唤摄像头开关状态查询用） */
    private void get(String path, SimpleCallback callback) {
        if (baseUrl == null) {
            callback.onResult(false, "未设置设备 IP");
            return;
        }
        Request request = new Request.Builder()
                .url(baseUrl + path)
                .get()
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
