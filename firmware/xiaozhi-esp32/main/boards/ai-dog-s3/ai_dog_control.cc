#include "ai_dog_control.h"

#include <cJSON.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_wifi.h>
#include <ssid_manager.h>
#include <wifi_manager.h>

#include <cstring>

#define TAG "AiDogControl"

namespace aidog {

const char* ModeToString(Mode mode) {
    switch (mode) {
        case Mode::Idle:   return "idle";
        case Mode::Chat:   return "chat";
        case Mode::Patrol: return "patrol";
        case Mode::Guard:  return "guard";
        default:           return "idle";
    }
}

bool ModeFromString(const std::string& text, Mode& out) {
    if (text == "idle")        { out = Mode::Idle;   return true; }
    if (text == "chat")        { out = Mode::Chat;   return true; }
    if (text == "patrol")      { out = Mode::Patrol; return true; }
    if (text == "guard")       { out = Mode::Guard;  return true; }
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

void ControlServer::SetMode(Mode mode) {
    ModeChangedHandler handler;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (mode_ == mode) {
            return;
        }
        mode_ = mode;
        moving_ = false;  // 模式切换后运动状态失效，由运动模块重新上报
        handler = mode_changed_handler_;
    }
    ESP_LOGI(TAG, "模式切换 -> %s", ModeToString(mode));
    if (handler) {
        handler(mode);
    }
}

void ControlServer::RequestEmergencyStop() {
    EmergencyStopHandler handler;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        mode_ = Mode::Idle;
        moving_ = false;
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

esp_err_t ControlServer::StatusHandler(httpd_req_t* req) {
    auto& self = GetInstance();

    Mode mode;
    bool moving;
    int battery;
    bool charging;
    std::string fault;
    {
        std::lock_guard<std::mutex> lock(self.mutex_);
        mode = self.mode_;
        moving = self.moving_;
        battery = self.battery_percent_;
        charging = self.charging_;
        fault = self.fault_;
    }

    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "mode", ModeToString(mode));
    cJSON_AddBoolToObject(root, "moving", moving);
    cJSON_AddNumberToObject(root, "battery", battery);
    cJSON_AddBoolToObject(root, "charging", charging);

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
    std::string body = ReadRequestBody(req);

    cJSON* root = cJSON_Parse(body.c_str());
    if (root == nullptr) {
        SendResult(req, false, "invalid json");
        return ESP_OK;
    }
    cJSON* mode_item = cJSON_GetObjectItem(root, "mode");
    Mode mode;
    bool valid = cJSON_IsString(mode_item) && ModeFromString(mode_item->valuestring, mode);
    cJSON_Delete(root);

    if (!valid) {
        SendResult(req, false, "invalid mode (idle|chat|patrol|guard)");
        return ESP_OK;
    }

    GetInstance().SetMode(mode);

    cJSON* resp = cJSON_CreateObject();
    cJSON_AddBoolToObject(resp, "ok", true);
    cJSON_AddStringToObject(resp, "mode", ModeToString(mode));
    SendJson(req, resp);
    return ESP_OK;
}

esp_err_t ControlServer::StopHandler(httpd_req_t* req) {
    GetInstance().RequestEmergencyStop();
    SendResult(req, true, nullptr);
    return ESP_OK;
}

esp_err_t ControlServer::WifiHandler(httpd_req_t* req) {
    std::string body = ReadRequestBody(req);

    cJSON* root = cJSON_Parse(body.c_str());
    if (root == nullptr) {
        SendResult(req, false, "invalid json");
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
    cJSON* resp = cJSON_CreateObject();
    cJSON_AddBoolToObject(resp, "ok", true);
    cJSON_AddStringToObject(resp, "ip", "");
    SendJson(req, resp);
    return ESP_OK;
}

void ControlServer::Start() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (server_ != nullptr) {
        return;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.max_uri_handlers = 8;
    config.stack_size = 6144;   // JSON 解析需要更多栈

    if (httpd_start(&server_, &config) != ESP_OK) {
        ESP_LOGE(TAG, "HTTP 服务启动失败（端口 80 可能被占用）");
        server_ = nullptr;
        return;
    }

    httpd_uri_t status_uri = {
        .uri = "/api/status",
        .method = HTTP_GET,
        .handler = StatusHandler,
        .user_ctx = nullptr
    };
    httpd_uri_t mode_uri = {
        .uri = "/api/mode",
        .method = HTTP_POST,
        .handler = ModeHandler,
        .user_ctx = nullptr
    };
    httpd_uri_t stop_uri = {
        .uri = "/api/stop",
        .method = HTTP_POST,
        .handler = StopHandler,
        .user_ctx = nullptr
    };
    httpd_uri_t wifi_uri = {
        .uri = "/api/wifi",
        .method = HTTP_POST,
        .handler = WifiHandler,
        .user_ctx = nullptr
    };

    httpd_register_uri_handler(server_, &status_uri);
    httpd_register_uri_handler(server_, &mode_uri);
    httpd_register_uri_handler(server_, &stop_uri);
    httpd_register_uri_handler(server_, &wifi_uri);

    ESP_LOGI(TAG, "局域网控制服务已启动：http://<ip>/api/status");
}

void ControlServer::Stop() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (server_ == nullptr) {
        return;
    }
    httpd_stop(server_);
    server_ = nullptr;
    ESP_LOGI(TAG, "局域网控制服务已停止");
}

}  // namespace aidog
