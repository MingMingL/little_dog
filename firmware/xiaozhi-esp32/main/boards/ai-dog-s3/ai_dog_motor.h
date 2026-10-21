#ifndef AI_DOG_MOTOR_H
#define AI_DOG_MOTOR_H

#include <cstdint>
#include <mutex>

#include <sdkconfig.h>

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
#include <esp_timer.h>
#endif

namespace aidog {

/**
 * TB6612FNG 双路直流电机驱动（左轮 + 右轮），差速转向的底层执行层。
 *
 * 引脚见 config.h：左 PWM=GPIO11 / IN1=12 / IN2=13，右 PWM=GPIO14 / IN1=21 / IN2=47，
 * STANDBY=GPIO10（低电平关断整个驱动芯片）。
 *
 * 设计约束（很重要）：
 * - 本模块**只做"给定的左右轮速度 → 电平/PWM"的翻译**，不做任何决策；
 *   谁能动、动多快，全部由模式状态机（ControlServer）通过回调决定，
 *   这样"控制指令必须经状态机"的约定才不会被绕过。
 * - 所有写 GPIO 的方法都加锁：HTTP 回调线程和看门狗定时器线程会并发调用 Stop()。
 * - 初始化失败（比如 PWM 资源不够）时 enabled_ 保持 false，SetSpeeds() 直接返回，
 *   不会因为电机没接就让整个固件崩掉——设备还能正常跑语音和状态上报。
 */
class MotorDriver {
public:
    static MotorDriver& GetInstance();

    /** 初始化 GPIO + LEDC PWM。重复调用安全（只做一次）。 */
    void Initialize();

    /** 左右轮速度，范围 -255..255；负数为反转，0 为停止（PWM=0 且方向线清零） */
    void SetSpeeds(int left, int right);

    /** 立即停止两轮 */
    void Stop();

    bool IsEnabled() const { return enabled_; }

    /** 当前左右轮速度（供状态/调试用） */
    void GetSpeeds(int& left, int& right) const;

    /** 是否正在动（SW-205 状态上报：/api/status 的 moving） */
    bool IsMoving() const { return last_left_ != 0 || last_right_ != 0; }

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
    // ---- Summon 寻声专用（SW-205；V1 遥控不用）----

    /**
     * 低速巡航档：双轮同速前进，duty_percent 0..100（内部按 8bit 换算）。
     * 约 0.2m/s（Kconfig AI_DOG_SUMMON_CRUISE_DUTY 标定值）。
     * 传 0 表示停车。
     */
    void CruiseForward(int duty_percent);

    /**
     * 点动纠偏：原地差速短脉冲。dir < 0 左转 / dir > 0 右转；
     * 脉冲宽度 Kconfig AI_DOG_MOTOR_NUDGE_MS，到点自动停（内部定时器兜底，
     * 即使调用方卡住也不会一直转——安全设计）。
     * 返回本次脉冲剩余毫秒数（供调用方节拍对齐），失败返回 -1。
     */
    int NudgeTurn(int dir, int duty_percent, int duration_ms);

    /** 当前巡航/点动占空比（0..100），未动为 0 */
    int GetCruiseDuty() const;
#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

private:
    MotorDriver() = default;
    MotorDriver(const MotorDriver&) = delete;
    MotorDriver& operator=(const MotorDriver&) = delete;

    /** 单轮输出：先定方向 IN1/IN2，再给 PWM 占空比 */
    void ApplyOne(bool is_left, int speed);

    mutable std::mutex mutex_;
    bool initialized_ = false;
    bool enabled_ = false;
    int last_left_ = 0;
    int last_right_ = 0;

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
    // 点动脉冲兜底定时器（SW-205）：到点强制停车，调用方卡死也不一直转
    esp_timer_handle_t nudge_timer_ = nullptr;
    static void NudgeTimerCallback(void* arg);
    int cruise_duty_ = 0;  // 当前巡航/点动占空比（0..100）
#endif
};

}  // namespace aidog

#endif  // AI_DOG_MOTOR_H
