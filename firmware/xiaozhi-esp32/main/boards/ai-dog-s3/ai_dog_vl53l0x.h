#ifndef AI_DOG_VL53L0X_H
#define AI_DOG_VL53L0X_H

#include <cstdint>
#include <mutex>

#include <esp_err.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * VL53L0X 前向测程（SW-204 避障 + 接近段触发）。
 *
 * I²C 0x29，挂 AiDogI2c 总线（GPIO41/42）。单次测距（single-shot）：
 * Summon 状态机在"停-听-走"的停车段调用，阻塞约 30~40ms。
 *
 * 初始化序列基于 ST VL53L0X API（STSW-IMG005）的 DataInit + StaticInit +
 * PerformRefCalibration 最小集（与 pololu/vl53l0x-arduino 同源，MIT）。
 * 未实现：自定义 timing budget（用默认值）、多传感器/地址重映射。
 */
class Vl53l0xSensor {
public:
    static Vl53l0xSensor& GetInstance();

    /** 初始化 + 参考校准。失败不致命（避障降级：只信 DOA），返回错误码。 */
    esp_err_t Initialize();

    void Shutdown();
    bool IsEnabled() const { return enabled_; }

    /**
     * 单次测距，返回毫米。65535 = 超时/无效。
     * 线程安全（内部加锁，I²C 访问串行化）。
     */
    uint16_t ReadMillimeters(int timeout_ms = 100);

    /** 有效读次数 / 超时次数（调试） */
    uint32_t GetOkCount() const;
    uint32_t GetTimeoutCount() const;

private:
    Vl53l0xSensor() = default;
    Vl53l0xSensor(const Vl53l0xSensor&) = delete;
    Vl53l0xSensor& operator=(const Vl53l0xSensor&) = delete;

    bool WriteReg(uint8_t reg, uint8_t value);
    bool WriteRegs(uint8_t reg, const uint8_t* data, size_t len);
    uint8_t ReadReg(uint8_t reg);
    bool ReadRegs(uint8_t reg, uint8_t* data, size_t len);
    uint16_t ReadReg16(uint8_t reg);
    bool GetSpadInfo(uint8_t& count, bool& is_aperture);
    bool PerformSingleRefCalibration(uint8_t vhv_init_byte);

    mutable std::mutex mutex_;
    bool enabled_ = false;
    void* dev_ = nullptr;  // i2c_master_dev_handle_t
    uint8_t stop_variable_ = 0;
    uint32_t ok_count_ = 0;
    uint32_t timeout_count_ = 0;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_VL53L0X_H
