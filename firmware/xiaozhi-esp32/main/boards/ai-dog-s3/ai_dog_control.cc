#include "ai_dog_control.h"

#include <cJSON.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <ssid_manager.h>
#include <wifi_manager.h>

#include <cstring>

#define TAG "AiDogControl"

namespace {

// 遥控看门狗：超过这个时间没收到"让狗动"的指令就自动停车。
// 1.5s 是权衡值——App 按 ~10Hz（100ms）重复发指令，正常网络抖动下绝不会超时；
// 但手机锁屏、App 被杀或 Wi-Fi 断掉时，1.5s 内狗最多再跑十几厘米就停。
constexpr int64_t kMoveTimeoutUs = 1500 * 1000;

// 看门狗检查周期
constexpr uint64_t kWatchdogPeriodUs = 200 * 1000;

// 模式名的唯一清单：ModeToString / ModeFromString 都查这张表。
// 以前两个函数各列一遍字符串，加模式时漏改一处就会出现"能解析进来、
// 但状态里报成 idle"这种半同步错误。
constexpr struct { aidog::Mode mode; const char* name; } kModeNames[] = {
    {aidog::Mode::Idle,   "idle"},
    {aidog::Mode::Chat,   "chat"},
    {aidog::Mode::Patrol, "patrol"},
    {aidog::Mode::Guard,  "guard"},
    {aidog::Mode::Remote, "remote"},
};

// 所有 POST 接口解析失败时回同一句话，别在四个 handler 里各写一遍字面量
constexpr char kErrInvalidJson[] = "invalid json";

}  // namespace

namespace aidog {

const char* ModeToString(Mode mode) {
    for (const auto& entry : kModeNames) {
        if (entry.mode == mode) {
            return entry.name;
        }
    }
    return "idle";  // 兜底：未知枚举按待机报，别把空指针写进 JSON
}

bool ModeFromString(const std::string& text, Mode& out) {
    for (const auto& entry : kModeNames) {
        if (text == entry.name) {
            out = entry.mode;
            return true;
        }
    }
    return false;
}

bool MoveDirectionFromString(const std::string& text, MoveDirection& out) {
    if (text == "stop")         { out = MoveDirection::Stop;         return true; }
    if (text == "forward")      { out = MoveDirection::Forward;      return true; }
    if (text == "backward")     { out = MoveDirection::Backward;     return true; }
    if (text == "left")         { out = MoveDirection::Left;         return true; }
    if (text == "right")        { out = MoveDirection::Right;        return true; }
    if (text == "rotate_left")  { out = MoveDirection::RotateLeft;   return true; }
    if (text == "rotate_right") { out = MoveDirection::RotateRight;  return true; }
    return false;
}

ControlServer& ControlServer::GetInstance() {
    static ControlServer instance;
    return instance;
}

// ---------------------------------------------------------------------------
// 状态读写
// ---------------------------------------------------------------------------

Mode ControlServer::GetMode() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return mode_;
}

bool ControlServer::SetMode(Mode mode) {
    ModeChangedHandler handler;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (mode_ == mode) {
            return true;
        }
        // 闸门在持锁状态下调用：回调只读自己的状态，不会再回头调 ControlServer
        if (mode_guard_handler_ && !mode_guard_handler_(mode)) {
            ESP_LOGW(TAG, "模式切换被拒绝：%s（当前 %s）", ModeToString(mode), ModeToString(mode_));
            return false;
        }
        mode_ = mode;
        moving_ = false;  // 模式切换后运动状态失效，由运动模块重新上报
        // 换模式就作废遥控看门狗：新模式的运动由该模式自己的逻辑负责，
        // 不能让遥控的超时停车在别的模式里插一脚。
        last_move_time_us_ = 0;
        handler = mode_changed_handler_;
    }
    ESP_LOGI(TAG, "模式切换 -> %s", ModeToString(mode));
    if (handler) {
        handler(mode);
    }
    return true;
}

