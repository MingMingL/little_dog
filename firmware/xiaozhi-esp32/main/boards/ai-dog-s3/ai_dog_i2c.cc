#include "ai_dog_i2c.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"

#include <esp_log.h>
#include <driver/i2c_master.h>

#define TAG "line_i2c"

namespace aidog {

AiDogI2c& AiDogI2c::GetInstance() {
    static AiDogI2c instance;
    return instance;
}

esp_err_t AiDogI2c::Initialize() {
    if (bus_ != nullptr) {
        return ESP_OK;
    }
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = DOG_I2C_SDA_GPIO,
        .scl_io_num = DOG_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .intr_priority = 0,
        .trans_queue_depth = 0,
        .flags = {
            .enable_internal_pullup = true,
            .allow_pd = false,
        },
    };
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &bus_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I²C 总线初始化失败: %d（SDA=GPIO%d SCL=GPIO%d）",
                 err, DOG_I2C_SDA_GPIO, DOG_I2C_SCL_GPIO);
        bus_ = nullptr;
        return err;
    }
    ESP_LOGI(TAG, "I²C 总线就绪：SDA=GPIO%d SCL=GPIO%d 400kHz",
             DOG_I2C_SDA_GPIO, DOG_I2C_SCL_GPIO);
    return ESP_OK;
}

void AiDogI2c::Shutdown() {
    if (bus_ == nullptr) {
        return;
    }
    i2c_del_master_bus(bus_);
    bus_ = nullptr;
    ESP_LOGI(TAG, "I²C 总线已关闭");
}

int AiDogI2c::Scan(uint8_t* found_addrs, int max_count) {
    if (bus_ == nullptr || found_addrs == nullptr || max_count <= 0) {
        return 0;
    }
    int found = 0;
    for (uint16_t addr = 0x08; addr <= 0x77; ++addr) {
        // 探测：临时建设备句柄发 1 字节读；ESP_OK 即在位
        i2c_device_config_t dev_cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = static_cast<uint16_t>(addr),
            .scl_speed_hz = 400 * 1000,
            .scl_wait_us = 0,
            .flags = { .disable_ack_check = 0 },
        };
        i2c_master_dev_handle_t dev = nullptr;
        if (i2c_master_bus_add_device(bus_, &dev_cfg, &dev) != ESP_OK) {
            continue;
        }
        uint8_t dummy = 0;
        esp_err_t err = i2c_master_receive(dev, &dummy, 1, 20);
        i2c_master_bus_rm_device(dev);
        if (err == ESP_OK || err == ESP_ERR_TIMEOUT) {
            // TIMEOUT 也可能是在位但无数据返回；PCF8574 读会直接给数据，
            // 保守起见只把 ESP_OK 记作在位，避免误报。
            if (err == ESP_OK && found < max_count) {
                found_addrs[found++] = static_cast<uint8_t>(addr);
                ESP_LOGI(TAG, "T1 扫描：发现 I²C 设备 0x%02X", addr);
            }
        }
    }
    return found;
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
