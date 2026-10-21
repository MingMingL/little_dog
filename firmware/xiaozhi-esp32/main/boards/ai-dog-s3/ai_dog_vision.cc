#include "ai_dog_vision.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_i2c.h"
#include "ov2640_regs_table.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <driver/spi_master.h>
#include <driver/i2c_master.h>
#include <driver/gpio.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <cstring>

// 人形检测（esp-dl PICO 模型，rodata 内嵌）
#include "pedestrian_detect.hpp"
#include "dl_image_jpeg.hpp"
#include "dl_image_process.hpp"

#define TAG "vision"

namespace aidog {

// ---- ArduChip 寄存器（docs.arducam.com ArduChip SPI 协议）----
static constexpr uint8_t kArduchipTest1 = 0x00;
static constexpr uint8_t kArduchipMode = 0x02;
static constexpr uint8_t kArduchipFifo = 0x04;
static constexpr uint8_t kFifoClearMask = 0x01;
static constexpr uint8_t kFifoStartMask = 0x02;
static constexpr uint8_t kBurstFifoRead = 0x3C;
static constexpr uint8_t kArduchipTrig = 0x41;
static constexpr uint8_t kCapDoneMask = 0x08;
static constexpr uint8_t kFifoSize1 = 0x42;
static constexpr uint8_t kFifoSize2 = 0x43;
static constexpr uint8_t kFifoSize3 = 0x44;

// OV2640 I²C 7 位地址
static constexpr uint8_t kOv2640I2cAddr = 0x30;
// OV2640 芯片 ID 高/低字节寄存器
static constexpr uint8_t kOv2640ChipidHigh = 0x0A;
static constexpr uint8_t kOv2640ChipidLow = 0x0B;

// 单帧 JPEG 上限（160x120 JPEG 一般 < 20KB，留足余量）
static constexpr size_t kJpegMaxLen = 64 * 1024;

AiDogVision& AiDogVision::GetInstance() {
    static AiDogVision instance;
    return instance;
}

// ---------------------------------------------------------------------------
// SPI / I²C 原语
// ---------------------------------------------------------------------------

uint8_t AiDogVision::ChipReadReg(uint8_t addr) {
    if (spi_dev_ == nullptr) return 0;
    auto dev = static_cast<spi_device_handle_t>(spi_dev_);
    uint8_t tx[2] = {static_cast<uint8_t>(addr & 0x7F), 0x00};  // 读：bit7=0 +  dummy
    uint8_t rx[2] = {0, 0};
    spi_transaction_t t = {};
    t.length = 16;
    t.tx_buffer = tx;
    t.rx_buffer = rx;
    if (spi_device_transmit(dev, &t) != ESP_OK) {
        return 0;
    }
    return rx[1];
}

bool AiDogVision::ChipWriteReg(uint8_t addr, uint8_t value) {
    if (spi_dev_ == nullptr) return false;
    auto dev = static_cast<spi_device_handle_t>(spi_dev_);
    uint8_t tx[2] = {static_cast<uint8_t>(addr | 0x80), value};  // 写：bit7=1
    spi_transaction_t t = {};
    t.length = 16;
    t.tx_buffer = tx;
    return spi_device_transmit(dev, &t) == ESP_OK;
}

bool AiDogVision::SensorWriteReg(uint8_t addr, uint8_t value) {
    if (i2c_dev_ == nullptr) return false;
    uint8_t buf[2] = {addr, value};
    return i2c_master_transmit(static_cast<i2c_master_dev_handle_t>(i2c_dev_), buf, 2, 50) == ESP_OK;
}

uint8_t AiDogVision::SensorReadReg(uint8_t addr) {
    if (i2c_dev_ == nullptr) return 0;
    uint8_t value = 0;
    if (i2c_master_transmit_receive(static_cast<i2c_master_dev_handle_t>(i2c_dev_), &addr, 1,
                                    &value, 1, 50) != ESP_OK) {
        return 0;
    }
    return value;
}

bool AiDogVision::WriteSensorTable(const uint8_t* table, size_t bytes) {
    const auto* regs = reinterpret_cast<const Ov2640Reg*>(table);
    const size_t count = bytes / sizeof(Ov2640Reg);
    for (size_t i = 0; i < count; ++i) {
        if (regs[i].addr == 0xFF && regs[i].value == 0xFF) {
            break;  // 结束标记
        }
        if (!SensorWriteReg(regs[i].addr, regs[i].value)) {
            ESP_LOGE(TAG, "OV2640 寄存器 0x%02X 写失败", regs[i].addr);
            return false;
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

esp_err_t AiDogVision::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }
    jpeg_buf_.resize(kJpegMaxLen);

    // ---- 1) SPI 总线（与 OLED 共 SPI2；OLED 先起也没关系，重复 initialize 返回
    //         ESP_ERR_INVALID_STATE，此时总线已由 OLED 建好但可能没开 MISO，
    //         因此摄像头路径必须第一个建总线：board 在 summon 开启时先初始化摄像头）----
    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num = CAMERA_SPI_MOSI_PIN;
    buscfg.miso_io_num = CAMERA_SPI_MISO_PIN;
    buscfg.sclk_io_num = CAMERA_SPI_SCLK_PIN;
    buscfg.quadwp_io_num = GPIO_NUM_NC;
    buscfg.quadhd_io_num = GPIO_NUM_NC;
    buscfg.max_transfer_sz = 32 * 1024;
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "SPI2 总线初始化失败: %d", err);
        return err;
    }

