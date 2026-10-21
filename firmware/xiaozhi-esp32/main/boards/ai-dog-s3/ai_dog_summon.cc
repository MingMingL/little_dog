#include "ai_dog_summon.h"

// 编译开关（SW-001）：OFF 时本文件编译为空单元，构建产物与 V1 一致。
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON

#include "config.h"
#include "ai_dog_control.h"
#include "ai_dog_doa.h"
#include "ai_dog_mic_array.h"
#include "ai_dog_motor.h"
#include "ai_dog_vision.h"
#include "ai_dog_vl53l0x.h"
#include "ai_dog_voiceprint.h"

#include <application.h>

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>

#include <cmath>
#include <cstring>

#define TAG "summon"

namespace aidog {

// 状态机节拍：50ms。停-听-走的时间参数都是它的整数倍量级。
static constexpr int kTickMs = 50;
// S5 重扫总时长
static constexpr int kRescanMs = 8000;
// 连续多少个监听窗拿不到有效 DOA 判定为"声源丢失"（丢源 → S5）
static constexpr int kLostSourceWindows = 3;
// 避障重定向的原地转动时长（标定值：与转速相关，M2 实测回写）
static constexpr int kAvoidTurnMs = 400;
// S4 到达播报停留
static constexpr int kArriveGreetMs = 2500;

AiDogSummon& AiDogSummon::GetInstance() {
    static AiDogSummon instance;
    return instance;
}

const char* AiDogSummon::PhaseToString(Phase phase) {
    switch (phase) {
        case Phase::Idle:        return "idle";
        case Phase::Verifying:   return "verifying";
        case Phase::Listening:   return "listening";
        case Phase::Aligning:    return "aligning";
        case Phase::Moving:      return "moving";
        case Phase::Avoiding:    return "avoiding";
        case Phase::VisionCheck: return "vision";
        case Phase::Arrived:     return "arrived";
        case Phase::Rescanning:  return "rescan";
        case Phase::Finishing:   return "finishing";
        default:                 return "idle";
    }
}

esp_err_t AiDogSummon::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (enabled_) {
        return ESP_OK;
    }

    // ---- 子模块初始化（硬件未到货时逐个降级，不影响其他功能）----
    esp_err_t err = MicArray::GetInstance().Initialize();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "4 麦阵列初始化失败（%d）：召唤不可用，音频/遥控不受影响", err);
        return err;
    }
    err = DoaEngine::GetInstance().Initialize();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "DOA 初始化失败（%d）：按 R-F3 召唤只能纯声学到达，先关DOA", err);
        // DOA 失败不阻断：视觉/口令仍在（纯声学降级由状态机判断）
    }
    err = AiDogVoiceprint::GetInstance().Initialize();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "口令验证初始化失败（%d）：召唤不可用", err);
        return err;
    }
    // 测距/视觉/巡线失败都只记日志（对应功能降级）
    if (Vl53l0xSensor::GetInstance().Initialize() != ESP_OK) {
        ESP_LOGW(TAG, "VL53L0X 不可用：避障降级为只信 DOA（R-F 降级记录 SW-403）");
    }
#if defined(CONFIG_AI_DOG_VISION_ENABLE)
    if (AiDogVision::GetInstance().Initialize() != ESP_OK) {
        ESP_LOGW(TAG, "ArduCAM 不可用：按 R-F6 退回纯声学到达判定");
    }
#endif

    // 口令结果回调（S1 → S2 / 回 Idle）
    AiDogVoiceprint::GetInstance().SetResultCallback(
        [this](bool passed, const std::string& word) {
            if (!passed) {
                ESP_LOGI(TAG, "口令未通过（%s）：回监听，走原聊天链路（Q-02）",
                         word.empty() ? "超时" : word.c_str());
                SetPhase(Phase::Idle, "没听清");
                return;
            }
            ESP_LOGI(TAG, "口令通过（%s）：进入 Summon，抢占当前模式", word.c_str());
            auto& control = ControlServer::GetInstance();
            control.SetMode(Mode::Summon);
        });

    shutdown_ = false;
    task_stack_ = static_cast<StackType_t*>(heap_caps_malloc(4096, MALLOC_CAP_SPIRAM));
    task_buffer_ = static_cast<StaticTask_t*>(heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL));
    if (task_stack_ == nullptr || task_buffer_ == nullptr) {
        ESP_LOGE(TAG, "状态机任务内存分配失败");
        return ESP_ERR_NO_MEM;
    }
    task_ = xTaskCreateStatic(StateMachineTask, "summon", 4096, this, 3,
                              task_stack_, task_buffer_);
    if (task_ == nullptr) {
        ESP_LOGE(TAG, "状态机任务创建失败");
        heap_caps_free(task_stack_);
        heap_caps_free(task_buffer_);
        task_stack_ = nullptr;
        task_buffer_ = nullptr;
        return ESP_FAIL;
    }
    enabled_ = true;
    ESP_LOGI(TAG, "Summon 状态机就绪：口令超时 %dms，监听 %dms，步长 %dms，总时限 %dms",
             CONFIG_AI_DOG_SUMMON_COMMAND_TIMEOUT_MS, CONFIG_AI_DOG_SUMMON_LISTEN_MS,
             CONFIG_AI_DOG_SUMMON_STEP_MS, CONFIG_AI_DOG_SUMMON_TOTAL_TIMEOUT_MS);
    return ESP_OK;
}

