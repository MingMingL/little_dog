#ifndef AI_DOG_VISION_H
#define AI_DOG_VISION_H

#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <vector>

#include <esp_err.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * ArduCAM 视觉确认链路（SW-301/302/303，路线 B）。
 *
 * 硬件：ArduCAM Mini 2MP Plus（OV2640）。
 *   - SPI（与 OLED 共 SPI2 总线）：SCK=GPIO1 / MOSI=GPIO2 / MISO=GPIO17 / CS=GPIO18，
 *     ArduChip 模式 0，8MHz；抓 JPEG 快照。
 *   - I²C（与 VL53L0X/PCF8574 共 GPIO41/42 总线，0x30）：OV2640 传感器寄存器，
 *     初始化成 JPEG 160x120（正好是人形检测模型输入尺寸，免缩放）。
 *
 * 链路：抓 1 帧 JPEG → sw_decode_jpeg → 160x120 RGB888 → pedestrian_detect
 * （esp-dl PICO 模型，score 阈值 Kconfig）→ 检出框占比过滤。
 *
 * 隐私（SW-303 / AT-S09）：JPEG 只在内存中处理，不写 flash、不发网络；
 * 日志只打尺寸/分数/框占比，不打图像内容。
 *
 * SPI 共存（SW-304 / T2）：OLED CS 未飞线（接 GND）时摄像头帧会串到 OLED，
 * 用完后由 board 调 ReinitOledAfterCamera() 重初始化屏幕（软件兜底）；
 * 飞线到 GPIO45 后（Kconfig AI_DOG_OLED_CS_GPIO45）无此问题。
 */
class AiDogVision {
public:
    static AiDogVision& GetInstance();

    /**
     * 初始化 SPI 设备 + OV2640（JPEG 160x120）+ 人形检测模型。
     * 摄像头未接/型号不符时返回错误，调用方按 R-F6 退回纯声学到达判定。
     */
    esp_err_t Initialize();

    void Shutdown();
    bool IsEnabled() const { return enabled_; }

    /**
     * 运行时开关（App 摄像头开关，SW-401）：编译期启用且摄像头在位时，
     * 可动态关闭视觉确认（按 R-F6 退回纯声学到达判定）。关闭不影响已抓的帧。
     */
    void SetRuntimeEnabled(bool on);
    bool IsRuntimeEnabled() const;

    /**
     * 抓 1 帧并检测。返回：1=有人形 / 0=无人形 / -1=失败（超时/解码/检测错误）。
     * frame_budget_ms 为单帧总预算（抓帧+解码+检测），超时按 -1 记并打日志。
     */
    int CaptureAndDetect(int frame_budget_ms);

    /**
     * S3 入口（SW-303）：触发条件由状态机给；这里做 1-2 帧采样 + 重试。
     * max_tries 次内任一帧检出即返回 1；全部无人形返回 0；硬件失败返回 -1。
     */
    int ConfirmHuman(int max_tries, int frame_budget_ms);

    /** 调试统计：抓帧成功/失败次数 */
    uint32_t GetCaptureOkCount() const;
    uint32_t GetCaptureFailCount() const;

    /**
     * 注册"摄像头用完"回调（SW-304 软件兜底）：OLED CS 未飞线时，
     * 摄像头抓帧会污染 OLED 显示，board 在这里重初始化屏幕。
     * 飞线方案下回调仍会被调用（幂等，内部直接返回）。
     */
    using PostUseCallback = std::function<void()>;
    void SetPostUseCallback(PostUseCallback cb) {
        std::lock_guard<std::mutex> lock(mutex_);
        post_use_callback_ = std::move(cb);
    }

private:
    AiDogVision() = default;
    AiDogVision(const AiDogVision&) = delete;
    AiDogVision& operator=(const AiDogVision&) = delete;

    // ArduChip SPI 寄存器（命令字节 bit7=写，bit[6:0]=地址）
    uint8_t ChipReadReg(uint8_t addr);
    bool ChipWriteReg(uint8_t addr, uint8_t value);
    // OV2640 传感器寄存器（I²C 0x30）
    bool SensorWriteReg(uint8_t addr, uint8_t value);
    uint8_t SensorReadReg(uint8_t addr);
    bool WriteSensorTable(const uint8_t* table, size_t bytes);

    /** 抓一帧 JPEG 到 out，返回长度（0=失败）。自动剥离前导非 JPEG 字节。 */
    size_t CaptureJpeg(uint8_t* out, size_t max_len, int timeout_ms);

    mutable std::mutex mutex_;
    bool enabled_ = false;
    bool runtime_enabled_ = true;   // App 摄像头开关（默认开）
    bool shutdown_ = false;
    void* spi_dev_ = nullptr;   // spi_device_handle_t
    void* i2c_dev_ = nullptr;   // i2c_master_dev_handle_t
    void* detector_ = nullptr;  // PedestrianDetect*
    uint32_t capture_ok_ = 0;
    uint32_t capture_fail_ = 0;
    std::vector<uint8_t> jpeg_buf_;
    PostUseCallback post_use_callback_;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_VISION_H
