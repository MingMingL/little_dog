#ifndef AI_DOG_BATTERY_H
#define AI_DOG_BATTERY_H

#include <cstdint>
#include <functional>

namespace aidog {

/**
 * 电池电压监测（2S 锂电，GPIO8 ADC + 电阻分压）。
 *
 * 为什么不用 main/boards/common/adc_battery_monitor.cc：那个封装只给百分比，
 * 而且默认 OCV 曲线是**单节**锂电（4.2V 满）。排查"电池供电反复重启"要的是**电压**，
 * 2S 的曲线也不一样，所以这里自己读 ADC。
 *
 * 分档（门限在 config.h，2S 锂电）：
 *   Unknown  —— 读数不在合理区间（分压没接/接错），不上报、不动作
 *   Normal   —— > WARN_V
 *   Warn     —— 日志 + OLED 提示
 *   Restrict —— 拒绝进入巡逻/看门/遥控（只留待机和聊天）
 *   Cutoff   —— 停电机 + 提示 + 深度睡眠（赶在 5V 轨崩掉之前）
 */
class BatteryMonitor {
public:
    enum class Level { Unknown, Normal, Warn, Restrict, Cutoff };

    static BatteryMonitor& GetInstance();

    /** 初始化 ADC 与周期采样（2 秒一次）。ADC 不可用时退化为 Unknown。 */
    void Init();

    float voltage() const { return voltage_; }   // V；无效时 -1
    int percent() const { return percent_; }     // 0..100；无效时 -1
    Level level() const { return level_; }
    bool valid() const { return valid_; }
    uint32_t raw_mv() const { return raw_mv_; }  // ADC 引脚上的原始毫伏（标定用）

    using LevelChangedHandler = std::function<void(Level, float, int)>;
    void SetOnLevelChanged(LevelChangedHandler handler);

private:
    BatteryMonitor() = default;
    BatteryMonitor(const BatteryMonitor&) = delete;
    BatteryMonitor& operator=(const BatteryMonitor&) = delete;

    void Sample();
    void SetLevel(Level level);

    static void TimerCallback(void* arg);
    static int PercentForVoltage(float voltage);

    void* adc_handle_ = nullptr;
    void* cali_handle_ = nullptr;
    void* timer_handle_ = nullptr;

    float voltage_ = -1.0f;
    int percent_ = -1;
    uint32_t raw_mv_ = 0;
    bool valid_ = false;
    Level level_ = Level::Unknown;
    // Restrict/Cutoff 的连续确认计数（防 ADC 抖动误关机）
    Level pending_level_ = Level::Unknown;
    int pending_count_ = 0;
    int sample_count_ = 0;

    LevelChangedHandler on_level_changed_;
};

const char* BatteryLevelText(BatteryMonitor::Level level);

}  // namespace aidog

#endif  // AI_DOG_BATTERY_H