void AiDogSummon::Shutdown() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!enabled_) {
            return;
        }
        shutdown_ = true;
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
    enabled_ = false;
    status_ = Status{};
    ESP_LOGI(TAG, "Summon 状态机已关闭");
}

// ---------------------------------------------------------------------------
// 对外入口
// ---------------------------------------------------------------------------

void AiDogSummon::OnWakeWordHeard(const std::string& wake_word) {
    if (!enabled_) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (status_.phase != Phase::Idle) {
        ESP_LOGI(TAG, "唤醒词命中但已在召唤流程（%s）：忽略", PhaseToString(status_.phase));
        return;
    }

    // Q-11：低压预检，低于阈值拒绝进入
    const int battery = ControlServer::GetInstance().GetBatteryPercent();
    if (battery >= 0 && battery < CONFIG_AI_DOG_SUMMON_LOW_BATTERY_PCT) {
        ESP_LOGW(TAG, "电量 %d%% < %d%%：拒绝进入召唤（Q-11 预检）",
                 battery, CONFIG_AI_DOG_SUMMON_LOW_BATTERY_PCT);
        return;
    }
    // Q-08/R-F10：看门模式按开关决定是否允许被召唤抢占
    const Mode mode = ControlServer::GetInstance().GetMode();
    if (mode == Mode::Guard && !CONFIG_AI_DOG_SUMMON_GUARD_ALLOW) {
        ESP_LOGW(TAG, "看门时段禁用召唤（Q-08）：忽略唤醒词");
        return;
    }

    ESP_LOGI(TAG, "唤醒词命中（%s）：打开口令验证窗口 %dms",
             wake_word.c_str(), CONFIG_AI_DOG_SUMMON_COMMAND_TIMEOUT_MS);
    status_.phase = Phase::Verifying;
    status_.message = "验证口令";
    status_.elapsed_ms = 0;
    AiDogVoiceprint::GetInstance().StartWindow(CONFIG_AI_DOG_SUMMON_COMMAND_TIMEOUT_MS);
}

void AiDogSummon::SetPlaybackGated(bool gated) {
    AiDogVoiceprint::GetInstance().SetPlaybackGated(gated);
}

AiDogSummon::Status AiDogSummon::GetStatus() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return status_;
}

bool AiDogSummon::IsActive() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return status_.phase != Phase::Idle;
}

// ---------------------------------------------------------------------------
// 状态机
// ---------------------------------------------------------------------------

void AiDogSummon::SetPhase(Phase phase, const char* message) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (status_.phase != phase) {
        ESP_LOGI(TAG, "阶段 %s -> %s（%s）", PhaseToString(status_.phase),
                 PhaseToString(phase), message ? message : "");
    }
    status_.phase = phase;
    status_.message = message ? message : "";
}

void AiDogSummon::StateMachineTask(void* arg) {
    static_cast<AiDogSummon*>(arg)->StateMachineLoop();
}

