#ifndef AI_DOG_SUMMON_H
#define AI_DOG_SUMMON_H

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

class ControlServer;

/**
 * Summon 召唤状态机（SW-204/206）——方案 §4.2 的固件实现。
 *
 * ```
 * [任意模式] --唤醒词--> [S1 口令验证] --通过--> [S2 寻声]
 * S2 循环（停-听-走）:
 *   停 → 听 0.5s（DOA+能量，多帧滤波）→ 原地对准（死区±12°，点动防过冲）
 *   → 前进 1.5s（低速 0.2m/s）→ 停 → 听 …
 *   避障：VL53L0X < 0.4m → 停车 → 原地重定向
 *   接近段触发（DOA 稳定 + 能量近场 或 测距 < 1m）→ S3 视觉确认
 * S3 视觉确认：抓 1~2 帧 → 人形检测
 *   → 有人形 → S4 到达；无人形 → 回 S2（最多 3 次）→ 仍无 → S5 降级
 * S4 到达：停车 → 狗叫/问候 → 自动切 Chat
 * S5 降级：原地 360° 扫描重搜一次 → 仍失败 → 回 Idle，App 提示"没找到你"
 * 异常：超时 60s / 声源丢失 / 低压 → 同 S5 或立即回 Idle
 * ```
 *
 * 抢占（SW-203）：Summon > Chat/Patrol/Guard/Remote，急停 > 全部。
 * Q-09：Summon 抢占后原模式不自动恢复（记录事件日志，用户手动切回）。
 * Q-11：进入前低压预检；过程中低压立即停车回 Idle 并播报。
 *
 * 运动出口：本模块是 Summon 模式的所有者，直接驱动 MotorDriver
 * （与巡逻/看门同层）；/api/move 仍只在 Remote 模式放行，App 抢不了电机。
 * 模式切换/急停由 ControlServer 的状态机保证先停车（board 层回调）。
 */
class AiDogSummon {
public:
    enum class Phase {
        Idle = 0,      // 未召唤
        Verifying,     // S1 口令验证
        Listening,     // S2 听
        Aligning,      // S2 原地对准
        Moving,        // S2 前进
        Avoiding,      // S2 避障重定向
        VisionCheck,   // S3 视觉确认
        Arrived,       // S4 到达
        Rescanning,    // S5 360° 重搜
        Finishing,     // 收尾（回 Idle 前的播报）
    };

    struct Status {
        Phase phase = Phase::Idle;
        float doa_deg = 0.0f;      // 最近一次有效 DOA（0..360）
        float energy_db = -100.0f; // 最近一次能量
        int distance_mm = -1;      // VL53L0X 前向测程（-1 无效）
        bool vision_used = false;  // 本段是否用过视觉
        int vision_result = -1;    // -1 未用 / 0 无人形 / 1 有人形 / 2 超时
        int elapsed_ms = 0;        // 本次召唤已用时
        int vision_tries = 0;      // 视觉确认已重试次数
        const char* message = "";  // App 展示的状态短语
    };

    static AiDogSummon& GetInstance();

    /**
     * 初始化各子模块（阵列/DOA/口令/测距/视觉/巡线）并启动状态机任务。
     *  summon 开关打开时由 board 构造调用；失败时逐个降级并继续
     *  （阵列/DOA 失败 → 召唤不可用，语音遥控等不受影响）。
     */
    esp_err_t Initialize();

    /** 关闭状态机与子模块（进配网模式/关机前） */
    void Shutdown();

    bool IsEnabled() const { return enabled_; }

    /** 唤醒词命中（Application 钩子）。打开 S1 验证窗口。 */
    void OnWakeWordHeard(const std::string& wake_word);

    /** 播放门控：狗叫/播报期间暂停口令识别（AT-S07） */
    void SetPlaybackGated(bool gated);

    /** 当前状态（/api/status 用，线程安全） */
    Status GetStatus() const;

    /** 阶段字符串（协议字段 phase） */
    static const char* PhaseToString(Phase phase);

    /** 是否处于召唤流程中（/api/mode 拒绝判断用） */
    bool IsActive() const;

private:
    AiDogSummon() = default;
    AiDogSummon(const AiDogSummon&) = delete;
    AiDogSummon& operator=(const AiDogSummon&) = delete;

    void SetPhase(Phase phase, const char* message);
    void PlayBarkAndGreet();

    /** 低速巡航占空比（R-F1 降级档 30% / 标定值） */
    static int CruiseDutyPercent();

    // S2 各子步（在状态机任务里按 sub_step 节拍执行）
    void DoVisionCheck();
    void DoRescan();

    /** 接近段触发判定：DOA 稳定 + 近场能量/测距 */
    bool IsApproachTriggered() const;

    static void StateMachineTask(void* arg);
    void StateMachineLoop();

    mutable std::mutex mutex_;
    bool enabled_ = false;
    bool shutdown_ = false;

    Status status_;
    int64_t summon_start_us_ = 0;
    int64_t phase_deadline_us_ = 0;   // 当前阶段截止（毫秒级精度）
    int64_t sub_deadline_us_ = 0;     // 子步截止（对准/听）
    int64_t rescan_start_us_ = 0;     // S5 重搜起点
    int64_t last_nudge_us_ = 0;       // S5 上次点动时刻
    int rescan_count_ = 0;            // S5 进入次数（上限 2，防无限循环）
    int vision_tries_ = 0;
    bool approach_vision_done_ = false;
    float last_angle_error_deg_ = 0.0f;
    int consecutive_lost_ = 0;        // 连续听不到次数（丢源判定）
    bool low_battery_announced_ = false;

    TaskHandle_t task_ = nullptr;
    StaticTask_t* task_buffer_ = nullptr;
    StackType_t* task_stack_ = nullptr;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_SUMMON_H
