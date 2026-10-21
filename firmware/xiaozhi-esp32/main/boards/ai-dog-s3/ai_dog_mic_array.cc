#include "ai_dog_mic_array.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <driver/i2s_std.h>
#include <driver/i2s_tdm.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <vector>

// AFE 单麦旁出：把阵列通道 0 包装成 AudioCodec，AudioService 无感
#include "audio_codec.h"

#define TAG "mic_array"

namespace aidog {

// 采集任务：优先级高于 AFE 处理任务(3)，保证 I2S DMA 被及时抽干；
// 栈放 PSRAM（与 AFE 任务同做法），静态分配避免碎片。
static constexpr int kCaptureTaskStackSize = 4096;
static constexpr int kCaptureTaskPriority = 4;

// 单次从 I2S 读的原始 32-bit slot 数（每帧 4 麦 = 4 个 slot）。
// 32 帧一批 ≈ 2ms @16kHz，批太大增加延迟，太小增加调用开销。
static constexpr size_t kFramesPerRead = 32;

MicArray& MicArray::GetInstance() {
    static MicArray instance;
    return instance;
}

// ---------------------------------------------------------------------------
// 环形广播缓冲
// ---------------------------------------------------------------------------

size_t MicArray::Read(uint64_t& cursor, int16_t* out, size_t frames) const {
    if (out == nullptr || frames == 0) {
        return 0;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t available = written_ > cursor ? written_ - cursor : 0;
    size_t to_read = std::min(frames, static_cast<size_t>(available));
    if (to_read == 0) {
        return 0;
    }
    // 环形读：起点 = cursor % ring_frames_
    size_t start = static_cast<size_t>(cursor % ring_frames_);
    size_t first = std::min(to_read, ring_frames_ - start);
    memcpy(out, ring_ + start * kChannels, first * kChannels * sizeof(int16_t));
    if (first < to_read) {
        memcpy(out + first * kChannels, ring_, (to_read - first) * kChannels * sizeof(int16_t));
    }
    cursor += to_read;
    return to_read;
}

size_t MicArray::WaitFrames(uint64_t& cursor, size_t min_frames, int timeout_ms) const {
    std::unique_lock<std::mutex> lock(mutex_);
    auto enough = [&]() { return written_ >= cursor + min_frames; };
    if (timeout_ms > 0) {
        cv_.wait_for(lock, std::chrono::milliseconds(timeout_ms), enough);
    }
    uint64_t available = written_ > cursor ? written_ - cursor : 0;
    return static_cast<size_t>(std::min<uint64_t>(available, SIZE_MAX));
}

float MicArray::GetChannelLevelDb(int channel) const {
    if (channel < 0 || channel >= kChannels) {
        return -100.0f;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    return channel_db_[channel];
}

uint64_t MicArray::GetWrittenFrames() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return written_;
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

esp_err_t MicArray::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }

    ring_frames_ = CONFIG_AI_DOG_MIC_CAPTURE_RING_FRAMES;
    ring_ = static_cast<int16_t*>(
        heap_caps_malloc(ring_frames_ * kChannels * sizeof(int16_t), MALLOC_CAP_INTERNAL));
    if (ring_ == nullptr) {
        ESP_LOGE(TAG, "环形缓冲分配失败（%u 帧）", static_cast<unsigned>(ring_frames_));
        return ESP_ERR_NO_MEM;
    }
    memset(ring_, 0, ring_frames_ * kChannels * sizeof(int16_t));

    esp_err_t err = ESP_OK;
#if defined(CONFIG_AI_DOG_MIC_BACKEND_B_DUAL_STEREO)
    backend_ = 1;
    err = InitBackendB();
#elif defined(CONFIG_AI_DOG_MIC_BACKEND_C_TDM_BOARD)
    backend_ = 2;
    err = InitBackendAOrC(true);
#else
    backend_ = 0;
    err = InitBackendAOrC(false);
#endif
    if (err != ESP_OK) {
        heap_caps_free(ring_);
        ring_ = nullptr;
        return err;
    }

    // 采集任务（静态分配，PSRAM 栈）
    task_stack_ = static_cast<StackType_t*>(heap_caps_malloc(kCaptureTaskStackSize, MALLOC_CAP_SPIRAM));
    task_buffer_ = static_cast<StaticTask_t*>(heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL));
    if (task_stack_ == nullptr || task_buffer_ == nullptr) {
        ESP_LOGE(TAG, "采集任务内存分配失败");
        DestroyChannels();
        heap_caps_free(ring_);
        ring_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    shutdown_ = false;
    task_ = xTaskCreateStatic(CaptureTask, "mic_array", kCaptureTaskStackSize, this,
                              kCaptureTaskPriority, task_stack_, task_buffer_);
    if (task_ == nullptr) {
        ESP_LOGE(TAG, "采集任务创建失败");
        DestroyChannels();
        heap_caps_free(task_stack_);
        heap_caps_free(task_buffer_);
        task_stack_ = nullptr;
        task_buffer_ = nullptr;
        heap_caps_free(ring_);
        ring_ = nullptr;
        return ESP_FAIL;
    }

    enabled_ = true;
    ESP_LOGI(TAG, "4 麦阵列就绪：后端 %s，%dHz，方阵边长 %dmm，环形缓冲 %u 帧",
             GetBackendName(), kSampleRate, CONFIG_AI_DOG_MIC_ARRAY_SIZE_MM,
             static_cast<unsigned>(ring_frames_));
    return ESP_OK;
}

void MicArray::Shutdown() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!enabled_) {
            return;
        }
        shutdown_ = true;
    }
    cv_.notify_all();
    // 等采集任务自行退出（它在锁外检查 shutdown_）
    for (int i = 0; i < 100 && task_ != nullptr; ++i) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        DestroyChannels();
        if (task_ != nullptr) {
            vTaskDelete(task_);
            task_ = nullptr;
        }
        if (task_stack_ != nullptr) {
            heap_caps_free(task_stack_);
            task_stack_ = nullptr;
        }
        if (task_buffer_ != nullptr) {
            heap_caps_free(task_buffer_);
            task_buffer_ = nullptr;
        }
        if (ring_ != nullptr) {
            heap_caps_free(ring_);
            ring_ = nullptr;
        }
        enabled_ = false;
    }
    ESP_LOGI(TAG, "4 麦阵列已关闭");
}