void AiDogSummon::StateMachineLoop() {
    // 阶段内子状态机：用 sub_step 编码当前阶段内的动作
    int sub_step = 0;
    int64_t sub_deadline = 0;

    while (true) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (shutdown_) {
                return;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(kTickMs));

        // 播放门控（AT-S07 自触发抑制）：狗叫/聊天播报期间暂停口令识别，
        // 防止设备自己的声音触发口令/唤醒。
        AiDogVoiceprint::GetInstance().SetPlaybackGated(Application::GetInstance().IsPlaybackPlaying());

        Phase phase;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            phase = status_.phase;
            if (phase == Phase::Idle) {
                sub_step = 0;
                continue;
            }
            //  elapsed 累计
            if (summon_start_us_ != 0) {
                status_.elapsed_ms = static_cast<int>(
                    (esp_timer_get_time() - summon_start_us_) / 1000);
            }
        }

        // ---- 全局安全闸（S1 验证期不检：此时模式还是原模式，且不动）----
        auto& control = ControlServer::GetInstance();
        const Mode mode = control.GetMode();
        if (phase != Phase::Verifying && mode != Mode::Summon) {
            // 急停/被抢：立即停车回 Idle（Q-09：不自动恢复原模式）
            ESP_LOGW(TAG, "模式已变为 %s（非 Summon）：召唤中止", ModeToString(mode));
            MotorDriver::GetInstance().Stop();
            DoaEngine::GetInstance().SetActive(false);
            SetPhase(Phase::Idle, "已停止");
            continue;
        }
        const int battery = control.GetBatteryPercent();
        if (battery >= 0 && battery < CONFIG_AI_DOG_SUMMON_LOW_BATTERY_PCT) {
            ESP_LOGW(TAG, "过程中低压 %d%%：立即停车回 Idle 并播报（Q-11）", battery);
            MotorDriver::GetInstance().Stop();
            DoaEngine::GetInstance().SetActive(false);
            if (!low_battery_announced_) {
                low_battery_announced_ = true;
                Application::GetInstance().Alert("召唤", "电量不足，我先停下", "sad", "low_battery");
            }
            control.SetMode(Mode::Idle);
            SetPhase(Phase::Idle, "电量不足");
            continue;
        }
        int elapsed_ms = 0;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            elapsed_ms = status_.elapsed_ms;
        }
        if (elapsed_ms > CONFIG_AI_DOG_SUMMON_TOTAL_TIMEOUT_MS && phase != Phase::Rescanning) {
            // 注意 != Rescanning：重搜阶段有自己的 8s 预算，不能被总时限
            // 每个 tick 反复打回重搜入口（仿真 SW-004 抓到的死循环）。
            ESP_LOGW(TAG, "召唤超时（%dms > %dms）：进入 S5 重搜",
                     elapsed_ms, CONFIG_AI_DOG_SUMMON_TOTAL_TIMEOUT_MS);
            SetPhase(Phase::Rescanning, "再找找");
            sub_step = 0;
            continue;
        }

        // ---- 阶段处理 ----
        switch (phase) {
            case Phase::Verifying: {
                // S1：口令回调通过时会把模式切成 Summon，这里检测到即进入 S2；
                // 未通过时回调直接把阶段置回 Idle。
                if (ControlServer::GetInstance().GetMode() == Mode::Summon) {
                    std::lock_guard<std::mutex> lock(mutex_);
                    summon_start_us_ = esp_timer_get_time();
                    vision_tries_ = 0;
                    consecutive_lost_ = 0;
                    rescan_count_ = 0;
                    low_battery_announced_ = false;
                    status_.distance_mm = -1;
                    status_.vision_result = -1;
                }
                DoaEngine::GetInstance().SetActive(true);
                DoaEngine::GetInstance().Reset();
                SetPhase(Phase::Listening, "找声音");
                sub_step = 0;
                break;
            }

            case Phase::Listening: {
                // 听 kListenMs：停-听-走的"听"。DOA 整个 S2 期间活跃，
                // 每个监听窗起点 Reset（语句边界，拿干净估计）。
                if (sub_step == 0) {
                    MotorDriver::GetInstance().Stop();
                    DoaEngine::GetInstance().SetActive(true);
                    DoaEngine::GetInstance().Reset();
                    sub_deadline = esp_timer_get_time() + CONFIG_AI_DOG_SUMMON_LISTEN_MS * 1000LL;
                    sub_step = 1;
                } else if (esp_timer_get_time() >= sub_deadline) {
                    DoaEngine::Estimate est = DoaEngine::GetInstance().GetEstimate();
                    {
                        std::lock_guard<std::mutex> lock(mutex_);
                        status_.doa_deg = est.azimuth_deg;
                        status_.energy_db = est.energy_db;
                    }
                    if (!est.valid) {
                        consecutive_lost_++;
                        ESP_LOGI(TAG, "监听窗无有效 DOA（连续 %d/%d）",
                                 consecutive_lost_, kLostSourceWindows);
                        if (consecutive_lost_ >= kLostSourceWindows) {
                            SetPhase(Phase::Rescanning, "声源丢了，原地找找");
                            sub_step = 0;
                            break;
                        }
                        // 没丢但没数据：原地慢转一点再听（扩大搜索）
                        MotorDriver::GetInstance().NudgeTurn(1, CONFIG_AI_DOG_MOTOR_NUDGE_DUTY,
                                                             CONFIG_AI_DOG_MOTOR_NUDGE_MS * 4);
                        SetPhase(Phase::Listening, "再听听");
                        sub_step = 0;
                        break;
                    }
                    consecutive_lost_ = 0;
                    // 方位角 → 有符号角度误差（+Y=0° 顺时针；正=源在右侧，应右转）
                    float err = est.azimuth_deg;
                    if (err > 180.0f) err -= 360.0f;
                    last_angle_error_deg_ = err;
                    ESP_LOGI(TAG, "监听窗结束：DOA=%.1f°（误差 %+.1f°，死区 ±%d°）能量=%.1fdB",
                             est.azimuth_deg, err, CONFIG_AI_DOG_DOA_DEADZONE_DEG, est.energy_db);
                    if (std::fabs(err) <= CONFIG_AI_DOG_DOA_DEADZONE_DEG) {
                        SetPhase(Phase::Moving, "前进");
                        sub_step = 0;
                    } else {
                        SetPhase(Phase::Aligning, "对准中");
                        sub_step = 0;
                    }
                }
                break;
            }

            case Phase::Aligning: {
                // 原地对准：点动脉冲 + 复听，直到死区内或超时
                if (sub_step == 0) {
                    sub_deadline = esp_timer_get_time() + CONFIG_AI_DOG_SUMMON_ALIGN_MAX_MS * 1000LL;
                    sub_step = 1;
                }
                DoaEngine::Estimate est = DoaEngine::GetInstance().GetEstimate();
                float err = est.valid ? est.azimuth_deg : 0.0f;
                if (err > 180.0f) err -= 360.0f;
                if (est.valid && std::fabs(err) <= CONFIG_AI_DOG_DOA_DEADZONE_DEG) {
                    ESP_LOGI(TAG, "对准完成：误差 %+.1f°（DOA=%.1f°）", err, est.azimuth_deg);
                    SetPhase(Phase::Moving, "前进");
                    sub_step = 0;
                    break;
                }
                if (esp_timer_get_time() >= sub_deadline) {
                    ESP_LOGW(TAG, "对准超时（%dms）：按当前角度前进", CONFIG_AI_DOG_SUMMON_ALIGN_MAX_MS);
                    SetPhase(Phase::Moving, "前进");
                    sub_step = 0;
                    break;
                }
                // 点动一步（短脉冲差速），随后节拍自动复听（DOA 任务仍在跑）
                const int dir = (err >= 0.0f) ? 1 : -1;
                MotorDriver::GetInstance().NudgeTurn(dir, CONFIG_AI_DOG_MOTOR_NUDGE_DUTY,
                                                     CONFIG_AI_DOG_MOTOR_NUDGE_MS);
                vTaskDelay(pdMS_TO_TICKS(CONFIG_AI_DOG_MOTOR_NUDGE_MS + 30));
                break;
            }

            case Phase::Moving: {
                // 前进 kStepMs，中途测距避障
                if (sub_step == 0) {
                    sub_deadline = esp_timer_get_time() + CONFIG_AI_DOG_SUMMON_STEP_MS * 1000LL;
                    MotorDriver::GetInstance().CruiseForward(CruiseDutyPercent());
                    sub_step = 1;
                    ESP_LOGI(TAG, "前进一步：%dms @ %d%%", CONFIG_AI_DOG_SUMMON_STEP_MS,
                             CruiseDutyPercent());
                }
                // 测距（阻塞 ~30-40ms，节拍容忍）
                uint16_t dist = Vl53l0xSensor::GetInstance().ReadMillimeters(100);
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    status_.distance_mm = (dist == 65535) ? -1 : static_cast<int>(dist);
                }
                if (dist != 65535 && dist < CONFIG_AI_DOG_VL53L0X_THRESHOLD_MM) {
                    ESP_LOGW(TAG, "避障：前向 %dmm < %dmm，停车重定向",
                             dist, CONFIG_AI_DOG_VL53L0X_THRESHOLD_MM);
                    MotorDriver::GetInstance().Stop();
                    SetPhase(Phase::Avoiding, "绕一下");
                    sub_step = 0;
                    break;
                }
                if (esp_timer_get_time() >= sub_deadline) {
                    MotorDriver::GetInstance().Stop();
                    if (IsApproachTriggered()) {
                        SetPhase(Phase::VisionCheck, "看看是不是你");
                        sub_step = 0;
                    } else {
                        SetPhase(Phase::Listening, "听听");
                        sub_step = 0;
                    }
                }
                break;
            }

            case Phase::Avoiding: {
                // 原地重定向：转开障碍方向再回监听
                if (sub_step == 0) {
                    sub_deadline = esp_timer_get_time() + kAvoidTurnMs * 1000LL;
                    MotorDriver::GetInstance().NudgeTurn(1, CONFIG_AI_DOG_MOTOR_NUDGE_DUTY,
                                                         CONFIG_AI_DOG_MOTOR_NUDGE_MS);
                    sub_step = 1;
                } else if (esp_timer_get_time() >= sub_deadline) {
                    MotorDriver::GetInstance().Stop();
                    SetPhase(Phase::Listening, "重新听");
                    sub_step = 0;
                }
                break;
            }

            case Phase::VisionCheck: {
                DoVisionCheck();
                break;
            }

            case Phase::Arrived: {
                // S4：播报后自动切 Chat
                if (sub_step == 0) {
                    MotorDriver::GetInstance().Stop();
                    DoaEngine::GetInstance().SetActive(false);
                    sub_deadline = esp_timer_get_time() + kArriveGreetMs * 1000LL;
                    PlayBarkAndGreet();
                    sub_step = 1;
                } else if (esp_timer_get_time() >= sub_deadline) {
                    ESP_LOGI(TAG, "到达完成：自动切 Chat（AT-S06）");
                    control.SetMode(Mode::Chat);
                    SetPhase(Phase::Idle, "来啦");
                    sub_step = 0;
                }
                break;
            }

            case Phase::Rescanning: {
                DoRescan();
                break;
            }

            default:
                break;
        }
    }
}

