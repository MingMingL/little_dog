#ifndef AI_DOG_LINE_PCF8574_H
#define AI_DOG_LINE_PCF8574_H

#include <cstdint>
#include <mutex>

#include <esp_err.h>
#include <driver/i2c_master.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * 巡线 5 路 I²C 化（SW-101）。
 *
 * 硬件改动（最终实施方案 §3.1 第 2 条）：5 路 TCRT5000 数字输出从
 * GPIO17/18/38/39/40 改接到 PCF8574 的 P0–P4，PCF8574 挂现有 I²C
 * （GPIO41/42，与 VL53L0X 0x29 并联），地址 0x20。
 *
 * 为什么要改：GPIO17/18/38/39/40 要释放给 4 麦方阵（SD2/3/4）和
 * ArduCAM（MISO/CS）——召唤功能的引脚前提。巡线功能本身不变。
 *
 * 电气注意（R-F8）：TCRT5000 数字输出多为开漏，与 PCF8574 准双向口兼容；
 * 读不稳时在模块端加 10k 上拉。本模块带消抖与轮询节流。
 *
 * 位定义（与 V1 GPIO 版的语义对齐）：bit0..bit4 = 传感器 1..5 号
 * （左→右），1 = 检测到线（TCRT5000 数字输出低电平有效时由硬件反相，
 * 固件见到的就是"1=在线"）。
 */
class LineSensorPcf8574 {
public:
    static LineSensorPcf8574& GetInstance();

    /** 初始化 I²C 并扫描 0x20（T1）。失败不致命：巡逻不可用，其余功能照常。 */
    esp_err_t Initialize();

    /** 关闭（进配网模式等） */
    void Shutdown();

    bool IsEnabled() const { return enabled_; }

    /** 读一次 5 路（bit0..bit4 = 传感器 1..5）。失败返回 false，out 保持上次值。 */
    bool Read(uint8_t& out);

    /** 消抖后的稳定值（连续 N 次相同才更新，N = kDebounceCount） */
    uint8_t GetStable() const;

    /** 最近一次 I²C 错误码（调试用） */
    esp_err_t GetLastError() const;

private:
    LineSensorPcf8574() = default;
    LineSensorPcf8574(const LineSensorPcf8574&) = delete;
    LineSensorPcf8574& operator=(const LineSensorPcf8574&) = delete;

    bool ReadRaw(uint8_t& out);

    static constexpr int kDebounceCount = 3;

    mutable std::mutex mutex_;
    bool enabled_ = false;
    i2c_master_dev_handle_t dev_ = nullptr;
    uint8_t last_raw_ = 0;
    uint8_t stable_ = 0;
    int same_count_ = 0;
    esp_err_t last_error_ = ESP_OK;
    uint32_t reads_ = 0;
    uint32_t errors_ = 0;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_LINE_PCF8574_H
