#include "ai_dog_voiceprint.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_mic_array.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_mn_iface.h>
#include <esp_mn_models.h>
#include <esp_mn_speech_commands.h>
#include <model_path.h>

#include <cstring>
#include <sstream>

#define TAG "summon"  // 口令验证归 summon 组（TAG 规范 SW-003）

namespace aidog {

static constexpr int kConsumerTaskStackSize = 4096;
static constexpr int kConsumerTaskPriority = 3;
// 每次从阵列拉的帧数（喂 MN 前先攒够 chunksize）
static constexpr size_t kPullFrames = 256;

AiDogVoiceprint& AiDogVoiceprint::GetInstance() {
    static AiDogVoiceprint instance;
    return instance;
}

void AiDogVoiceprint::ParseCommandWords() {
    command_words_.clear();
    std::istringstream ss(CONFIG_AI_DOG_SUMMON_COMMAND_WORDS);
    std::string word;
    while (ss >> word) {
        command_words_.push_back(word);
    }
    if (command_words_.empty()) {
        command_words_.push_back("guo lai");  // 兜底：主口令"过来"
    }
}

esp_err_t AiDogVoiceprint::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }
    ParseCommandWords();

#if defined(CONFIG_AI_DOG_VOICEPRINT_MULTINET)
    // ---- MultiNet 后端 ----
    models_ = esp_srmodel_init("model");
    owns_models_ = models_ != nullptr;
    if (models_ == nullptr || reinterpret_cast<srmodel_list_t*>(models_)->num == -1) {
        ESP_LOGE(TAG, "model 分区扫描失败：确认已选中文 MultiNet 模型（Kconfig 自动 select）");
        models_ = nullptr;
        return ESP_ERR_NOT_FOUND;
    }
    auto* models = static_cast<srmodel_list_t*>(models_);
    char* mn_name = esp_srmodel_filter(models, ESP_MN_PREFIX, "cn");
    if (mn_name == nullptr) {
        ESP_LOGW(TAG, "中文 MultiNet 不在位，回退任意 MultiNet 模型");
        mn_name = esp_srmodel_filter(models, ESP_MN_PREFIX, nullptr);
    }
    if (mn_name == nullptr) {
        ESP_LOGE(TAG, "MultiNet 模型不在位：Kconfig → ESP-SR → Chinese Multinet Model");
        if (owns_models_) {
            esp_srmodel_deinit(models);
            models_ = nullptr;
            owns_models_ = false;
        }
        return ESP_ERR_NOT_FOUND;
    }

    auto* multinet = esp_mn_handle_from_name(mn_name);
    if (multinet == nullptr) {
        ESP_LOGE(TAG, "esp_mn_handle_from_name(%s) 失败", mn_name);
        if (owns_models_) {
            esp_srmodel_deinit(models);
            models_ = nullptr;
            owns_models_ = false;
        }
        return ESP_FAIL;
    }
    // duration = MN 超时（ms）。命令词短，给 3s 的识别跨度。
    constexpr int kMnDurationMs = 3000;
    mn_model_data_ = multinet->create(mn_name, kMnDurationMs);
    if (mn_model_data_ == nullptr) {
        ESP_LOGE(TAG, "MultiNet create 失败");
        if (owns_models_) {
            esp_srmodel_deinit(models);
            models_ = nullptr;
            owns_models_ = false;
        }
        return ESP_FAIL;
    }
    multinet_ = multinet;
    multinet->set_det_threshold(static_cast<model_iface_data_t*>(mn_model_data_),
                                CONFIG_AI_DOG_SUMMON_COMMAND_THRESHOLD / 100.0f);
    mn_chunksize_ = multinet->get_samp_chunksize(static_cast<model_iface_data_t*>(mn_model_data_));
    const int mn_rate = multinet->get_samp_rate(static_cast<model_iface_data_t*>(mn_model_data_));
    if (mn_rate != 16000) {
        ESP_LOGE(TAG, "MultiNet 采样率 %d != 阵列 16000，暂不支持", mn_rate);
        multinet->destroy(static_cast<model_iface_data_t*>(mn_model_data_));
        mn_model_data_ = nullptr;
        multinet_ = nullptr;
        if (owns_models_) {
            esp_srmodel_deinit(models);
            models_ = nullptr;
            owns_models_ = false;
        }
        return ESP_ERR_NOT_SUPPORTED;
    }

    esp_mn_commands_clear();
    for (size_t i = 0; i < command_words_.size(); ++i) {
        esp_mn_commands_add(static_cast<int>(i) + 1, command_words_[i].c_str());
    }
    esp_mn_commands_update();
    multinet->print_active_speech_commands(static_cast<model_iface_data_t*>(mn_model_data_));
    ESP_LOGI(TAG, "L1 口令（MultiNet %s，阈值 %d%%）：", mn_name,
             CONFIG_AI_DOG_SUMMON_COMMAND_THRESHOLD);
    for (const auto& w : command_words_) {
        ESP_LOGI(TAG, "  - %s", w.c_str());
    }
#else
    // ---- AlwaysAccept 后端（bring-up / 仿真联调）----
    ESP_LOGW(TAG, "L1 口令后端 = AlwaysAccept（无模型 bring-up）：唤醒词后直接放行");
