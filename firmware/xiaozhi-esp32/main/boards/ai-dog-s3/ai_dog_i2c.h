#ifndef AI_DOG_I2C_H
#define AI_DOG_I2C_H

#include <esp_err.h>
#include <driver/i2c_master.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * AI Dog 传感器 I²C 总线（GPIO41/42，400kHz）。
 *
 * VL53L0X(0x29) / PCF8574(0x20) / 可选 MPU6050(0x68) 共用。
 * 单例持有总线句柄；各设备模块自行 i2c_master_bus_add_device。
 * 重复 Initialize 安全；Shutdown 只在整机下电/重配网时用。
 */
class AiDogI2c {
public:
    static AiDogI2c& GetInstance();

    esp_err_t Initialize();
    void Shutdown();

    /** 总线句柄（未初始化返回 nullptr） */
    i2c_master_bus_handle_t GetBus() const { return bus_; }
    bool IsEnabled() const { return bus_ != nullptr; }

    /**
     * T1 用：扫描 0x08..0x77，返回发现的地址个数，found_addrs 输出地址列表。
     * 期望见到 0x20（PCF8574）与 0x29（VL53L0X）。
     */
    int Scan(uint8_t* found_addrs, int max_count);

private:
    AiDogI2c() = default;
    AiDogI2c(const AiDogI2c&) = delete;
    AiDogI2c& operator=(const AiDogI2c&) = delete;

    i2c_master_bus_handle_t bus_ = nullptr;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_I2C_H
