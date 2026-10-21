#include "ai_dog_vl53l0x.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_i2c.h"

#include <esp_log.h>
#include <driver/i2c_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#define TAG "summon"  // 测距传感器归 summon 组（TAG 规范 SW-003：summon/doa/mic_array/vision/line_i2c）

namespace aidog {

// ---- VL53L0X 寄存器（ datasheet + ST API ）----
static constexpr uint8_t REG_IDENTIFICATION_MODEL_ID       = 0xC0;
static constexpr uint8_t REG_VHV_CONFIG_PAD_SCL_SDA_EXTSUP = 0x89;
static constexpr uint8_t REG_MSRC_CONFIG_CONTROL           = 0x60;
static constexpr uint8_t REG_GLOBAL_CONFIG_SPAD_ENABLES    = 0xB0;
static constexpr uint8_t REG_DYNAMIC_SPAD_REF_EN_START     = 0x4F;
static constexpr uint8_t REG_DYNAMIC_SPAD_NUM_REQUESTED    = 0x4E;
static constexpr uint8_t REG_GLOBAL_CONFIG_REF_EN_START    = 0xB6;
static constexpr uint8_t REG_SYSTEM_SEQUENCE_CONFIG        = 0x01;
static constexpr uint8_t REG_SYSTEM_INTERRUPT_CONFIG_GPIO  = 0x0A;
static constexpr uint8_t REG_GPIO_HV_MUX_ACTIVE_HIGH       = 0x84;
static constexpr uint8_t REG_SYSTEM_INTERRUPT_CLEAR        = 0x0B;
static constexpr uint8_t REG_RESULT_INTERRUPT_STATUS       = 0x13;
static constexpr uint8_t REG_SYSRANGE_START                = 0x00;
static constexpr uint8_t REG_RESULT_RANGE_STATUS           = 0x14;

Vl53l0xSensor& Vl53l0xSensor::GetInstance() {
    static Vl53l0xSensor instance;
    return instance;
}

// ---------------------------------------------------------------------------
// I²C 读写
// ---------------------------------------------------------------------------

bool Vl53l0xSensor::WriteReg(uint8_t reg, uint8_t value) {
    if (dev_ == nullptr) return false;
    uint8_t buf[2] = {reg, value};
    return i2c_master_transmit(static_cast<i2c_master_dev_handle_t>(dev_), buf, 2, 50) == ESP_OK;
}

bool Vl53l0xSensor::WriteRegs(uint8_t reg, const uint8_t* data, size_t len) {
    if (dev_ == nullptr) return false;
    uint8_t buf[8];
    if (len > sizeof(buf) - 1) return false;
    buf[0] = reg;
    for (size_t i = 0; i < len; ++i) buf[i + 1] = data[i];
    return i2c_master_transmit(static_cast<i2c_master_dev_handle_t>(dev_), buf, len + 1, 50) == ESP_OK;
}

uint8_t Vl53l0xSensor::ReadReg(uint8_t reg) {
    if (dev_ == nullptr) return 0;
    uint8_t value = 0;
    if (i2c_master_transmit_receive(static_cast<i2c_master_dev_handle_t>(dev_), &reg, 1,
                                    &value, 1, 50) != ESP_OK) {
        return 0;
    }
    return value;
}

bool Vl53l0xSensor::ReadRegs(uint8_t reg, uint8_t* data, size_t len) {
    if (dev_ == nullptr) return false;
    return i2c_master_transmit_receive(static_cast<i2c_master_dev_handle_t>(dev_), &reg, 1,
                                       data, len, 50) == ESP_OK;
}

uint16_t Vl53l0xSensor::ReadReg16(uint8_t reg) {
    uint8_t buf[2] = {0, 0};
    if (!ReadRegs(reg, buf, 2)) return 0;
    return static_cast<uint16_t>((buf[0] << 8) | buf[1]);
}

// ---------------------------------------------------------------------------
// 初始化（ST API 最小集）
// ---------------------------------------------------------------------------

bool Vl53l0xSensor::GetSpadInfo(uint8_t& count, bool& is_aperture) {
    WriteReg(0x80, 0x01);
    WriteReg(0xFF, 0x01);
    WriteReg(0x00, 0x00);

    WriteReg(0xFF, 0x06);
    WriteReg(0x83, ReadReg(0x83) | 0x04);
    WriteReg(0xFF, 0x07);
    WriteReg(0x81, 0x01);

    WriteReg(0x80, 0x01);

    WriteReg(0x94, 0x6B);
    WriteReg(0x83, 0x00);
    for (int i = 0; i < 200 && ReadReg(0x83) == 0x00; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    if (ReadReg(0x83) == 0x00) {
        return false;
    }
    WriteReg(0x83, 0x01);
    uint8_t tmp = ReadReg(0x92);
    count = tmp & 0x7F;
    is_aperture = (tmp >> 7) & 0x01;

    WriteReg(0x81, 0x00);
    WriteReg(0xFF, 0x06);
    WriteReg(0x83, ReadReg(0x83) & ~0x04);
    WriteReg(0xFF, 0x01);
    WriteReg(0x00, 0x01);
    WriteReg(0xFF, 0x00);
    WriteReg(0x80, 0x00);
    return true;
}

bool Vl53l0xSensor::PerformSingleRefCalibration(uint8_t vhv_init_byte) {
    WriteReg(REG_SYSRANGE_START, 0x01 | vhv_init_byte);
    for (int i = 0; i < 250; ++i) {
        if ((ReadReg(REG_RESULT_INTERRUPT_STATUS) & 0x07) != 0) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    if ((ReadReg(REG_RESULT_INTERRUPT_STATUS) & 0x07) == 0) {
        return false;
    }
    WriteReg(REG_SYSTEM_INTERRUPT_CLEAR, 0x01);
    WriteReg(REG_SYSRANGE_START, 0x00);
    return true;
}

esp_err_t Vl53l0xSensor::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }
    esp_err_t err = AiDogI2c::GetInstance().Initialize();
    if (err != ESP_OK) {
        return err;
    }
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = VL53L0X_I2C_ADDR,
        .scl_speed_hz = 400 * 1000,
        .scl_wait_us = 0,
        .flags = { .disable_ack_check = 0 },
    };
    i2c_master_dev_handle_t dev = nullptr;
    err = i2c_master_bus_add_device(AiDogI2c::GetInstance().GetBus(), &dev_cfg, &dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "VL53L0X 0x%02X 添加失败: %d", VL53L0X_I2C_ADDR, err);
        return err;
    }
    dev_ = dev;

    // ---- DataInit ----
    if (ReadReg(REG_IDENTIFICATION_MODEL_ID) != 0xEE) {
        ESP_LOGE(TAG, "VL53L0X 型号校验失败（读到 0x%02X，期望 0xEE）", ReadReg(REG_IDENTIFICATION_MODEL_ID));
        i2c_master_bus_rm_device(dev);
        dev_ = nullptr;
        return ESP_ERR_INVALID_RESPONSE;
    }
    WriteReg(0x88, 0x00);  // I2C standard mode
    WriteReg(0x80, 0x01);
    WriteReg(0xFF, 0x01);
    WriteReg(0x00, 0x00);
    stop_variable_ = ReadReg(0x91);
    WriteReg(0x00, 0x01);
    WriteReg(0xFF, 0x00);
    WriteReg(0x80, 0x00);
    WriteReg(REG_MSRC_CONFIG_CONTROL, ReadReg(REG_MSRC_CONFIG_CONTROL) | 0x12);
    // final range signal rate limit 0.25 MCPS（默认值，写一次确保）
    WriteReg(0x44, 32);   // high byte
    WriteReg(0x45, 200);  // low byte -> 0.25 * (1<<7) ≈ 32.0
    WriteReg(REG_SYSTEM_SEQUENCE_CONFIG, 0xFF);

    // ---- StaticInit：SPAD ----
    uint8_t spad_count = 0;
    bool spad_aperture = false;
    if (!GetSpadInfo(spad_count, spad_aperture)) {
        ESP_LOGE(TAG, "VL53L0X SPAD 信息读取失败");
        i2c_master_bus_rm_device(dev);
        dev_ = nullptr;
        return ESP_FAIL;
    }
    uint8_t ref_spad_map[6];
    ReadRegs(REG_GLOBAL_CONFIG_SPAD_ENABLES, ref_spad_map, 6);
    WriteReg(0xFF, 0x01);
    WriteReg(REG_DYNAMIC_SPAD_REF_EN_START, 0x00);
    WriteReg(REG_DYNAMIC_SPAD_NUM_REQUESTED, 0x2C);
    WriteReg(0xFF, 0x00);
    WriteReg(REG_GLOBAL_CONFIG_REF_EN_START, 0xB4);
    uint8_t first_spad_to_enable = spad_aperture ? 12 : 0;
    uint8_t spads_enabled = 0;
    for (uint8_t i = 0; i < 48; ++i) {
        if (i < first_spad_to_enable || spads_enabled == spad_count) {
            ref_spad_map[i / 8] &= ~(1 << (i % 8));
        } else if ((ref_spad_map[i / 8] >> (i % 8)) & 0x1) {
            spads_enabled++;
        }
    }
    WriteRegs(REG_GLOBAL_CONFIG_SPAD_ENABLES, ref_spad_map, 6);

    // ---- StaticInit：DefaultTuningSettings（vl53l0x_tuning.h）----
    WriteReg(0xFF, 0x01); WriteReg(0x00, 0x00);
    WriteReg(0xFF, 0x00); WriteReg(0x09, 0x00); WriteReg(0x10, 0x00); WriteReg(0x11, 0x00);
    WriteReg(0x24, 0x01); WriteReg(0x25, 0xFF); WriteReg(0x75, 0x00);
    WriteReg(0xFF, 0x01); WriteReg(0x4E, 0x2C); WriteReg(0x48, 0x00); WriteReg(0x30, 0x20);
    WriteReg(0xFF, 0x00); WriteReg(0x30, 0x09); WriteReg(0x54, 0x00); WriteReg(0x31, 0x04);
    WriteReg(0x32, 0x03); WriteReg(0x40, 0x83); WriteReg(0x46, 0x25); WriteReg(0x60, 0x00);
    WriteReg(0x27, 0x00); WriteReg(0x50, 0x06); WriteReg(0x51, 0x00); WriteReg(0x52, 0x96);
    WriteReg(0x56, 0x08); WriteReg(0x57, 0x30); WriteReg(0x61, 0x00); WriteReg(0x62, 0x00);
    WriteReg(0x64, 0x00); WriteReg(0x65, 0x00); WriteReg(0x66, 0xA0);
    WriteReg(0xFF, 0x01); WriteReg(0x22, 0x32); WriteReg(0x47, 0x14); WriteReg(0x49, 0xFF);
    WriteReg(0x4A, 0x00);
    WriteReg(0xFF, 0x00); WriteReg(0x7A, 0x0A); WriteReg(0x7B, 0x00); WriteReg(0x78, 0x21);
    WriteReg(0xFF, 0x01); WriteReg(0x23, 0x34); WriteReg(0x42, 0x00); WriteReg(0x44, 0xFF);
    WriteReg(0x45, 0x26); WriteReg(0x46, 0x05); WriteReg(0x40, 0x40); WriteReg(0x0E, 0x06);
    WriteReg(0x20, 0x1A); WriteReg(0x43, 0x40);
    WriteReg(0xFF, 0x00); WriteReg(0x34, 0x03); WriteReg(0x35, 0x44);
    WriteReg(0xFF, 0x01); WriteReg(0x31, 0x04); WriteReg(0x4B, 0x09); WriteReg(0x4C, 0x05);
    WriteReg(0x4D, 0x04);
    WriteReg(0xFF, 0x00); WriteReg(0x44, 0x00); WriteReg(0x45, 0x20); WriteReg(0x47, 0x08);
    WriteReg(0x48, 0x28); WriteReg(0x67, 0x00); WriteReg(0x70, 0x04); WriteReg(0x71, 0x01);
    WriteReg(0x72, 0xFE); WriteReg(0x76, 0x00); WriteReg(0x77, 0x00);
    WriteReg(0xFF, 0x01); WriteReg(0x0D, 0x01);
    WriteReg(0xFF, 0x00); WriteReg(0x80, 0x01); WriteReg(0x01, 0xF8);
    WriteReg(0xFF, 0x01); WriteReg(0x8E, 0x01); WriteReg(0x00, 0x01);
    WriteReg(0xFF, 0x00); WriteReg(0x80, 0x00);

    // ---- 中断配置：new sample ready ----
    WriteReg(REG_SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04);
    WriteReg(REG_GPIO_HV_MUX_ACTIVE_HIGH, ReadReg(REG_GPIO_HV_MUX_ACTIVE_HIGH) & ~0x10);
    WriteReg(REG_SYSTEM_INTERRUPT_CLEAR, 0x01);

    // ---- 序列：TCC + DSS + MSRC + PRE + FINAL（默认 timing budget）----
    WriteReg(REG_SYSTEM_SEQUENCE_CONFIG, 0xE8);

    // ---- PerformRefCalibration ----
    WriteReg(REG_SYSTEM_SEQUENCE_CONFIG, 0x01);
    if (!PerformSingleRefCalibration(0x40)) {
        ESP_LOGE(TAG, "VL53L0X VHV 参考校准失败");
        i2c_master_bus_rm_device(dev);
        dev_ = nullptr;
        return ESP_FAIL;
    }
    WriteReg(REG_SYSTEM_SEQUENCE_CONFIG, 0x02);
    if (!PerformSingleRefCalibration(0x00)) {
        ESP_LOGE(TAG, "VL53L0X 相位参考校准失败");
        i2c_master_bus_rm_device(dev);
        dev_ = nullptr;
        return ESP_FAIL;
    }
    WriteReg(REG_SYSTEM_SEQUENCE_CONFIG, 0xE8);

    enabled_ = true;
    ESP_LOGI(TAG, "VL53L0X 就绪：0x%02X，单次测距（SPAD count=%d aperture=%d）",
             VL53L0X_I2C_ADDR, spad_count, spad_aperture ? 1 : 0);
    return ESP_OK;
}