#endif

    shutdown_ = false;
    task_stack_ = static_cast<StackType_t*>(heap_caps_malloc(kConsumerTaskStackSize, MALLOC_CAP_SPIRAM));
    task_buffer_ = static_cast<StaticTask_t*>(heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL));
    if (task_stack_ == nullptr || task_buffer_ == nullptr) {
        ESP_LOGE(TAG, "口令消费者任务内存分配失败");
        return ESP_ERR_NO_MEM;
    }
    task_ = xTaskCreateStatic(ConsumerTask, "voiceprint", kConsumerTaskStackSize, this,
                              kConsumerTaskPriority, task_stack_, task_buffer_);
    if (task_ == nullptr) {
        ESP_LOGE(TAG, "口令消费者任务创建失败");
        heap_caps_free(task_stack_);
        heap_caps_free(task_buffer_);
        task_stack_ = nullptr;
        task_buffer_ = nullptr;
        return ESP_FAIL;
    }
    cursor_ = MicArray::GetInstance().GetWrittenFrames();
    enabled_ = true;
    return ESP_OK;
}

void AiDogVoiceprint::Shutdown() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!enabled_) {
            return;
        }
        shutdown_ = true;
        window_open_ = false;
    }
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
    if (multinet_ != nullptr && mn_model_data_ != nullptr) {
        static_cast<esp_mn_iface_t*>(multinet_)->destroy(static_cast<model_iface_data_t*>(mn_model_data_));
        mn_model_data_ = nullptr;
        multinet_ = nullptr;
    }
    if (owns_models_ && models_ != nullptr) {
        esp_srmodel_deinit(static_cast<srmodel_list_t*>(models_));
        models_ = nullptr;
        owns_models_ = false;
    }
    enabled_ = false;
    ESP_LOGI(TAG, "L1 口令验证已关闭");
}

void AiDogVoiceprint::StartWindow(int timeout_ms) {
    std::lock_guard<std::mutex> lock(mutex_);
    window_open_ = true;
    window_deadline_us_ = esp_timer_get_time() + timeout_ms * 1000LL;
    ESP_LOGI(TAG, "口令验证窗口打开：%dms，词表 %zu 个", timeout_ms, command_words_.size());
}

void AiDogVoiceprint::CancelWindow() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (window_open_) {
        window_open_ = false;
        ESP_LOGI(TAG, "口令验证窗口取消");
    }
}

bool AiDogVoiceprint::IsWindowOpen() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return window_open_;
}

void AiDogVoiceprint::SetPlaybackGated(bool gated) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (playback_gated_ != gated) {
        ESP_LOGI(TAG, "播放门控 %s（口令识别%s）", gated ? "开" : "关",
                 gated ? "暂停，防自触发" : "恢复");
    }
    playback_gated_ = gated;
}

std::vector<std::string> AiDogVoiceprint::GetCommandWords() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return command_words_;
}

void AiDogVoiceprint::NotifyResult(bool passed, const std::string& word) {
    ResultCallback cb;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        window_open_ = false;
        cb = result_callback_;
    }
    ESP_LOGI(TAG, "口令验证结果：%s（%s）", passed ? "通过" : "未通过",
             word.empty() ? "超时/未命中" : word.c_str());
    if (cb) {
        cb(passed, word);
    }
}

void AiDogVoiceprint::ConsumerTask(void* arg) {
    static_cast<AiDogVoiceprint*>(arg)->ConsumerLoop();
}

void AiDogVoiceprint::ConsumerLoop() {
    // 注意：MicArray::Read 每次写 frames * 4 个 int16（4 麦交错），
    // 这里只取通道 0 喂 MN —— 缓冲必须按 4 通道宽度分配，否则越界。
    static int16_t pull[kPullFrames * MicArray::kChannels];
    std::vector<int16_t> mono;
    mono.reserve(kPullFrames);

    while (true) {
        bool running = false;
        bool gated = false;
        int64_t deadline = 0;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (shutdown_) {
                return;
            }
            running = window_open_ && !playback_gated_;
            gated = playback_gated_;
            deadline = window_deadline_us_;
        }
        if (!running) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (esp_timer_get_time() > deadline) {
            NotifyResult(false, "");
            continue;
        }

        // 从阵列取单麦（通道 0）
        size_t got = MicArray::GetInstance().Read(cursor_, pull, kPullFrames);
        if (got == 0) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        mono.clear();
        mono.reserve(got);
        for (size_t i = 0; i < got; ++i) {
            mono.push_back(pull[i * 4]);  // 通道 0 = MIC1
        }

#if defined(CONFIG_AI_DOG_VOICEPRINT_MULTINET)
        auto* multinet = static_cast<esp_mn_iface_t*>(multinet_);
        if (multinet == nullptr || mn_model_data_ == nullptr) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        // 攒够一个检测块
        // （MN chunksize 一般数百样本，256 帧一批通常一次或多次 detect）
        // mn_model_data_ 存为 void* （头文件不依赖 esp_mn 头），这里显式回转为实际类型
        auto* mn_data = static_cast<model_iface_data_t*>(mn_model_data_);
        size_t offset = 0;
        while (offset + static_cast<size_t>(mn_chunksize_) <= mono.size()) {
            esp_mn_state_t state = multinet->detect(mn_data, mono.data() + offset);
            offset += mn_chunksize_;
            if (state == ESP_MN_STATE_DETECTED) {
                esp_mn_results_t* r = multinet->get_results(mn_data);
                for (int i = 0; i < r->num; ++i) {
                    const int id = r->command_id[i];
                    if (id >= 1 && id <= static_cast<int>(command_words_.size())) {
                        NotifyResult(true, command_words_[id - 1]);
                        break;
                    }
                }
                multinet->clean(mn_data);
                break;
            } else if (state == ESP_MN_STATE_TIMEOUT) {
                multinet->clean(mn_data);
            }
        }
        // 窗口内没说完的残余样本丢弃（口令短，跨窗无意义）
#else
        // AlwaysAccept：窗口打开即放行（模拟"听到了"）
        NotifyResult(true, "always-accept");
        vTaskDelay(pdMS_TO_TICKS(200));
#endif
    }
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