int AiDogSummon::CruiseDutyPercent() {
    // 注意：C++ 里未定义的开关应用 #ifdef（直接写在 if() 里会报“not declared”）
#ifdef CONFIG_AI_DOG_SUMMON_MOVE_DUTY_30
    if (true) {
        return 30;  // R-F1 自噪抑制降级档
    }
#endif
    return CONFIG_AI_DOG_SUMMON_CRUISE_DUTY;
}

bool AiDogSummon::IsApproachTriggered() const {
    // 条件一：DOA 稳定（监听窗内偏差小）——用最近误差的绝对值近似；
    // 条件二：近场能量或测距进入接近段
    DoaEngine::Estimate est = DoaEngine::GetInstance().GetEstimate();
    std::lock_guard<std::mutex> lock(mutex_);
    const bool energy_near = est.valid && est.energy_db >= CONFIG_AI_DOG_SUMMON_NEAR_ENERGY_DB;
    const bool distance_near = status_.distance_mm > 0 &&
                               status_.distance_mm < CONFIG_AI_DOG_SUMMON_APPROACH_STOP_MM;
    const bool doa_stable = std::fabs(last_angle_error_deg_) <= CONFIG_AI_DOG_DOA_STABLE_DEG;
    if (energy_near || distance_near) {
        ESP_LOGI(TAG, "接近段触发：能量=%.1fdB(门限 %d) 测距=%dmm(门限 %d) DOA稳定=%d",
                 est.energy_db, CONFIG_AI_DOG_SUMMON_NEAR_ENERGY_DB,
                 status_.distance_mm, CONFIG_AI_DOG_SUMMON_APPROACH_STOP_MM, doa_stable ? 1 : 0);
        return true;
    }
    return false;
}

