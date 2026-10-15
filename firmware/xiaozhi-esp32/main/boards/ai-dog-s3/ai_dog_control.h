#ifndef AI_DOG_CONTROL_H
#define AI_DOG_CONTROL_H

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

#include <esp_err.h>
#include <esp_http_server.h>
#include <esp_timer.h>

namespace aidog {

/**
 * 机器狗工作模式。
 * 与 Android 端约定的字符串：idle / chat / patrol / guard / remote
 */
enum class Mode {
    Idle,     // 待机：电机停止
    Chat,     // 语音聊天（xiaozhi 原有行为）
    Patrol,   // 巡逻：循线行驶
    Guard,    // 看门：设防，检测声音后狗叫
    Remote,   // 遥控：只接受 App 发来的 /api/move 指令
};

const char* ModeToString(Mode mode);
bool ModeFromString(const std::string& text, Mode& out);

/**
 * 遥控方向（App 端方向按钮直接对应）。
 * 字符串与 App 约定：stop / forward / backward / left / right / rotate_left / rotate_right
 */
enum class MoveDirection {
    Stop,         // 停车
    Forward,      // 前进
    Backward,     // 后退
    Left,         // 前进中左转（左右轮差速）
    Right,        // 前进中右转
    RotateLeft,   // 原地左转（两轮反向）
    RotateRight,  // 原地右转
};

const char* MoveDirectionToString(MoveDirection dir);
bool MoveDirectionFromString(const std::string& text, MoveDirection& out);

/** 一条遥控指令：方向 + 速度百分比（0..100） */
struct MoveCommand {
    MoveDirection direction = MoveDirection::Stop;
    int speed = 0;
};

/**
 * 局域网控制服务（设备端）。
 *
 * 对应 docs/Android控制端设计.md 第 2 节：
 *   GET  /api/status   查询状态
 *   POST /api/mode     切换模式
 *   POST /api/stop     急停（最高优先级）
 *   POST /api/move     遥控运动指令（只在 remote 模式生效）
 *   POST /api/wifi     配网
 *
 * 说明：
 * - 本模块只负责"收指令 + 报状态"，不直接驱动电机；
 *   电机/传感器的具体动作由后续运动模块注册进回调（SetModeChangedHandler）
 *   后再实现，保证控制指令始终经过模式状态机。
 * - 遥控安全三道闸（缺一不可）：
 *     1) /api/move 只在 Remote 模式被接受，巡逻/看门时 App 抢不动电机；
 *     2) App 松手/退后台会主动发 stop；
 *     3) 看门狗：Remote 模式下超过 kMoveTimeoutUs 没收到新指令就自动停车，
 *        防止 App 崩溃、手机锁屏或 Wi-Fi 断了以后狗一直往前跑。
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

    // ---- 遥控指令 ----
    /**
     * 下发一条遥控指令。只有当前是 Remote 模式才接受，返回 false 表示被拒绝
     * （调用方应给 App 回 ok:false，让它提示用户"先进入遥控模式"）。
     */
    bool PostMoveCommand(MoveDirection direction, int speed);

    // ---- 供运动/看门模块接入的回调 ----
    using ModeChangedHandler = std::function<void(Mode)>;
    using EmergencyStopHandler = std::function<void()>;
    using MoveCommandHandler = std::function<void(const MoveCommand&)>;
    void SetModeChangedHandler(ModeChangedHandler handler);
    void SetEmergencyStopHandler(EmergencyStopHandler handler);
    void SetMoveCommandHandler(MoveCommandHandler handler);

private:
    ControlServer() = default;
    ControlServer(const ControlServer&) = delete;
    ControlServer& operator=(const ControlServer&) = delete;

    /** 启动/停止遥控看门狗定时器（必须在 mutex_ 之外调用，否则会和看门狗回调死锁） */
    void StartWatchdog();
    void StopWatchdog();
    static void WatchdogCallback(void* arg);

    mutable std::mutex mutex_;
    httpd_handle_t server_ = nullptr;
    esp_timer_handle_t watchdog_timer_ = nullptr;

    Mode mode_ = Mode::Idle;
    bool moving_ = false;
    int battery_percent_ = -1;
    bool charging_ = false;
    std::string fault_;

    // 遥控看门狗：记录最近一条"前进/转向"指令的时间（微秒）。
    // 0 表示当前不需要看门狗（已停车或已触发过一次自动停车）。
    int64_t last_move_time_us_ = 0;

    ModeChangedHandler mode_changed_handler_;
    EmergencyStopHandler stop_handler_;
    MoveCommandHandler move_command_handler_;

    // httpd 回调（静态，转发到单例）
    static esp_err_t StatusHandler(httpd_req_t* req);
    static esp_err_t ModeHandler(httpd_req_t* req);
    static esp_err_t StopHandler(httpd_req_t* req);
    static esp_err_t MoveHandler(httpd_req_t* req);
    static esp_err_t WifiHandler(httpd_req_t* req);
};

}  // namespace aidog

#endif  // AI_DOG_CONTROL_H