void Vl53l0xSensor::Shutdown() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (dev_ != nullptr) {
        i2c_master_bus_rm_device(static_cast<i2c_master_dev_handle_t>(dev_));
        dev_ = nullptr;
    }
    enabled_ = false;
    ESP_LOGI(TAG, "VL53L0X 已关闭");
}

uint16_t Vl53l0xSensor::ReadMillimeters(int timeout_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_ || dev_ == nullptr) {
        return 65535;
    }
    // 单次测距触发序列
    WriteReg(0x80, 0x01);
    WriteReg(0xFF, 0x01);
    WriteReg(0x00, 0x00);
    WriteReg(0x91, stop_variable_);
    WriteReg(0x00, 0x01);
    WriteReg(0xFF, 0x00);
    WriteReg(0x80, 0x00);
    WriteReg(REG_SYSRANGE_START, 0x01);

    // 等 start 位清除（测量完成）
    const int poll_ms = 2;
    for (int elapsed = 0; elapsed < timeout_ms; elapsed += poll_ms) {
        if ((ReadReg(REG_SYSRANGE_START) & 0x01) == 0) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(poll_ms));
    }
    if (ReadReg(REG_SYSRANGE_START) & 0x01) {
        timeout_count_++;
        return 65535;
    }

    // 等 new sample ready 中断
    for (int elapsed = 0; elapsed < timeout_ms; elapsed += poll_ms) {
        if ((ReadReg(REG_RESULT_INTERRUPT_STATUS) & 0x07) != 0) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(poll_ms));
    }
    if ((ReadReg(REG_RESULT_INTERRUPT_STATUS) & 0x07) == 0) {
        timeout_count_++;
        return 65535;
    }
    uint16_t range = ReadReg16(REG_RESULT_RANGE_STATUS + 10);
    WriteReg(REG_SYSTEM_INTERRUPT_CLEAR, 0x01);
    ok_count_++;
    return range;
}

uint32_t Vl53l0xSensor::GetOkCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return ok_count_;
}

uint32_t Vl53l0xSensor::GetTimeoutCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return timeout_count_;
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