void MicArray::DestroyChannels() {
    if (tdm_rx_ != nullptr) {
        i2s_channel_disable(tdm_rx_);
        i2s_del_channel(tdm_rx_);
        tdm_rx_ = nullptr;
    }
    if (stereo_rx1_ != nullptr) {
        i2s_channel_disable(stereo_rx1_);
        i2s_del_channel(stereo_rx1_);
        stereo_rx1_ = nullptr;
    }
    if (stereo_rx0_ != nullptr) {
        i2s_channel_disable(stereo_rx0_);
        i2s_del_channel(stereo_rx0_);
        stereo_rx0_ = nullptr;
    }
}

const char* MicArray::GetBackendName() const {
    switch (backend_) {
        case 1:  return "B:dual-stereo";
        case 2:  return "C:tdm-board";
        default: return "A:tdm4";
    }
}

/**
 * 后端 A / C：I2S1（麦克风口）TDM 4 槽。
 *
 * INMP441 输出 24-bit 数据装在 32-bit slot 里（MSB 对齐，WS 沿后延迟 1 BCLK）。
 * IDF TDM 配置：data 16bit / slot 32bit / bit_shift（Philips），
 * 读出的是 4 个 int32 slot 一组，>>12 得 16-bit PCM（与 V1 单麦链路同一换算）。
 *
 * ws_width = 32（一个 slot 宽）：TDM Philips 惯例，WS 每帧拉高一个 slot 标记帧起点，
 * 之后 4 个 slot 依次为 mic0..mic3。阵列板/复用板按此时序输出。
 */