void AiDogSummon::DoVisionCheck() {
    // 注意：本函数在状态机任务上下文调用，不能持锁调 SetPhase（会死锁）
#if defined(CONFIG_AI_DOG_VISION_ENABLE)
    if (!AiDogVision::GetInstance().IsEnabled() || !AiDogVision::GetInstance().IsRuntimeEnabled()) {
        // R-F6：摄像头不可用或 App 关了视觉 → 纯声学到达判定
        ESP_LOGW(TAG, "视觉不可用/已关闭：按 R-F6 纯声学到达判定（AT-S04 口径）");
        {
            std::lock_guard<std::mutex> lock(mutex_);
            status_.vision_used = false;
            status_.vision_result = -1;
        }
        SetPhase(Phase::Arrived, "到达");
        return;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.vision_used = true;
        status_.vision_tries = ++vision_tries_;
    }
    ESP_LOGI(TAG, "S3 视觉确认：第 %d/%d 次，帧预算 %dms",
             status_.vision_tries, CONFIG_AI_DOG_VISION_MAX_TRIES,
             CONFIG_AI_DOG_VISION_FRAME_BUDGET_MS);
    int result = AiDogVision::GetInstance().ConfirmHuman(
        CONFIG_AI_DOG_VISION_MAX_TRIES, CONFIG_AI_DOG_VISION_FRAME_BUDGET_MS);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.vision_result = result;
    }
    if (result == 1) {
        ESP_LOGI(TAG, "视觉确认：面前有人形 → S4 到达");
        SetPhase(Phase::Arrived, "看到你啦");
        return;
    }
    if (vision_tries_ >= CONFIG_AI_DOG_VISION_MAX_TRIES) {
        ESP_LOGW(TAG, "视觉确认 %d 次均无人形 → S5 降级", vision_tries_);
        SetPhase(Phase::Rescanning, "没看到人");
        return;
    }
    ESP_LOGI(TAG, "视觉确认无人形（结果 %d）：回 S2 再接近", result);
    SetPhase(Phase::Listening, "再听听");