    spi_device_interface_config_t devcfg = {};
    devcfg.mode = 0;                       // ArduChip：SPI mode 0
    devcfg.clock_speed_hz = CAMERA_SPI_CLOCK_HZ;
    devcfg.spics_io_num = CAMERA_SPI_CS_PIN;
    devcfg.queue_size = 1;
    spi_device_handle_t spi_dev = nullptr;
    err = spi_bus_add_device(SPI2_HOST, &devcfg, &spi_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "摄像头 SPI 设备添加失败: %d", err);
        return err;
    }
    spi_dev_ = spi_dev;

    // ---- 2) I²C 传感器（共享传感器总线 0x30）----
    err = AiDogI2c::GetInstance().Initialize();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I²C 总线初始化失败: %d", err);
        spi_bus_remove_device(spi_dev);
        spi_dev_ = nullptr;
        return err;
    }
    i2c_device_config_t i2c_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = kOv2640I2cAddr,
        .scl_speed_hz = 100 * 1000,   // SCCB 100kHz（OV2640 稳妥值）
        .scl_wait_us = 0,
        .flags = { .disable_ack_check = 0 },
    };
    i2c_master_dev_handle_t i2c_dev = nullptr;
    err = i2c_master_bus_add_device(AiDogI2c::GetInstance().GetBus(), &i2c_cfg, &i2c_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OV2640 I²C 设备添加失败: %d（模块 SDA/SCL 接到 GPIO41/42 了吗）", err);
        spi_bus_remove_device(spi_dev);
        spi_dev_ = nullptr;
        return err;
    }
    i2c_dev_ = i2c_dev;

    // ---- 3) ArduChip 自检 + MCU 模式 ----
    uint8_t rev = ChipReadReg(kArduchipTest1);
    ESP_LOGI(TAG, "ArduChip TEST1 = 0x%02X（不同批次值不同，能读到即可）", rev);
    ChipWriteReg(kArduchipMode, 0x00);  // MCU 模式

    // ---- 4) OV2640：JPEG 160x120 ----
    uint8_t vid = SensorReadReg(kOv2640ChipidHigh);
    uint8_t pid = SensorReadReg(kOv2640ChipidLow);
    ESP_LOGI(TAG, "OV2640 芯片 ID：0x%02X 0x%02X（期望 0x26 0x42）", vid, pid);
    if (vid != 0x26 || pid != 0x42) {
        ESP_LOGE(TAG, "OV2640 不在位或型号不符（R-F5：检查供电/接线，5V 不得直接接信号脚）");
        i2c_master_bus_rm_device(i2c_dev);
        i2c_dev_ = nullptr;
        spi_bus_remove_device(spi_dev);
        spi_dev_ = nullptr;
        return ESP_ERR_NOT_FOUND;
    }
    SensorWriteReg(0xFF, 0x01);   // 选传感器 bank 1（ArduCAM 库同款前置）
    SensorWriteReg(0x12, 0x80);   // 复位所有寄存器
    vTaskDelay(pdMS_TO_TICKS(100));

    bool ok = WriteSensorTable(reinterpret_cast<const uint8_t*>(OV2640_JPEG_INIT), sizeof(OV2640_JPEG_INIT)) &&
              WriteSensorTable(reinterpret_cast<const uint8_t*>(OV2640_YUV422), sizeof(OV2640_YUV422)) &&
              WriteSensorTable(reinterpret_cast<const uint8_t*>(OV2640_JPEG), sizeof(OV2640_JPEG)) &&
              WriteSensorTable(reinterpret_cast<const uint8_t*>(OV2640_160x120_JPEG), sizeof(OV2640_160x120_JPEG));
    if (!ok) {
        ESP_LOGE(TAG, "OV2640 JPEG 初始化序列写入失败");
        i2c_master_bus_rm_device(i2c_dev);
        i2c_dev_ = nullptr;
        spi_bus_remove_device(spi_dev);
        spi_dev_ = nullptr;
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "OV2640 就绪：JPEG 160x120（检测模型输入尺寸，免缩放）");

    // ---- 5) 人形检测模型（pedestrian_detect，rodata 内嵌）----
    auto* detector = new PedestrianDetect();
    detector->set_score_thr(CONFIG_AI_DOG_VISION_DETECT_THRESHOLD / 100.0f);
    detector_ = detector;

    enabled_ = true;
    ESP_LOGI(TAG, "视觉链路就绪：ArduCAM + pedestrian_detect（score 阈值 %d%%，框占比门限 %d%%）",
             CONFIG_AI_DOG_VISION_DETECT_THRESHOLD, CONFIG_AI_DOG_VISION_BOX_RATIO);
    return ESP_OK;
}