esp_err_t MicArray::InitBackendAOrC(bool board_variant) {
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(XIAOZHI_I2S_PORT(1), I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 240;
    chan_cfg.auto_clear_after_cb = true;
    esp_err_t err = i2s_new_channel(&chan_cfg, nullptr, &tdm_rx_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S1 TDM 通道创建失败: %d", err);
        return err;
    }

    // ws_width = 32（一个 slot 宽的帧同步脉冲）：经典 TDM 惯例，WS 每帧拉高
    // 一个 slot 标记帧起点，随后 4 个 slot 依次为 mic0..mic3。阵列板/复用板
    // 按此时序输出（M1 硬件联调时如时序不符，改这里即可）。
    // 数据格式与 V1 单麦链路一致：INMP441 24-bit 数据装 32-bit slot，
    // 读出 int32 后 >>12 得 16-bit PCM。
    i2s_tdm_slot_config_t slot_cfg = I2S_TDM_PHILIPS_SLOT_DEFAULT_CONFIG(
        I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO,
        static_cast<i2s_tdm_slot_mask_t>(I2S_TDM_SLOT0 | I2S_TDM_SLOT1 | I2S_TDM_SLOT2 | I2S_TDM_SLOT3));
    slot_cfg.slot_bit_width = I2S_SLOT_BIT_WIDTH_32BIT;
    slot_cfg.total_slot = 4;
    slot_cfg.ws_width = 32;

    i2s_tdm_config_t tdm_cfg = {
        .clk_cfg = I2S_TDM_CLK_DEFAULT_CONFIG(kSampleRate),
        .slot_cfg = slot_cfg,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = AUDIO_I2S_MIC_GPIO_SCK,   // GPIO5：阵列 4 麦共享
            .ws = AUDIO_I2S_MIC_GPIO_WS,      // GPIO4：阵列 4 麦共享
            .dout = I2S_GPIO_UNUSED,
            .din = AUDIO_I2S_MIC_GPIO_DIN,    // GPIO6：MIC1 数据线（TDM 复用）
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    err = i2s_channel_init_tdm_mode(tdm_rx_, &tdm_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S1 TDM 模式初始化失败: %d", err);
        i2s_del_channel(tdm_rx_);
        tdm_rx_ = nullptr;
        return err;
    }
    err = i2s_channel_enable(tdm_rx_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S1 TDM 使能失败: %d", err);
        i2s_del_channel(tdm_rx_);
        tdm_rx_ = nullptr;
        return err;
    }
    ESP_LOGI(TAG, "后端 %s：I2S1 TDM 4 槽（DIN=GPIO%d，SCK/WS=GPIO%d/%d）",
             board_variant ? "C" : "A", AUDIO_I2S_MIC_GPIO_DIN,
             AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS);
    return ESP_OK;
}

/**
 * 后端 B：双立体声数据线。
 *   I2S1 立体声（master）：MIC1/MIC2 经 GPIO6 L/R 分槽（L=mic0 左上，R=mic1 右上）；
 *   I2S0-RX 立体声（slave）：MIC3/MIC4 经 GPIO38（L=mic2 右下，R=mic3 左下），
 *     BCLK/WS 由 I2S1 的阵列时钟线（GPIO5/4）提供——同一控制器的 TX（扬声器）
 *     不受影响，RX 从机只吃外部时钟。
 * 若 I2S0-RX 申请失败（R-F2），返回错误由调用方切后端 A/C。
 */
esp_err_t MicArray::InitBackendB() {
    // 1) I2S1 立体声（MIC1/MIC2）
    i2s_chan_config_t chan1 = I2S_CHANNEL_DEFAULT_CONFIG(XIAOZHI_I2S_PORT(1), I2S_ROLE_MASTER);
    chan1.dma_desc_num = 6;
    chan1.dma_frame_num = 240;
    chan1.auto_clear_after_cb = true;
    esp_err_t err = i2s_new_channel(&chan1, nullptr, &stereo_rx1_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S1 立体声通道创建失败: %d", err);
        return err;
    }
    i2s_std_config_t std1 = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSampleRate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = AUDIO_I2S_MIC_GPIO_SCK,
            .ws = AUDIO_I2S_MIC_GPIO_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = AUDIO_I2S_MIC_GPIO_DIN,  // MIC1(L)/MIC2(R)
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    err = i2s_channel_init_std_mode(stereo_rx1_, &std1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S1 立体声初始化失败: %d", err);
        i2s_del_channel(stereo_rx1_);
        stereo_rx1_ = nullptr;
        return err;
    }

    // 2) I2S0-RX 立体声从机（MIC3/MIC4）——与扬声器 TX 共存同一控制器
    i2s_chan_config_t chan0 = I2S_CHANNEL_DEFAULT_CONFIG(XIAOZHI_I2S_PORT(0), I2S_ROLE_SLAVE);
    chan0.dma_desc_num = 6;
    chan0.dma_frame_num = 240;
    chan0.auto_clear_after_cb = true;
    err = i2s_new_channel(&chan0, nullptr, &stereo_rx0_);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S0-RX 创建失败（扬声器占用?）: %d —— 按 R-F2 切后端 A/C", err);
        i2s_channel_disable(stereo_rx1_);
        i2s_del_channel(stereo_rx1_);
        stereo_rx1_ = nullptr;
        return err;
    }
    i2s_std_config_t std0 = {
        .clk_cfg = {
            .sample_rate_hz = kSampleRate,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = AUDIO_I2S_MIC_GPIO_SCK,  // 从机：BCLK 取自阵列时钟线
            .ws = AUDIO_I2S_MIC_GPIO_WS,     // 从机：WS 取自阵列时钟线
            .dout = I2S_GPIO_UNUSED,
            .din = MIC_ARRAY_SD2_GPIO,      // GPIO38：MIC3(L)/MIC4(R)
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    err = i2s_channel_init_std_mode(stereo_rx0_, &std0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S0-RX 立体声从机初始化失败: %d", err);
        i2s_del_channel(stereo_rx0_);
        stereo_rx0_ = nullptr;
        i2s_channel_disable(stereo_rx1_);
        i2s_del_channel(stereo_rx1_);
        stereo_rx1_ = nullptr;
        return err;
    }

    err = i2s_channel_enable(stereo_rx1_);
    if (err == ESP_OK) {
        err = i2s_channel_enable(stereo_rx0_);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "后端 B 通道使能失败: %d", err);
        DestroyChannels();
        return err;
    }
    ESP_LOGI(TAG, "后端 B：I2S1 立体声（GPIO%d）+ I2S0-RX 立体声从机（GPIO%d）",
             AUDIO_I2S_MIC_GPIO_DIN, MIC_ARRAY_SD2_GPIO);
    return ESP_OK;
}

// ---------------------------------------------------------------------------
// 采集任务
// ---------------------------------------------------------------------------

void MicArray::CaptureTask(void* arg) {
    static_cast<MicArray*>(arg)->CaptureLoop();
}

void MicArray::CaptureLoop() {
    // 原始读缓冲：32-bit slot × 4 麦 × kFramesPerRead
    static int32_t raw[kFramesPerRead * kChannels];
    // 后端 B 的第二条数据线缓冲
    static int32_t raw2[kFramesPerRead * 2];

    while (true) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (shutdown_) {
                return;
            }
        }

        if (backend_ == 1) {
            // 后端 B：两路立体声合并成 4 通道交错
            size_t bytes1 = 0;
            size_t bytes0 = 0;
            esp_err_t e1 = i2s_channel_read(stereo_rx1_, raw, sizeof(raw), &bytes1, pdMS_TO_TICKS(100));
            esp_err_t e0 = i2s_channel_read(stereo_rx0_, raw2, sizeof(raw2), &bytes0, pdMS_TO_TICKS(100));
            if (e1 != ESP_OK || e0 != ESP_OK || bytes1 == 0 || bytes0 == 0) {
                continue;
            }
            size_t frames = std::min(bytes1, bytes0) / (2 * sizeof(int32_t));
            frames = std::min(frames, kFramesPerRead);
            static int16_t merged[kFramesPerRead * kChannels];
            for (size_t i = 0; i < frames; ++i) {
                // I2S1: L=mic0(左上) R=mic1(右上)；I2S0: L=mic2(右下) R=mic3(左下)
                merged[i * 4 + 0] = static_cast<int16_t>(raw[i * 2 + 0] >> 12);
                merged[i * 4 + 1] = static_cast<int16_t>(raw[i * 2 + 1] >> 12);
                merged[i * 4 + 2] = static_cast<int16_t>(raw2[i * 2 + 0] >> 12);
                merged[i * 4 + 3] = static_cast<int16_t>(raw2[i * 2 + 1] >> 12);
            }
            PublishFrames(merged, frames);
        } else {
            // 后端 A / C：TDM 4 槽，交错顺序即 slot0..3 = mic0..mic3
            size_t bytes = 0;
            esp_err_t err = i2s_channel_read(tdm_rx_, raw, sizeof(raw), &bytes, pdMS_TO_TICKS(100));
            if (err != ESP_OK || bytes == 0) {
                continue;
            }
            size_t frames = bytes / (kChannels * sizeof(int32_t));
            frames = std::min(frames, kFramesPerRead);
            static int16_t out[kFramesPerRead * kChannels];
            for (size_t i = 0; i < frames * kChannels; ++i) {
                out[i] = static_cast<int16_t>(raw[i] >> 12);
            }
            PublishFrames(out, frames);
        }
    }
}

