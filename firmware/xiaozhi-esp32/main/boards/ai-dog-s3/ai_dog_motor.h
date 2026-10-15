#ifndef AI_DOG_MOTOR_H
#define AI_DOG_MOTOR_H

#include <cstdint>
#include <mutex>

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
};

}  // namespace aidog

#endif  // AI_DOG_MOTOR_H