void AiDogVision::Shutdown() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_) {
        return;
    }    if (detector_ != nullptr) {
        delete static_cast<PedestrianDetect*>(detector_);
        detector_ = nullptr;
    }
    if (i2c_dev_ != nullptr) {
        i2c_master_bus_rm_device(static_cast<i2c_master_dev_handle_t>(i2c_dev_));
        i2c_dev_ = nullptr;
    }
    if (spi_dev_ != nullptr) {
        spi_bus_remove_device(static_cast<spi_device_handle_t>(spi_dev_));
        spi_dev_ = nullptr;
    }
    jpeg_buf_.clear();
    jpeg_buf_.shrink_to_fit();
    enabled_ = false;
    ESP_LOGI(TAG, "视觉链路已关闭");
}

void AiDogVision::SetRuntimeEnabled(bool on) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (runtime_enabled_ != on) {
        runtime_enabled_ = on;
        ESP_LOGI(TAG, "视觉确认运行时开关 -> %s", on ? "开" : "关（R-F6 纯声学到达）");
    }
}

bool AiDogVision::IsRuntimeEnabled() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return runtime_enabled_;
}

// ---------------------------------------------------------------------------
// 抓帧 + 检测
// ---------------------------------------------------------------------------

size_t AiDogVision::CaptureJpeg(uint8_t* out, size_t max_len, int timeout_ms) {
    if (!enabled_ || spi_dev_ == nullptr) {
        return 0;
    }
    auto dev = static_cast<spi_device_handle_t>(spi_dev_);

    // 1) 清 FIFO → 2) 启动抓帧 → 3) 轮询完成
    ChipWriteReg(kArduchipFifo, kFifoClearMask);
    vTaskDelay(pdMS_TO_TICKS(5));
    ChipWriteReg(kArduchipFifo, kFifoStartMask);

    const int poll_ms = 10;
    bool done = false;
    for (int elapsed = 0; elapsed < timeout_ms; elapsed += poll_ms) {
        if (ChipReadReg(kArduchipTrig) & kCapDoneMask) {
            done = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(poll_ms));
    }
    if (!done) {
        ESP_LOGW(TAG, "抓帧超时（%dms）", timeout_ms);
        capture_fail_++;
        return 0;
    }

    // 4) FIFO 长度（3 字节，最大 2^19-1）
    const uint32_t len1 = ChipReadReg(kFifoSize1);
    const uint32_t len2 = ChipReadReg(kFifoSize2);
    const uint32_t len3 = ChipReadReg(kFifoSize3) & 0x7F;
    uint32_t length = (len3 << 16) | (len2 << 8) | len1;
    if (length == 0 || length > max_len) {
        ESP_LOGW(TAG, "FIFO 长度异常：%u（上限 %u）", static_cast<unsigned>(length),
                 static_cast<unsigned>(max_len));
        capture_fail_++;
        return 0;
    }

    // 5) 突发读 FIFO：先发 0x3C 命令，随后 length 个 0x00 全双工读
    //    （老批次模块首字节是 dummy，用 JPEG 头尾标记自动剥离/截断）
    uint8_t cmd = kBurstFifoRead;
    spi_transaction_t t = {};
    t.length = 8;
    t.tx_buffer = &cmd;
    if (spi_device_transmit(dev, &t) != ESP_OK) {
        capture_fail_++;
        return 0;
    }
    // 分块读（DMA 单次上限 32KB，JPEG 一般一次读完）
    size_t got = 0;
    while (got < length) {
        size_t chunk = length - got > 32768 ? 32768 : length - got;
        spi_transaction_t rt = {};
        rt.length = chunk * 8;
        rt.rxlength = chunk * 8;
        rt.tx_buffer = nullptr;  // MOSI 发 0x00（不关心）
        rt.rx_buffer = out + got;
        if (spi_device_transmit(dev, &rt) != ESP_OK) {
            ESP_LOGW(TAG, "FIFO 读失败 @%u", static_cast<unsigned>(got));
            capture_fail_++;
            return 0;
        }
        got += chunk;
    }

    // 6) 找 JPEG 头（FFD8），以 FFD9 结尾；剥离 dummy/噪声字节
    size_t start = got;
    for (size_t i = 0; i + 1 < got; ++i) {
        if (out[i] == 0xFF && out[i + 1] == 0xD8) {
            start = i;
            break;
        }
    }
    size_t end = got;
    for (size_t i = got - 1; i > start + 1; --i) {
        if (out[i - 1] == 0xFF && out[i] == 0xD9) {
            end = i + 1;
            break;
        }
    }
    if (start >= end) {
        ESP_LOGW(TAG, "FIFO 数据里找不到 JPEG 头尾（len=%u）", static_cast<unsigned>(got));
        capture_fail_++;
        return 0;
    }
    const size_t jpeg_len = end - start;
    if (start > 0) {
        memmove(out, out + start, jpeg_len);
    }
    capture_ok_++;
    ESP_LOGI(TAG, "抓帧成功：%u 字节（原始 %u，剥掉 %u 前导字节）",
             static_cast<unsigned>(jpeg_len), static_cast<unsigned>(got),
             static_cast<unsigned>(start));
    return jpeg_len;
}

