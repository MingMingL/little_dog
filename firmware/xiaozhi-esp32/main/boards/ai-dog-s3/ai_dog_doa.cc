#include "ai_dog_doa.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_mic_array.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <algorithm>
#include <cmath>
#include <cstring>

#include "esp_apa_doa.h"

#define TAG "doa"

namespace aidog {

// doa_ 存为 void*（头文件不依赖 esp_apa_doa），这里集中回转为真实类型
static inline esp_apa_doa_handle_t AsHandle(void* p) {
    return reinterpret_cast<esp_apa_doa_handle_t>(p);
}

// 决策日志节流：同一状态最多每 500ms 打一条（SW-003：决策点输出依据，
// 但不刷屏）。调参期可把 Maximum log verbosity 调到 Debug 看每帧。
static constexpr int64_t kLogIntervalUs = 500 * 1000;

// 消费者任务：栈放 PSRAM，静态分配；优先级低于采集任务(4)。
static constexpr int kConsumerTaskStackSize = 4096;
static constexpr int kConsumerTaskPriority = 3;
// 每次从 MicArray 拉的帧数（512 帧 = 32ms @16kHz，与 DOA 分析窗匹配）
static constexpr size_t kPullFrames = 512;

DoaEngine& DoaEngine::GetInstance() {
    static DoaEngine instance;
    return instance;
}

esp_err_t DoaEngine::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }

    // ---- 方阵几何：边长 = AI_DOG_MIC_ARRAY_SIZE_MM，mic0..mic3 从左上顺时针 ----
    // 不用组件的 ESP_APA_DOA_CFG_DEFAULT()：其宏把 16000.0f 赋给 int 字段，
    // C++ 下是 -Wnarrowing 错误（组件小 bug，测试不会触发）。
    esp_apa_doa_cfg_t cfg = {};
    cfg.sample_rate_hz = 16000;
    cfg.mic_num = 4;
    const float side_m = CONFIG_AI_DOG_MIC_ARRAY_SIZE_MM / 1000.0f;
    const float mic_pos[4][2] = ESP_APA_DOA_MIC_POS_SQUARE(side_m);
    memcpy(cfg.mic_pos, mic_pos, sizeof(mic_pos));

    // ---- 频带：上限防空间混叠（c/2d @40mm ≈ 4250Hz，方案初值 4000）----
    cfg.fmax_hz = static_cast<float>(CONFIG_AI_DOG_DOA_FMAX_HZ);

    // ---- CPU/延迟预设 ----
#if defined(CONFIG_AI_DOG_DOA_PRESET_ECONOMY)
    ESP_APA_DOA_PRESET_ECONOMY(&cfg);
#elif defined(CONFIG_AI_DOG_DOA_PRESET_RESPONSIVE)
    ESP_APA_DOA_PRESET_RESPONSIVE(&cfg);
#else
    ESP_APA_DOA_PRESET_BALANCED(&cfg);
