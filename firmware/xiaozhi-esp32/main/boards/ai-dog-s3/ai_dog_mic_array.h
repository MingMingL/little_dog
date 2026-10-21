#ifndef AI_DOG_MIC_ARRAY_H
#define AI_DOG_MIC_ARRAY_H

#include <cstddef>
#include <cstdint>
#include <condition_variable>
#include <mutex>

#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <driver/gpio.h>
#include <driver/i2s_std.h>
#include <driver/i2s_tdm.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * 4 麦方阵采集（SW-102）。
 *
 * 输出统一的 4 通道交错 16-bit PCM（mic0, mic1, mic2, mic3, ...），16kHz。
 * 通道顺序必须与 ai_dog_doa 的 mic_pos[] 一致（esp_apa_doa 约定：
 * ESP_APA_DOA_MIC_POS_SQUARE 的 mic0=左上 / mic1=右上 / mic2=右下 / mic3=左下）。
 * 硬件接线按此顺序焊：后端 A/C 的 TDM slot0..3、后端 B 的 L/R 槽。
 *
 * 采集后端（Kconfig AI_DOG_MIC_BACKEND，方案 §3.3 / Q-04 / R-F2）：
 *   A 单数据线 4 槽 TDM：I2S1 拥有麦克风口（DIN=GPIO6），阵列板做 TDM 复用；
 *   B 双立体声数据线：I2S1 立体声（MIC1/MIC2 走 GPIO6 L/R 分槽）
 *     + I2S0-RX 立体声从机（MIC3/MIC4 走 GPIO38，BCLK/WS 取自阵列时钟 GPIO5/4）；
 *   C 成品 TDM 阵列板：与 A 同构，仅日志区分。
 *
 * ===== 架构决策（Q-03 决策 A 的固件落地，M1 分析结论）=====
 * ESP32-S3 只有两个 I2S 控制器：扬声器（TX，24kHz）占 I2S0、
 * AFE 麦克风（RX，16kHz）占 I2S1。4 麦阵列无法新增第三个口，
 * 因此由本模块"拥有"麦克风 I2S 口：
 *   - 后端 A/C：I2S1 配成 TDM 4 槽；
 *   - 后端 B：I2S1 配成立体声，另用 I2S0 的 RX 通道（从机，与扬声器 TX 共存
 *     同一控制器、时钟取自阵列时钟线）收 MIC3/MIC4。
 * AFE 的单麦链路改为从阵列通道 0 取数（MicArrayAudioCodec，见 .cc）——
 * AFE 看到的仍是单麦 16kHz 音频，"现有 MIC1 链路不动"（物理接线不变，
 * 数据入口从 codec 直读 I2S 改为阵列广播）。DOA / 口令（MultiNet）
 * 消费同一条 4 通道广播流的旁路副本，互不抢数据。
 *
 * 线程模型：采集任务（CaptureTask）独占 I2S 读，把帧写进广播环形缓冲；
 * 各消费方持自己的读游标（uint64_t cursor）调 Read()/WaitFrames() 独立取数，
 * 广播语义：游标只前移，互不抢数据。游标初值取 GetWrittenFrames() 表示从当前起。
 */
class MicArray {
public:
    static constexpr int kChannels = 4;
    static constexpr int kSampleRate = 16000;

    static MicArray& GetInstance();

    /**
     * 初始化 I2S + 启动采集任务。重复调用安全。
     * 失败（如后端 B 的 I2S0-RX 申请不到）时返回错误，调用方应降级：
     * R-F2 → 切后端 A/C，或禁用召唤音频链。
     */
    esp_err_t Initialize();

    /** 停止采集任务并释放 I2S（召唤关闭/进配网模式时用） */
    void Shutdown();

    bool IsEnabled() const { return enabled_; }

    /** 后端标识（日志用）："A:tdm4" / "B:dual-stereo" / "C:tdm-board" */
    const char* GetBackendName() const;

    /** 当前总写入帧数（单调递增，供消费方判断是否有新数据） */
    uint64_t GetWrittenFrames() const;

    /**
     * 读一段 4 通道交错 PCM。*cursor 为调用方自己的读游标
     * （初值传当前 GetWrittenFrames() 即可从头开始）。
     * 返回实际读到的帧数（不够则少给，不阻塞；返回 0 表示暂时无新数据）。
     * 线程安全，可在任意任务上下文调用。
     */
    size_t Read(uint64_t& cursor, int16_t* out, size_t frames) const;

    /**
     * 等待直到至少 min_frames 帧可读或超时。返回可读帧数（可能更多）。
     * timeout_ms = 0 表示不等待直接返回当前可读量。
     */
    size_t WaitFrames(uint64_t& cursor, size_t min_frames, int timeout_ms) const;

    /** 每通道最近一帧能量（RMS，dBFS 近似；T3 幅度一致性检查用） */
    float GetChannelLevelDb(int channel) const;

    /** 广播缓冲容量（帧） */
    size_t GetRingCapacity() const { return ring_frames_; }

    /**
     * 工厂：创建"AFE 从阵列通道 0 取数"的 AudioCodec（Q-03 决策 A）。
     * 返回 void* 是为了不在本头文件泄漏 audio_codec.h 依赖，
     * 调用方（board）转回 AudioCodec* 用。仅 MicArray::IsEnabled() 为 true 时调用。
     */
    void* CreateAudioCodec(int input_sample_rate, int output_sample_rate,
                           gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout);

private:
    MicArray() = default;
    MicArray(const MicArray&) = delete;
    MicArray& operator=(const MicArray&) = delete;

    esp_err_t InitBackendAOrC(bool board_variant);
    esp_err_t InitBackendB();
    void DestroyChannels();

    static void CaptureTask(void* arg);
    void CaptureLoop();

    // 把一段原始 I2S 数据整理成 4 通道交错帧，写入环形缓冲
    void PublishFrames(const int16_t* src_interleaved4, size_t frames);

    mutable std::mutex mutex_;
    mutable std::condition_variable_any cv_;

    bool enabled_ = false;
    bool shutdown_ = false;
    int backend_ = 0;  // 0=A 1=B 2=C

    // I2S 通道（后端 A/C：tdm_rx_；后端 B：stereo_rx1_ + stereo_rx0_）
    i2s_chan_handle_t tdm_rx_ = nullptr;
    i2s_chan_handle_t stereo_rx1_ = nullptr;  // I2S1：MIC1(L)/MIC2(R)
    i2s_chan_handle_t stereo_rx0_ = nullptr;  // I2S0-RX：MIC3(L)/MIC4(R)

    // 广播环形缓冲：容量 ring_frames_ 帧 × 4 通道交错
    int16_t* ring_ = nullptr;
    size_t ring_frames_ = 0;
    uint64_t written_ = 0;      // 已写入总帧数（单调递增）
    float channel_db_[kChannels] = {-100.0f, -100.0f, -100.0f, -100.0f};

    TaskHandle_t task_ = nullptr;
    StaticTask_t* task_buffer_ = nullptr;
    StackType_t* task_stack_ = nullptr;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_MIC_ARRAY_H