void MicArray::PublishFrames(const int16_t* src_interleaved4, size_t frames) {
    if (frames == 0) {
        return;
    }
    double sum[kChannels] = {0.0, 0.0, 0.0, 0.0};
    {
        std::lock_guard<std::mutex> lock(mutex_);
        size_t start = static_cast<size_t>(written_ % ring_frames_);
        size_t first = std::min(frames, ring_frames_ - start);
        memcpy(ring_ + start * kChannels, src_interleaved4, first * kChannels * sizeof(int16_t));
        if (first < frames) {
            memcpy(ring_, src_interleaved4 + first * kChannels,
                   (frames - first) * kChannels * sizeof(int16_t));
        }
        written_ += frames;
        // 能量统计（本批），供 T3 幅度一致性与 DOA 近场能量用
        for (size_t i = 0; i < frames; ++i) {
            for (int c = 0; c < kChannels; ++c) {
                int32_t v = src_interleaved4[i * kChannels + c];
                sum[c] += static_cast<double>(v) * v;
            }
        }
        for (int c = 0; c < kChannels; ++c) {
            double mean = sum[c] / frames;
            channel_db_[c] = (mean > 0.0) ? 10.0 * log10(mean / 32768.0 / 32768.0) : -100.0f;
        }
    }
    cv_.notify_all();
}

}  // namespace aidog