int AiDogVision::CaptureAndDetect(int frame_budget_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_ || detector_ == nullptr) {
        return -1;
    }
    const int64_t t0 = esp_timer_get_time();

    size_t jpeg_len = CaptureJpeg(jpeg_buf_.data(), jpeg_buf_.size(), frame_budget_ms / 2);
    if (jpeg_len == 0) {
        return -1;
    }

    // 解码 → 160x120 RGB888
    dl::image::jpeg_img_t jpeg = {
        .data = jpeg_buf_.data(),
        .data_len = jpeg_len,
    };
    dl::image::img_t img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    if (img.data == nullptr) {
        ESP_LOGW(TAG, "JPEG 解码失败");
        return -1;
    }
    ESP_LOGI(TAG, "解码：%dx%d RGB888", img.width, img.height);

    // 人形检测
    auto& results = static_cast<PedestrianDetect*>(detector_)->run(img);
    int verdict = 0;
    const float box_ratio_min = CONFIG_AI_DOG_VISION_BOX_RATIO / 100.0f;
    for (const auto& r : results) {
        if (r.box.size() < 4) {
            continue;  // 异常结果保护
        }
        const float box_area = static_cast<float>((r.box[2] - r.box[0]) * (r.box[3] - r.box[1]));
        const float ratio = box_area / static_cast<float>(img.width * img.height);
        ESP_LOGI(TAG, "检出：score=%.2f box=[%d,%d,%d,%d] 占比=%.1f%%（门限 %.0f%%）",
                 r.score, r.box[0], r.box[1], r.box[2], r.box[3],
                 ratio * 100.0f, box_ratio_min * 100.0f);
        if (ratio >= box_ratio_min) {
            verdict = 1;
        }
    }
    heap_caps_free(img.data);
    ESP_LOGI(TAG, "视觉判定：%s（耗时 %dms / 预算 %dms）",
             verdict == 1 ? "有人形" : "无人形",
             static_cast<int>((esp_timer_get_time() - t0) / 1000), frame_budget_ms);
    return verdict;
}

int AiDogVision::ConfirmHuman(int max_tries, int frame_budget_ms) {
    // SW-303：1-2 帧采样 + 至多 max_tries 次重试；任一帧过阈值即确认。
    // 隐私：全程不存图、不上传（Kconfig AI_DOG_VISION_PRIVACY_NO_SAVE 强制 ON）。
    int verdict = 0;
    for (int i = 0; i < max_tries; ++i) {
        verdict = CaptureAndDetect(frame_budget_ms);
        if (verdict == 1) {
            break;
        }
        if (verdict < 0 && i == max_tries - 1) {
            break;  // 硬件失败：调用方按 R-F6 处理
        }
        // 无人形：稍等再抓（避免连续抓同一过渡帧）
        vTaskDelay(pdMS_TO_TICKS(150));
    }
    // 摄像头用完：OLED 软件兜底（未飞线时重初始化屏幕）
    PostUseCallback cb;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        cb = post_use_callback_;
    }
    if (cb) {
        cb();
    }
    return verdict;
}

uint32_t AiDogVision::GetCaptureOkCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return capture_ok_;
}

uint32_t AiDogVision::GetCaptureFailCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return capture_fail_;
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