void ControlServer::RequestEmergencyStop() {
    EmergencyStopHandler handler;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        mode_ = Mode::Idle;
        moving_ = false;
        last_move_time_us_ = 0;  // 已停车，看门狗不再需要介入
        handler = stop_handler_;
    }
    ESP_LOGW(TAG, "急停指令：立即停止并回到待机");
    if (handler) {
        handler();
    }
}

void ControlServer::SetMoving(bool moving) {
    std::lock_guard<std::mutex> lock(mutex_);
    moving_ = moving;
}

void ControlServer::SetBatteryPercent(int percent) {
    std::lock_guard<std::mutex> lock(mutex_);
    battery_percent_ = percent;
}

void ControlServer::SetBatteryVoltage(float volts) {
    std::lock_guard<std::mutex> lock(mutex_);
    battery_volts_ = volts;
}

void ControlServer::SetBatteryLevelText(const char* level) {
    std::lock_guard<std::mutex> lock(mutex_);
    battery_level_ = level ? level : "unknown";
}

void ControlServer::SetCharging(bool charging) {
    std::lock_guard<std::mutex> lock(mutex_);
    charging_ = charging;
}

void ControlServer::SetFault(const std::string& fault) {
    std::lock_guard<std::mutex> lock(mutex_);
    fault_ = fault;
}

void ControlServer::ClearFault() {
    std::lock_guard<std::mutex> lock(mutex_);
    fault_.clear();
}

void ControlServer::SetModeChangedHandler(ModeChangedHandler handler) {
    std::lock_guard<std::mutex> lock(mutex_);
    mode_changed_handler_ = std::move(handler);
}

void ControlServer::SetEmergencyStopHandler(EmergencyStopHandler handler) {
    std::lock_guard<std::mutex> lock(mutex_);
    stop_handler_ = std::move(handler);
}

void ControlServer::SetMoveCommandHandler(MoveCommandHandler handler) {
    std::lock_guard<std::mutex> lock(mutex_);
    move_command_handler_ = std::move(handler);
}

void ControlServer::SetModeGuardHandler(ModeGuardHandler handler) {
    std::lock_guard<std::mutex> lock(mutex_);
    mode_guard_handler_ = std::move(handler);
}

// ---------------------------------------------------------------------------
// 遥控指令 + 看门狗
// ---------------------------------------------------------------------------

bool ControlServer::PostMoveCommand(MoveDirection direction, int speed) {
    MoveCommandHandler handler;
    MoveCommand command;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        // 闸门一：只有遥控模式接受运动指令。
        // 巡逻/看门有自己的运动逻辑，App 不能越过模式状态机直接抢电机。
        if (mode_ != Mode::Remote) {
            return false;
        }

        if (speed < 0)   speed = 0;
        if (speed > 100) speed = 100;

        command.direction = direction;
        command.speed = speed;

        // 只有"让狗动起来"的指令才刷新看门狗时间戳：
        // stop 指令本身已经停了车，再刷新会让看门狗 1.5s 后又白触发一次。
        if (direction != MoveDirection::Stop) {
            last_move_time_us_ = esp_timer_get_time();
            moving_ = true;
        } else {
            last_move_time_us_ = 0;
            moving_ = false;
        }
        handler = move_command_handler_;
    }

    if (handler) {
        handler(command);
    }
    return true;
}

void ControlServer::WatchdogCallback(void* arg) {
    auto& self = GetInstance();
    MoveCommandHandler handler;
    {
        std::lock_guard<std::mutex> lock(self.mutex_);
        // 闸门三：遥控模式下太久没收到新指令，判定为"控制端没了"（App 被杀、
        // 手机锁屏、Wi-Fi 断了都算），主动停车。置 0 保证只触发一次，
        // 不会每 200ms 打一条日志。
        if (self.mode_ != Mode::Remote || self.last_move_time_us_ == 0) {
            return;
        }
        if (esp_timer_get_time() - self.last_move_time_us_ < kMoveTimeoutUs) {
            return;
        }
        self.last_move_time_us_ = 0;
        self.moving_ = false;
        handler = self.move_command_handler_;
    }

    if (handler) {
        ESP_LOGW(TAG, "遥控看门狗超时（%lld ms 无指令），自动停车", kMoveTimeoutUs / 1000);
        handler(MoveCommand{MoveDirection::Stop, 0});
    }
}