// ---------------------------------------------------------------------------
// MicArrayAudioCodec（Q-03 决策 A 的固件落地）
//
// ESP32-S3 只有两个 I²S 口：扬声器 TX（I²S0）+ 4 麦阵列 RX（I²S1）。
// AFE 的单麦链路改为从阵列通道 0 取数：AFE 看到的仍是 16kHz 单麦 PCM，
// 与 V1 语义一致；DOA / 口令消费同一条流的旁路副本。
// 扬声器路径与 V1 NoAudioCodecSimplex 完全相同（I²S0 TX + 音量缩放）。
// ---------------------------------------------------------------------------

namespace aidog {

class MicArrayAudioCodec : public AudioCodec {
public:
    MicArrayAudioCodec(int input_sample_rate, int output_sample_rate,
                       gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout) {
        duplex_ = false;
        input_sample_rate_ = input_sample_rate;
        output_sample_rate_ = output_sample_rate;
        input_channels_ = 1;   // AFE 只看 MIC1（阵列通道 0）
        output_channels_ = 1;

        // 扬声器：I²S0 TX（与 V1 simplex 一致）
        i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(XIAOZHI_I2S_PORT(0), I2S_ROLE_MASTER);
        chan_cfg.dma_desc_num = AUDIO_CODEC_DMA_DESC_NUM;
        chan_cfg.dma_frame_num = AUDIO_CODEC_DMA_FRAME_NUM;
        chan_cfg.auto_clear_after_cb = true;
        chan_cfg.auto_clear_before_cb = false;
        chan_cfg.intr_priority = 0;
        ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &tx_handle_, nullptr));

