#ifndef AI_DOG_VOICEPRINT_H
#define AI_DOG_VOICEPRINT_H

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

#include <esp_err.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>

// 编译开关（SW-001）：OFF 时本头文件不声明任何符号。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

namespace aidog {

/**
 * L1 口令验证（SW-202）：唤醒词命中后，限时监听 MultiNet 命令词。
 *
 * Q-01 决策 A 的落地：预置唤醒词"你好小智"（AFE WakeNet9，V1 已有）命中后
 * 打开验证窗口，窗口内说出口令（默认"过来/到这里来"，Kconfig 拼音配置）
 * 才进入 Summon；超时/未命中 → 回监听，纯唤醒词仍走原聊天链路。
 *
 * 后端（Kconfig AI_DOG_VOICEPRINT_BACKEND）：
 *   - MultiNet：esp-sr MultiNet 中文命令词（mn6_cn，Kconfig 自动选模型）。
 *     消费 MicArray 通道 0（单麦 16kHz），与 AFE 旁路共存。
 *   - AlwaysAccept：无模型 bring-up / 仿真联调，唤醒词后直接放行。
 *
 * L2 预留（P1-1）：VerifyEmbedding() 接口与 L2 声纹模型对齐，
 * 口令实测误召不可接受时再上（Q-13），本版只留接口不实现。
 *
 * 播放门控（AT-S07）：狗叫/聊天播报期间 SetPlaybackGated(true)，
 * 消费者任务停止喂模型，防止自己的声音触发口令。
 */
class AiDogVoiceprint {
public:
    using ResultCallback = std::function<void(bool passed, const std::string& word)>;

    static AiDogVoiceprint& GetInstance();

    /** 创建 MultiNet 实例 + 注册命令词。模型不在位时返回错误（召唤仍可用
     *  AlwaysAccept 后端或提示用户补模型）。 */
    esp_err_t Initialize();

    void Shutdown();
    bool IsEnabled() const { return enabled_; }

    /** 打开验证窗口（S1 入口）。已有窗口时刷新超时。 */
    void StartWindow(int timeout_ms);

    /** 关闭窗口（取消验证） */
    void CancelWindow();

    bool IsWindowOpen() const;

    /** 播放门控：true = 播报中，暂停口令识别 */
    void SetPlaybackGated(bool gated);

    void SetResultCallback(ResultCallback cb) {
        std::lock_guard<std::mutex> lock(mutex_);
        result_callback_ = std::move(cb);
    }

    /** L2 预留接口（P1-1，未实现）：将来接 x-vector 声纹比对 */
    bool VerifyEmbedding(const float* /*embedding*/, int /*dim*/) {
        return false;  // L2 未上车：一律不通过，调用方应退回 L1 口径
    }

    /** 调试：当前命令词表 */
    std::vector<std::string> GetCommandWords() const;

private:
    AiDogVoiceprint() = default;
    AiDogVoiceprint(const AiDogVoiceprint&) = delete;
    AiDogVoiceprint& operator=(const AiDogVoiceprint&) = delete;

    void ParseCommandWords();
    void NotifyResult(bool passed, const std::string& word);

    static void ConsumerTask(void* arg);
    void ConsumerLoop();

    mutable std::mutex mutex_;
    bool enabled_ = false;
    bool shutdown_ = false;
    bool window_open_ = false;
    bool playback_gated_ = false;
    int64_t window_deadline_us_ = 0;

    // MultiNet 实例（opaque）
    void* multinet_ = nullptr;       // esp_mn_iface_t*
    void* mn_model_data_ = nullptr;  // model_iface_data_t*
    void* models_ = nullptr;         // srmodel_list_t*（本模块自己 init 的）
    bool owns_models_ = false;
    int mn_chunksize_ = 0;
    std::vector<std::string> command_words_;

    // 消费者任务
    uint64_t cursor_ = 0;
    TaskHandle_t task_ = nullptr;
    StaticTask_t* task_buffer_ = nullptr;
    StackType_t* task_stack_ = nullptr;

    ResultCallback result_callback_;
};

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif  // AI_DOG_VOICEPRINT_H