void ControlServer::StartWatchdog() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (watchdog_timer_ != nullptr) {
        return;
    }
    esp_timer_create_args_t args = {};
    args.callback = WatchdogCallback;
    args.arg = nullptr;
    args.name = "aidog_move_wd";
    if (esp_timer_create(&args, &watchdog_timer_) != ESP_OK) {
        ESP_LOGE(TAG, "遥控看门狗定时器创建失败");
        watchdog_timer_ = nullptr;
        return;
    }
    // 周期检查比超时阈值小一个量级，保证超时判断的误差不超过一个周期
    if (esp_timer_start_periodic(watchdog_timer_, kWatchdogPeriodUs) != ESP_OK) {
        ESP_LOGE(TAG, "遥控看门狗定时器启动失败");
        esp_timer_delete(watchdog_timer_);
        watchdog_timer_ = nullptr;
    }
}

void ControlServer::StopWatchdog() {
    esp_timer_handle_t timer = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        timer = watchdog_timer_;
        watchdog_timer_ = nullptr;
        last_move_time_us_ = 0;
    }
    // 必须在锁外删除：esp_timer_delete 会等回调跑完，而回调要拿同一把锁，
    // 持锁删除会死锁。
    if (timer != nullptr) {
        esp_timer_stop(timer);
        esp_timer_delete(timer);
    }
}

// ---------------------------------------------------------------------------
// HTTP 服务
// ---------------------------------------------------------------------------

/** 读完整 POST body。返回 0 表示读取结束，负数表示出错/已读完。 */
static std::string ReadRequestBody(httpd_req_t* req) {
    std::string body;
    char buffer[256];
    int received;
    while ((received = httpd_req_recv(req, buffer, sizeof(buffer))) > 0) {
        body.append(buffer, static_cast<size_t>(received));
        if (static_cast<size_t>(received) < sizeof(buffer)) {
            break;
        }
    }
    return body;
}

/** 读 body 并解析；解析失败返回 nullptr，由调用方统一回 invalid json */
static cJSON* ParseJsonBody(httpd_req_t* req) {
    std::string body = ReadRequestBody(req);
    return cJSON_Parse(body.c_str());
}

static void SendJson(httpd_req_t* req, cJSON* root) {
    httpd_resp_set_type(req, "application/json");
    char* text = cJSON_PrintUnformatted(root);
    if (text != nullptr) {
        httpd_resp_sendstr(req, text);
        cJSON_free(text);
    } else {
        httpd_resp_sendstr(req, "{\"ok\":false,\"error\":\"json\"}");
    }
    cJSON_Delete(root);
}

static void SendResult(httpd_req_t* req, bool ok, const char* error) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddBoolToObject(root, "ok", ok);
    if (!ok && error != nullptr) {
        cJSON_AddStringToObject(root, "error", error);
    }
    SendJson(req, root);
}

/** 成功且要带一个额外字段时用（切模式回 mode、配网回 ip），省得每处手搓一遍 */
static void SendOkWith(httpd_req_t* req, const char* key, const char* value) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddBoolToObject(root, "ok", true);
    cJSON_AddStringToObject(root, key, value);
    SendJson(req, root);
}