#else
    // 编译期关闭视觉：直接到达（纯声学）
    {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.vision_used = false;
        status_.vision_result = -1;
    }
    SetPhase(Phase::Arrived, "到达");
#endif
}

void AiDogSummon::DoRescan() {
    // S5：原地 360° 匀速扫一次，边走边听；找到稳定声源回 S2，否则回 Idle。
    // 重搜上限：每次召唤最多 2 次 S5（方案 §4.2 “重搜一次”的可重试边界），
    // 第 2 次仍找不到一律回 Idle——防止“找得到但视觉过不去”无限循环。
    const int64_t now = esp_timer_get_time();
    if (rescan_start_us_ == 0) {
        rescan_start_us_ = now;
        last_nudge_us_ = 0;
        rescan_count_++;
        DoaEngine::GetInstance().SetActive(true);
        DoaEngine::GetInstance().Reset();
        ESP_LOGI(TAG, "S5 重搜（第 %d 次，上限 2）：原地 360° 扫描（%dms 预算）",
                 rescan_count_, kRescanMs);
    }
    // 每 150ms 一个点动脉冲，累计转动约 360°
    if (now - last_nudge_us_ > 150 * 1000LL) {
        last_nudge_us_ = now;
        MotorDriver::GetInstance().NudgeTurn(1, CONFIG_AI_DOG_MOTOR_NUDGE_DUTY,
                                             CONFIG_AI_DOG_MOTOR_NUDGE_MS);
    }
    const bool last_chance = rescan_count_ >= 2;
    DoaEngine::Estimate est = DoaEngine::GetInstance().GetEstimate();
    if (est.valid && est.quality >= CONFIG_AI_DOG_DOA_MIN_QUALITY && !last_chance) {
        ESP_LOGI(TAG, "S5 重搜命中：DOA=%.1f° quality=%.1f → 回 S2",
                 est.azimuth_deg, est.quality);
        rescan_start_us_ = 0;
        SetPhase(Phase::Listening, "找到了");
        return;
    }
    if (now - rescan_start_us_ > kRescanMs * 1000LL) {
        ESP_LOGW(TAG, "S5 重搜失败（第 %d 次）：回 Idle，App 提示\"没找到你\"（AT-S08）",
                 rescan_count_);
        rescan_start_us_ = 0;
        MotorDriver::GetInstance().Stop();
        DoaEngine::GetInstance().SetActive(false);
        ControlServer::GetInstance().SetMode(Mode::Idle);
        SetPhase(Phase::Idle, "没找到你");
    }
}

void AiDogSummon::PlayBarkAndGreet() {
    // S4：停车 → 狗叫/问候 → 自动切 Chat。
    // 狗叫素材（Q-10）：V1 狗叫模块/后续 opus 素材就位后把 sound 换成狗叫名；
    // 当前先用成功音 + 屏幕播报（不依赖网络，本地预置短音频）。
    auto& app = Application::GetInstance();
    app.Alert("召唤", "我来啦！", "happy", "success");
    ESP_LOGI(TAG, "S4 到达：播报问候（狗叫素材就位前用 success 音占位）");
}

}  // namespace aidog

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON
