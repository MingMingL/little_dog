#ifndef AI_DOG_CONTROL_H
#define AI_DOG_CONTROL_H

#include <functional>
#include <mutex>
#include <string>

#include <esp_err.h>
#include <esp_http_server.h>

namespace aidog {

/**
 * 机器狗工作模式。
 * 与 Android 端约定的字符串：idle / chat / patrol / guard
 */
enum class Mode {
    Idle,     // 待机：电机停止
    Chat,     // 语音聊天（xiaozhi 原有行为）
    Patrol,   // 巡逻：循线行驶
    Guard,    // 看门：设防，检测声音后狗叫
};

const char* ModeToString(Mode mode);
bool ModeFromString(const std::string& text, Mode& out);

/**
 * 局域网控制服务（设备端）。
 *
 * 对应 docs/Android控制端设计.md 第 2 节：
 *   GET  /api/status   查询状态
 *   POST /api/mode     切换模式
 *   POST /api/stop     急停（最高优先级）
 *   POST /api/wifi     配网
 *
 * 说明：
 * - 本模块只负责"收指令 + 报状态"，不直接驱动电机；
 *   电机/传感器的具体动作由后续运动模块注册进回调（SetModeChangedHandler）
 *   后再实现，保证控制指令始终经过模式状态机。
 * - HTTP 服务只有在 STA 连上路由器后才启动，避免和配网热点的
 *   80 端口 captive portal 冲突。
 */
class ControlServer {
public:
    static ControlServer& GetInstance();

    /** 启动 HTTP 服务（端口 80）；重复调用安全 */
    void Start();
    /** 停止 HTTP 服务（进入配网热点模式前必须调用） */
    void Stop();
    bool IsRunning() const { return server_ != nullptr; }

    // ---- 状态（线程安全） ----
    Mode GetMode() const;
    void SetMode(Mode mode);
    /** 急停：立即回到 Idle 并触发停止回调 */
    void RequestEmergencyStop();
    void SetMoving(bool moving);
    void SetBatteryPercent(int percent);   // -1 表示无采样
    void SetCharging(bool charging);
    void SetFault(const std::string& fault);
    void ClearFault();

    // ---- 供运动/看门模块接入的回调 ----
    using ModeChangedHandler = std::function<void(Mode)>;
    using EmergencyStopHandler = std::function<void()>;
    void SetModeChangedHandler(ModeChangedHandler handler);
    void SetEmergencyStopHandler(EmergencyStopHandler handler);

private:
    ControlServer() = default;
    ControlServer(const ControlServer&) = delete;
    ControlServer& operator=(const ControlServer&) = delete;

    mutable std::mutex mutex_;
    httpd_handle_t server_ = nullptr;

    Mode mode_ = Mode::Idle;
    bool moving_ = false;
    int battery_percent_ = -1;
    bool charging_ = false;
    std::string fault_;

    ModeChangedHandler mode_changed_handler_;
    EmergencyStopHandler stop_handler_;

    // httpd 回调（静态，转发到单例）
    static esp_err_t StatusHandler(httpd_req_t* req);
    static esp_err_t ModeHandler(httpd_req_t* req);
    static esp_err_t StopHandler(httpd_req_t* req);
    static esp_err_t WifiHandler(httpd_req_t* req);
};

}  // namespace aidog

#endif  // AI_DOG_CONTROL_H