esp_err_t ControlServer::StatusHandler(httpd_req_t* req) {
    auto& self = GetInstance();

    Mode mode;
    bool moving;
    int battery;
    bool charging;
    float battery_volts;
    std::string battery_level;
    std::string fault;
    {
        std::lock_guard<std::mutex> lock(self.mutex_);
        mode = self.mode_;
        moving = self.moving_;
        battery = self.battery_percent_;
        charging = self.charging_;
        battery_volts = self.battery_volts_;
        battery_level = self.battery_level_;
        fault = self.fault_;
    }

    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "mode", ModeToString(mode));
    cJSON_AddBoolToObject(root, "moving", moving);
    cJSON_AddNumberToObject(root, "battery", battery);
    cJSON_AddBoolToObject(root, "charging", charging);
    // 电池实测电压（V）。-1 表示分压没接好、读不到。
    // 排查"电池供电反复重启"就看这个数和 battery_level。
    cJSON_AddNumberToObject(root, "battery_volts", static_cast<double>(battery_volts));
    cJSON_AddStringToObject(root, "battery_level", battery_level.c_str());

    cJSON* wifi = cJSON_CreateObject();
    wifi_ap_record_t ap_info;
    if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
        char ssid[33] = {0};
        memcpy(ssid, ap_info.ssid, sizeof(ap_info.ssid));
        cJSON_AddStringToObject(wifi, "ssid", ssid);
        cJSON_AddNumberToObject(wifi, "rssi", ap_info.rssi);
    }
    cJSON_AddItemToObject(root, "wifi", wifi);

    if (fault.empty()) {
        cJSON_AddNullToObject(root, "fault");
    } else {
        cJSON_AddStringToObject(root, "fault", fault.c_str());
    }

    cJSON_AddNumberToObject(root, "uptime", static_cast<double>(esp_timer_get_time() / 1000000LL));
    SendJson(req, root);
    return ESP_OK;
}

esp_err_t ControlServer::ModeHandler(httpd_req_t* req) {
    cJSON* root = ParseJsonBody(req);
    if (root == nullptr) {
        SendResult(req, false, kErrInvalidJson);
        return ESP_OK;
    }
    cJSON* mode_item = cJSON_GetObjectItem(root, "mode");
    Mode mode;
    bool valid = cJSON_IsString(mode_item) && ModeFromString(mode_item->valuestring, mode);
    cJSON_Delete(root);

    if (!valid) {
        SendResult(req, false, "invalid mode (idle|chat|patrol|guard|remote)");
        return ESP_OK;
    }

    if (!GetInstance().SetMode(mode)) {
        SendResult(req, false, "mode change rejected (see fault / battery_level)");
        return ESP_OK;
    }

    SendOkWith(req, "mode", ModeToString(mode));
    return ESP_OK;
}

esp_err_t ControlServer::StopHandler(httpd_req_t* req) {
    GetInstance().RequestEmergencyStop();
    SendResult(req, true, nullptr);
    return ESP_OK;
}

esp_err_t ControlServer::MoveHandler(httpd_req_t* req) {
    cJSON* root = ParseJsonBody(req);
    if (root == nullptr) {
        SendResult(req, false, kErrInvalidJson);
        return ESP_OK;
    }
    cJSON* dir_item = cJSON_GetObjectItem(root, "dir");
    // speed 选填，缺省给一个偏慢的默认值：遥控误触时冲出去的代价太大
    cJSON* speed_item = cJSON_GetObjectItem(root, "speed");

    MoveDirection dir;
    bool valid = cJSON_IsString(dir_item) && MoveDirectionFromString(dir_item->valuestring, dir);
    int speed = cJSON_IsNumber(speed_item) ? static_cast<int>(speed_item->valuedouble) : 60;
    cJSON_Delete(root);

    if (!valid) {
        SendResult(req, false, "invalid dir (stop|forward|backward|left|right|rotate_left|rotate_right)");
        return ESP_OK;
    }

    auto& self = GetInstance();
    if (!self.PostMoveCommand(dir, speed)) {
        // 不是遥控模式：明确告诉 App 原因，让它在界面上提示用户，
        // 而不是让按钮看起来"点了没反应"
        SendResult(req, false, "not in remote mode");
        return ESP_OK;
    }

    SendResult(req, true, nullptr);
    return ESP_OK;
}