#endif
    cfg.fmax_hz = static_cast<float>(CONFIG_AI_DOG_DOA_FMAX_HZ);  // 预设在后再覆盖

    // ---- 高通预滤波系数（一阶 IIR，截止 = AI_DOG_DOA_FMIN_HZ）----
    // y[n] = alpha*(y[n-1] + x[n] - x[n-1])，alpha = RC/(RC+dt)
    const float dt = 1.0f / 16000.0f;
    const float rc = 1.0f / (2.0f * static_cast<float>(M_PI) * CONFIG_AI_DOG_DOA_FMIN_HZ);
    hp_alpha_ = rc / (rc + dt);

    esp_apa_doa_handle_t handle = nullptr;   // 真实类型（不能直接传 &doa_ 的 void**）
    esp_err_t err = esp_apa_doa_create(&cfg, &handle);
    doa_ = handle;
    if (err != ESP_OK || doa_ == nullptr) {
        ESP_LOGE(TAG, "esp_apa_doa_create 失败: %d（阵边长 %.0fmm, fmax=%dHz）",
                 err, side_m * 1000.0f, CONFIG_AI_DOG_DOA_FMAX_HZ);
        doa_ = nullptr;
        return err == ESP_OK ? ESP_FAIL : err;
    }

    uint16_t frame_samples = 0;
    esp_apa_doa_get_frame_samples(AsHandle(doa_), &frame_samples);
    scratch_.reserve(static_cast<size_t>(frame_samples) * 4 + 64);

    filter_window_.clear();
    fed_frames_ = 0;
    valid_frames_ = 0;
    estimate_ = Estimate{};
    active_ = false;
    shutdown_ = false;
    cursor_ = MicArray::GetInstance().GetWrittenFrames();

    // 消费者任务（静态分配，PSRAM 栈）：active_ 为 true 时从阵列拉流喂引擎
    task_stack_ = static_cast<StackType_t*>(
        heap_caps_malloc(kConsumerTaskStackSize, MALLOC_CAP_SPIRAM));
    task_buffer_ = static_cast<StaticTask_t*>(
        heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL));
    if (task_stack_ == nullptr || task_buffer_ == nullptr) {
        ESP_LOGE(TAG, "消费者任务内存分配失败");
        esp_apa_doa_destroy(AsHandle(doa_));
        doa_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    task_ = xTaskCreateStatic(ConsumerTask, "doa", kConsumerTaskStackSize, this,
                              kConsumerTaskPriority, task_stack_, task_buffer_);
    if (task_ == nullptr) {
        ESP_LOGE(TAG, "消费者任务创建失败");
        heap_caps_free(task_stack_);
        heap_caps_free(task_buffer_);
        task_stack_ = nullptr;
        task_buffer_ = nullptr;
        esp_apa_doa_destroy(AsHandle(doa_));
        doa_ = nullptr;
        return ESP_FAIL;
    }

    enabled_ = true;
    ESP_LOGI(TAG, "DOA 就绪：方阵边长 %.0fmm，频带 %d~%dHz，预设 stride=%d，"
                  "中值滤波 %d 帧，质量门限 %d，死区 ±%d°",
             side_m * 1000.0f, CONFIG_AI_DOG_DOA_FMIN_HZ, CONFIG_AI_DOG_DOA_FMAX_HZ,
             cfg.analysis_stride, CONFIG_AI_DOG_DOA_FILTER_FRAMES,
             CONFIG_AI_DOG_DOA_MIN_QUALITY, CONFIG_AI_DOG_DOA_DEADZONE_DEG);
    return ESP_OK;
}

void DoaEngine::Shutdown() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!enabled_) {
            return;
        }
        shutdown_ = true;
        active_ = false;
    }
    // 等消费者任务自行退出（锁外检查 shutdown_）
    for (int i = 0; i < 100 && task_ != nullptr; ++i) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    std::lock_guard<std::mutex> lock(mutex_);
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
    if (doa_ != nullptr) {
        esp_apa_doa_destroy(AsHandle(doa_));
        doa_ = nullptr;
    }
    scratch_.clear();
    scratch_.shrink_to_fit();
    filter_window_.clear();
    enabled_ = false;
    ESP_LOGI(TAG, "DOA 已关闭");
}

void DoaEngine::SetActive(bool active) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (active_ == active) {
        return;
    }
    active_ = active;
    ESP_LOGI(TAG, "DOA 消费 %s", active ? "启动（进入召唤音频链）" : "暂停（让出 CPU）");
}

bool DoaEngine::IsActive() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return active_;
}

void DoaEngine::ConsumerTask(void* arg) {
    static_cast<DoaEngine*>(arg)->ConsumerLoop();
}

void DoaEngine::ConsumerLoop() {
    static int16_t pull[kPullFrames * 4];
    while (true) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (shutdown_) {
                return;
            }
            if (!active_) {
                // 休眠轮询：20ms 粒度足够（SetActive 延迟对 S1 无感）
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
        }
        // active_ 期间从阵列拉流；cursor_ 只在本任务用，无需加锁读
        size_t got = MicArray::GetInstance().Read(cursor_, pull, kPullFrames);
        if (got == 0) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        Feed(pull, got);
    }
}

void DoaEngine::ApplyHighPass(int16_t* buf, size_t frames) {
    // 每通道独立一阶 IIR 高通；就地处理
    for (size_t i = 0; i < frames; ++i) {
        for (int c = 0; c < 4; ++c) {
            const size_t idx = i * 4 + c;
            const float x = static_cast<float>(buf[idx]);
            const float y = hp_alpha_ * (hp_out_prev_[c] + x - hp_in_prev_[c]);
            hp_in_prev_[c] = x;
            hp_out_prev_[c] = y;
            buf[idx] = static_cast<int16_t>(std::clamp(y, -32768.0f, 32767.0f));
        }
    }
}

