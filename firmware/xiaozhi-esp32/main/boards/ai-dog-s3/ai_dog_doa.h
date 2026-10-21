#ifndef AI_DOG_DOA_H
#define AI_DOG_DOA_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <vector>

#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * DOA 声源定向（SW-103）：esp_apa_doa 封装。
 *
 * - 几何：4 麦方阵 ESP_APA_DOA_MIC_POS_SQUARE（边长 AI_DOG_MIC_ARRAY_SIZE_MM），
 *   360° 无前后模糊；方位角约定 +Y=0°（正前）、顺时针（90°=右）。
 * - 频带：按阵距裁剪，上限走组件 fmax_hz（Kconfig AI_DOG_DOA_FMAX_HZ，
 *   防空间混叠）；下限用一阶高通预滤波（AI_DOG_DOA_FMIN_HZ，
 *   抑制电机低频轰鸣），代价 ≈ 每样本每麦 2 乘加。
 * - 滤波：多帧中值（AI_DOG_DOA_FILTER_FRAMES），只收 quality >=
 *   AI_DOG_DOA_MIN_QUALITY 的估计（组件 valid 之外的第二道门限）。
 * - 能量：喂入帧的带内/全带 RMS（dBFS），供 Summon 近场特征与日志。
 *
 * 线程模型：Feed() 只允许在一个任务上下文调用（组件非线程安全），
 * GetEstimate() 可从任意任务读（内部加锁）。
 */
class DoaEngine {
public:
    struct Estimate {
        bool valid = false;
        float azimuth_deg = 0.0f;   // 0..360，+Y=0° 顺时针；多帧中值滤波后
        float raw_azimuth_deg = 0.0f;  // 本帧原始值（日志用）
        float quality = 0.0f;       // 0..10
        float energy_db = -100.0f;  // 全带 RMS（dBFS）
        float band_energy_db = -100.0f;  // 高通后带内 RMS（dBFS）
        int64_t timestamp_us = 0;
    };

    static DoaEngine& GetInstance();

    /** 创建引擎（方阵几何 + Kconfig 参数）。失败返回错误，召唤音频链应降级。 */
    esp_err_t Initialize();

    /** 销毁引擎 */
    void Shutdown();

    bool IsEnabled() const { return enabled_; }

    /**
     * 启用/暂停消费。暂停时消费者任务休眠，不占 CPU（Summon 空闲期
     * DOA 不跑，把 CPU 还给 V1 聊天链路）。进入 S1 时置 true。
     */
    void SetActive(bool active);
    bool IsActive() const;

    /**
     * 喂一帧 4 通道交错 PCM（与 MicArray 输出一致：mic0..mic3）。
     * frames 任意（内部按 mic_num 倍数分批喂组件）。
     * 一般不需要手动调用——消费者任务已从 MicArray 拉流；仅仿真/测试用。
     */
    void Feed(const int16_t* interleaved4, size_t frames);

    /** 语句边界（VAD 起止 / 重新定位）时调用：清跟踪状态与滤波窗 */
    void Reset();

    /** 取当前估计（线程安全） */
    Estimate GetEstimate() const;

    /** 累计喂入帧数 / 产生过估计的帧数（调试统计） */
    uint64_t GetFedFrames() const;
    uint64_t GetValidFrames() const;

private:
    DoaEngine() = default;
    DoaEngine(const DoaEngine&) = delete;
    DoaEngine& operator=(const DoaEngine&) = delete;

    void ApplyHighPass(int16_t* buf, size_t frames);
    void PushAzimuth(float azimuth, float quality);

    mutable std::mutex mutex_;
    bool enabled_ = false;
    bool active_ = false;
    bool shutdown_ = false;
    void* doa_ = nullptr;  // esp_apa_doa_handle_t（opaque，避免头文件依赖扩散）

    // 消费者任务：从 MicArray 拉 4 通道流喂引擎
    uint64_t cursor_ = 0;
    TaskHandle_t task_ = nullptr;
    StaticTask_t* task_buffer_ = nullptr;
    StackType_t* task_stack_ = nullptr;
    static void ConsumerTask(void* arg);
    void ConsumerLoop();

    // 高通预滤波状态（每通道一个一阶 IIR）
    float hp_in_prev_[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    float hp_out_prev_[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    float hp_alpha_ = 0.5f;

    std::deque<float> filter_window_;
    std::vector<int16_t> scratch_;  // 高通后的交错缓冲

    Estimate estimate_;
    uint64_t fed_frames_ = 0;
    uint64_t valid_frames_ = 0;
    int64_t last_log_us_ = 0;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_DOA_H