        i2s_std_config_t std_cfg = {
            .clk_cfg = {
                .sample_rate_hz = static_cast<uint32_t>(output_sample_rate),
                .clk_src = I2S_CLK_SRC_DEFAULT,
                .ext_clk_freq_hz = 0,
                .mclk_multiple = I2S_MCLK_MULTIPLE_256,
            },
            .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
            .gpio_cfg = {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = spk_bclk,
                .ws = spk_ws,
                .dout = spk_dout,
                .din = I2S_GPIO_UNUSED,
                .invert_flags = {
                    .mclk_inv = false,
                    .bclk_inv = false,
                    .ws_inv = false,
                },
            },
        };
        ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_handle_, &std_cfg));
        ESP_ERROR_CHECK(i2s_channel_enable(tx_handle_));

        // 阵列消费者游标：从"现在"开始，不追历史
        cursor_ = MicArray::GetInstance().GetWrittenFrames();
        ESP_LOGI(TAG, "AFE 单麦旁出就绪：从 4 麦阵列通道 0 取数（Q-03 决策 A）");
    }

    ~MicArrayAudioCodec() override {
        if (tx_handle_ != nullptr) {
            i2s_channel_disable(tx_handle_);
            i2s_del_channel(tx_handle_);
        }
    }

    // 输入来自阵列（始终开启），启用/禁用只影响扬声器侧
    void EnableInput(bool enable) override {
        std::lock_guard<std::mutex> lock(data_if_mutex_);
        AudioCodec::EnableInput(enable);
    }
    void EnableOutput(bool enable) override {
        std::lock_guard<std::mutex> lock(data_if_mutex_);
        if (enable == output_enabled_) {
            return;
        }
        if (enable) {
            ESP_ERROR_CHECK(i2s_channel_enable(tx_handle_));
        } else {
            ESP_ERROR_CHECK(i2s_channel_disable(tx_handle_));
        }
        AudioCodec::EnableOutput(enable);
    }

protected:
    int Write(const int16_t* data, int samples) override {
        std::lock_guard<std::mutex> lock(data_if_mutex_);
        std::vector<int32_t> buffer(samples);
        int32_t volume_factor = pow(double(output_volume_) / 100.0, 2) * 65536;
        for (int i = 0; i < samples; i++) {
            int64_t temp = int64_t(data[i]) * volume_factor;
            if (temp > INT32_MAX) {
                buffer[i] = INT32_MAX;
            } else if (temp < INT32_MIN) {
                buffer[i] = INT32_MIN;
            } else {
                buffer[i] = static_cast<int32_t>(temp);
            }
        }
        size_t bytes_written;
        ESP_ERROR_CHECK(i2s_channel_write(tx_handle_, buffer.data(), samples * sizeof(int32_t),
                                          &bytes_written, portMAX_DELAY));
        return bytes_written / sizeof(int32_t);
    }

    int Read(int16_t* dest, int samples) override {
        // 从阵列环形缓冲取通道 0（广播语义，不抢 DOA/口令的数据）
        size_t got = 0;
        while (got < static_cast<size_t>(samples)) {
            size_t n = MicArray::GetInstance().Read(cursor_, pull_, kPullFrames);
            if (n == 0) {
                if (got > 0) {
                    break;  // 先交付已有的
                }
                vTaskDelay(pdMS_TO_TICKS(2));
                continue;
            }
            for (size_t i = 0; i < n && got < static_cast<size_t>(samples); ++i) {
                dest[got++] = pull_[i * MicArray::kChannels];  // 通道 0 = MIC1
            }
        }
        return static_cast<int>(got);
    }

private:
    static constexpr size_t kPullFrames = 256;
    std::mutex data_if_mutex_;
    uint64_t cursor_ = 0;
    int16_t pull_[kPullFrames * MicArray::kChannels] = {};
};

void* MicArray::CreateAudioCodec(int input_sample_rate, int output_sample_rate,
                                 gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout) {
    return new MicArrayAudioCodec(input_sample_rate, output_sample_rate,
                                  spk_bclk, spk_ws, spk_dout);
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