void DoaEngine::Feed(const int16_t* interleaved4, size_t frames) {
    if (!enabled_ || doa_ == nullptr || interleaved4 == nullptr || frames == 0) {
        return;
    }

    // 1) 高通预滤波到 scratch_
    if (scratch_.size() < frames * 4) {
        scratch_.resize(frames * 4);
    }
    memcpy(scratch_.data(), interleaved4, frames * 4 * sizeof(int16_t));
    ApplyHighPass(scratch_.data(), frames);

    // 2) 能量统计（全带 + 带内），近场声学特征用
    double sum_all = 0.0;
    double sum_band = 0.0;
    for (size_t i = 0; i < frames * 4; ++i) {
        const double v = interleaved4[i];
        const double w = scratch_[i];
        sum_all += v * v;
        sum_band += w * w;
    }
    const float energy_db = (sum_all > 0.0)
        ? 10.0f * log10f(static_cast<float>(sum_all / (frames * 4)) / 32768.0f / 32768.0f)
        : -100.0f;
    const float band_energy_db = (sum_band > 0.0)
        ? 10.0f * log10f(static_cast<float>(sum_band / (frames * 4)) / 32768.0f / 32768.0f)
        : -100.0f;

    // 3) 喂组件（sample_count 必须是 mic_num=4 的整数倍）
    esp_apa_doa_result_t result = {};
    esp_err_t err = esp_apa_doa_process(AsHandle(doa_),
                                        scratch_.data(), frames * 4, &result);
    std::lock_guard<std::mutex> lock(mutex_);
    fed_frames_ += frames;
    if (err != ESP_OK) {
        return;
    }
    if (!result.valid) {
        return;
    }

    // 4) 质量门限 + 多帧中值
    estimate_.energy_db = energy_db;
    estimate_.band_energy_db = band_energy_db;
    estimate_.raw_azimuth_deg = result.azimuth_deg;
    estimate_.quality = result.quality;
    estimate_.timestamp_us = esp_timer_get_time();

    if (result.quality >= CONFIG_AI_DOG_DOA_MIN_QUALITY) {
        PushAzimuth(result.azimuth_deg, result.quality);
        estimate_.valid = true;
        valid_frames_++;
    } else {
        // 质量不够：保留上次可用角度，标记不可用
        estimate_.valid = false;
    }

    // 5) 决策日志（SW-003：每个决策点输出依据，节流防刷屏）
    const int64_t now = esp_timer_get_time();
    if (now - last_log_us_ >= kLogIntervalUs) {
        last_log_us_ = now;
        ESP_LOGI(TAG, "az=%.1f° quality=%.1f/%d energy=%.1fdB band=%.1fdB valid=%d "
                      "fed=%llu valid_n=%llu",
                 result.azimuth_deg, result.quality, CONFIG_AI_DOG_DOA_MIN_QUALITY,
                 energy_db, band_energy_db, estimate_.valid ? 1 : 0,
                 static_cast<unsigned long long>(fed_frames_),
                 static_cast<unsigned long long>(valid_frames_));
    }
}

void DoaEngine::PushAzimuth(float azimuth, float quality) {
    filter_window_.push_back(azimuth);
    while (static_cast<int>(filter_window_.size()) > CONFIG_AI_DOG_DOA_FILTER_FRAMES) {
        filter_window_.pop_front();
    }
    if (filter_window_.size() < 3) {
        estimate_.azimuth_deg = azimuth;
        estimate_.quality = quality;
        return;
    }
    // 中值：拷贝 + nth_element（窗长 ≤21，成本可忽略）
    std::vector<float> tmp(filter_window_.begin(), filter_window_.end());
    const size_t mid = tmp.size() / 2;
    std::nth_element(tmp.begin(), tmp.begin() + mid, tmp.end());
    float median = tmp[mid];
    // 环形均值平滑（处理 359°/0° 跨界）：与中值取近者
    float sum = 0.0f;
    for (float v : filter_window_) {
        float d = v - median;
        if (d > 180.0f) d -= 360.0f;
        if (d < -180.0f) d += 360.0f;
        sum += median + d;  // 统一到 median 附近
    }
    estimate_.azimuth_deg = 0.5f * (median + sum / filter_window_.size());
    estimate_.quality = quality;
}

void DoaEngine::Reset() {
    std::lock_guard<std::mutex> lock(mutex_);
    filter_window_.clear();
    estimate_.valid = false;
    if (doa_ != nullptr) {
        esp_apa_doa_reset(AsHandle(doa_));
    }
    for (int c = 0; c < 4; ++c) {
        hp_in_prev_[c] = 0.0f;
        hp_out_prev_[c] = 0.0f;
    }
    ESP_LOGI(TAG, "DOA 跟踪状态已重置（语句边界/重新定位）");
}

DoaEngine::Estimate DoaEngine::GetEstimate() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return estimate_;
}

uint64_t DoaEngine::GetFedFrames() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return fed_frames_;
}

uint64_t DoaEngine::GetValidFrames() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return valid_frames_;
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