esp_err_t ControlServer::WifiHandler(httpd_req_t* req) {
    cJSON* root = ParseJsonBody(req);
    if (root == nullptr) {
        SendResult(req, false, kErrInvalidJson);
        return ESP_OK;
    }
    cJSON* ssid_item = cJSON_GetObjectItem(root, "ssid");
    cJSON* password_item = cJSON_GetObjectItem(root, "password");
    if (!cJSON_IsString(ssid_item)) {
        cJSON_Delete(root);
        SendResult(req, false, "missing ssid");
        return ESP_OK;
    }
    std::string ssid = ssid_item->valuestring;
    std::string password = cJSON_IsString(password_item) ? password_item->valuestring : "";
    cJSON_Delete(root);

    if (ssid.empty()) {
        SendResult(req, false, "empty ssid");
        return ESP_OK;
    }

    ESP_LOGI(TAG, "配网指令：ssid=%s", ssid.c_str());
    // 写入 NVS，然后用新凭据重新尝试连接（失败会自动回到配网模式，不会失联）
    SsidManager::GetInstance().AddSsid(ssid, password);
    WifiManager::GetInstance().StartStation();

    // IP 要等连接成功才有，这里先返回空串，App 会按"已下发"处理
    SendOkWith(req, "ip", "");
    return ESP_OK;
}

void ControlServer::Start() {
    {
        // httpd 的启停和 server_ 的读写都在这把锁里，Stop() 同样持锁，不会打架。
        // 走到函数末尾就代表启动成功：失败的两条路（已在运行 / httpd_start 失败）都直接 return。
        std::lock_guard<std::mutex> lock(mutex_);
        if (server_ != nullptr) {
            return;
        }

        httpd_config_t config = HTTPD_DEFAULT_CONFIG();
        config.server_port = 80;
        config.max_uri_handlers = 10;   // status/mode/stop/move/wifi 共 5 个，留余量
        config.stack_size = 6144;   // JSON 解析需要更多栈

        if (httpd_start(&server_, &config) != ESP_OK) {
            ESP_LOGE(TAG, "HTTP 服务启动失败（端口 80 可能被占用）");
            server_ = nullptr;
            return;
        }

        // 路由表：五个接口除了 uri/method/handler 其它字段都一样，
        // 摊平成一张表循环注册，加接口时只改这一处。
        const httpd_uri_t routes[] = {
            {.uri = "/api/status", .method = HTTP_GET,  .handler = StatusHandler, .user_ctx = nullptr},
            {.uri = "/api/mode",   .method = HTTP_POST, .handler = ModeHandler,   .user_ctx = nullptr},
            {.uri = "/api/stop",   .method = HTTP_POST, .handler = StopHandler,   .user_ctx = nullptr},
            {.uri = "/api/move",   .method = HTTP_POST, .handler = MoveHandler,   .user_ctx = nullptr},
            {.uri = "/api/wifi",   .method = HTTP_POST, .handler = WifiHandler,   .user_ctx = nullptr},
        };
        for (const httpd_uri_t& route : routes) {
            httpd_register_uri_handler(server_, &route);
        }
    }

    ESP_LOGI(TAG, "局域网控制服务已启动：http://<ip>/api/status");

    // 看门狗必须在锁外启动：它内部还要拿同一把 mutex_，std::mutex 不可重入
    StartWatchdog();
}

void ControlServer::Stop() {
    StopWatchdog();

    std::lock_guard<std::mutex> lock(mutex_);
    if (server_ == nullptr) {
        return;
    }
    httpd_stop(server_);
    server_ = nullptr;
    ESP_LOGI(TAG, "局域网控制服务已停止");
}

}  // namespace aidog
