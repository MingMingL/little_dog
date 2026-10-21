#include "ai_dog_line_pcf8574.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_i2c.h"

#include <esp_log.h>
#include <driver/i2c_master.h>

#define TAG "line_i2c"

namespace aidog {

LineSensorPcf8574& LineSensorPcf8574::GetInstance() {
    static LineSensorPcf8574 instance;
    return instance;
}

esp_err_t LineSensorPcf8574::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }
    esp_err_t err = AiDogI2c::GetInstance().Initialize();
    if (err != ESP_OK) {
        return err;
    }
    i2c_master_bus_handle_t bus = AiDogI2c::GetInstance().GetBus();

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = LINE_SENSOR_PCF8574_ADDR,
        .scl_speed_hz = 400 * 1000,
        .scl_wait_us = 0,
        .flags = { .disable_ack_check = 0 },
    };
    i2c_master_dev_handle_t dev = nullptr;
    err = i2c_master_bus_add_device(bus, &dev_cfg, &dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "PCF8574 0x%02X 添加失败: %d（T1 失败：模块没焊好或地址冲突）",
                 LINE_SENSOR_PCF8574_ADDR, err);
        return err;
    }

    // 全部写成 1 = 5 路准输入（开漏 TCRT5000 由外部决定电平）；
    // PCF8574 上电锁存默认 0xFF，这里显式写一次确保语义。
    uint8_t all_ones = 0xFF;
    err = i2c_master_transmit(dev, &all_ones, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "PCF8574 写 0xFF 失败: %d", err);
        i2c_master_bus_rm_device(dev);
        return err;
    }
    dev_ = dev;

    uint8_t first = 0;
    if (!ReadRaw(first)) {
        ESP_LOGE(TAG, "PCF8574 首次读失败（R-F8：模块端加 10k 上拉再试）");
        i2c_master_bus_rm_device(dev);
        dev_ = nullptr;
        return last_error_;
    }

    enabled_ = true;
    last_raw_ = first;
    stable_ = first;
    same_count_ = kDebounceCount;
    ESP_LOGI(TAG, "巡线 5 路就绪：PCF8574 0x%02X，初值 0x%02X（bit0..bit4 = 左→右）",
             LINE_SENSOR_PCF8574_ADDR, first);
    return ESP_OK;
}

void LineSensorPcf8574::Shutdown() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (dev_ != nullptr) {
        i2c_master_bus_rm_device(dev_);
        dev_ = nullptr;
    }
    enabled_ = false;
    ESP_LOGI(TAG, "巡线传感器已关闭");
}

bool LineSensorPcf8574::ReadRaw(uint8_t& out) {
    if (dev_ == nullptr) {
        last_error_ = ESP_ERR_INVALID_STATE;
        errors_++;
        return false;
    }
    uint8_t value = 0;
    esp_err_t err = i2c_master_receive(dev_, &value, 1, 50);
    reads_++;
    if (err != ESP_OK) {
        last_error_ = err;
        errors_++;
        return false;
    }
    out = static_cast<uint8_t>(value & 0x1F);  // 只取 P0..P4
    return true;
}

bool LineSensorPcf8574::Read(uint8_t& out) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_) {
        return false;
    }
    uint8_t raw = 0;
    if (!ReadRaw(raw)) {
        return false;
    }
    last_raw_ = raw;
    if (raw == stable_) {
        same_count_ = kDebounceCount;
    } else if (--same_count_ <= 0) {
        ESP_LOGI(TAG, "巡线值跳变 0x%02X -> 0x%02X（连续 %d 次确认）",
                 stable_, raw, kDebounceCount);
        stable_ = raw;
        same_count_ = kDebounceCount;
    }
    out = stable_;
    return true;
}

uint8_t LineSensorPcf8574::GetStable() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return stable_;
}

esp_err_t LineSensorPcf8574::GetLastError() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_error_;
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
