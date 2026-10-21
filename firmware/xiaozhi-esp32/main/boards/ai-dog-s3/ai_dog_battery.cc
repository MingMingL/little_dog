#include "ai_dog_battery.h"

#include "config.h"
#include "ai_dog_diag.h"

#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>
#include <esp_adc/adc_oneshot.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <driver/gpio.h>

#include <algorithm>
#include <cmath>

#define TAG "AiDogBattery"

namespace {

// 2S 锂电（两节串联）的开路电压-容量曲线。满电 8.40V，标称 7.40V，放到 6.6V 就该充电了。
struct CurvePoint {
    float voltage;
    int percent;
};

constexpr CurvePoint kCurve[] = {
    {8.40f, 100}, {8.28f, 90}, {8.16f, 80}, {8.04f, 70}, {7.92f, 60},
    {7.80f, 50},  {7.68f, 40}, {7.56f, 30}, {7.32f, 20}, {7.08f, 10},
    {6.60f, 0},
};

constexpr size_t kCurveCount = sizeof(kCurve) / sizeof(kCurve[0]);

constexpr int kSampleCount = 8;              // 每次采样取 8 个点平均，压掉 ADC 噪声
constexpr uint64_t kSamplePeriodUs = 2 * 1000 * 1000;  // 2 秒一次

// 进入 Restrict/Cutoff 前要连续几次都读低。ADC 抖动或分压接触不良造成的一次偏低
// 不该把狗关机，所以要确认；2 秒一次 × 3 = 6 秒。
constexpr int kConfirmSamples = 3;

// 心跳：电压没变化也定期打一条。电池测试时要看趋势，无效读数时要留着标定分压比。
constexpr int kHeartbeatSamples = 15;  // 2 秒 × 15 = 30 秒

// 档位没翻转但电压跳了这么多也补一条：电池测试看的是趋势，只看档位会漏掉缓降
constexpr float kReportDeltaV = 0.15f;

}  // namespace

namespace aidog {

BatteryMonitor& BatteryMonitor::GetInstance() {
    static BatteryMonitor instance;
    return instance;
}

int BatteryMonitor::PercentForVoltage(float voltage) {
    if (voltage >= kCurve[0].voltage) return 100;
    if (voltage <= kCurve[kCurveCount - 1].voltage) return 0;
    for (size_t i = 1; i < kCurveCount; ++i) {
        if (voltage >= kCurve[i].voltage) {
            // 线性插值
            float span = kCurve[i - 1].voltage - kCurve[i].voltage;
            float ratio = (kCurve[i - 1].voltage - voltage) / span;
            int value = static_cast<int>(kCurve[i - 1].percent -
                                         ratio * (kCurve[i - 1].percent - kCurve[i].percent));
            return std::max(0, std::min(100, value));
        }
    }
    return 0;
}

const char* BatteryLevelText(BatteryMonitor::Level level) {
    switch (level) {
        case BatteryMonitor::Level::Normal:   return "normal";
        case BatteryMonitor::Level::Warn:     return "warn";
        case BatteryMonitor::Level::Restrict: return "restrict";
        case BatteryMonitor::Level::Cutoff:   return "cutoff";
        default:                              return "unknown";
    }
}

void BatteryMonitor::Init() {
    // GPIO8 = ADC1_CH7
    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = ADC_UNIT_1,
        .clk_src = ADC_RTC_CLK_SRC_DEFAULT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    esp_err_t err = adc_oneshot_new_unit(&unit_cfg, reinterpret_cast<adc_oneshot_unit_handle_t*>(&adc_handle_));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC 单元创建失败 (%s)，电池监测不可用", esp_err_to_name(err));
        adc_handle_ = nullptr;
        return;
    }

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    err = adc_oneshot_config_channel(reinterpret_cast<adc_oneshot_unit_handle_t>(adc_handle_),
                                     ADC_CHANNEL_7, &chan_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC 通道配置失败 (%s)，电池监测不可用", esp_err_to_name(err));
        return;
    }

    adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id = ADC_UNIT_1,
        .chan = ADC_CHANNEL_7,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    err = adc_cali_create_scheme_curve_fitting(&cali_cfg,
            reinterpret_cast<adc_cali_handle_t*>(&cali_handle_));
    if (err != ESP_OK) {
        // 没有校准也能读，只是精度差些；不致命
        ESP_LOGW(TAG, "ADC 校准创建失败 (%s)，用未校准读数", esp_err_to_name(err));
        cali_handle_ = nullptr;
    }

    esp_timer_create_args_t timer_cfg = {
        .callback = &BatteryMonitor::TimerCallback,
        .arg = this,
        .name = "ai_dog_battery",
    };
    if (esp_timer_create(&timer_cfg, reinterpret_cast<esp_timer_handle_t*>(&timer_handle_)) != ESP_OK) {
        ESP_LOGE(TAG, "电池采样定时器创建失败");
        return;
    }
    esp_timer_start_periodic(reinterpret_cast<esp_timer_handle_t>(timer_handle_), kSamplePeriodUs);

    ESP_LOGI(TAG, "电池监测就绪：GPIO%d / ADC1_CH7，分压比 %.2f", DOG_BATTERY_ADC_GPIO,
             static_cast<double>(DOG_BATTERY_DIVIDER));
    Sample();  // 立刻采一次，不用等 2 秒
}

void BatteryMonitor::TimerCallback(void* arg) {
    static_cast<BatteryMonitor*>(arg)->Sample();
}

void BatteryMonitor::Sample() {
    auto unit = reinterpret_cast<adc_oneshot_unit_handle_t>(adc_handle_);
    if (unit == nullptr) return;

    sample_count_++;

    int sum = 0;
    int got = 0;
    for (int i = 0; i < kSampleCount; ++i) {
        int raw = 0;
        if (adc_oneshot_read(unit, ADC_CHANNEL_7, &raw) == ESP_OK) {
            sum += raw;
            got++;
        }
    }
    if (got == 0) return;

    int raw = sum / got;
    int mv = 0;
    if (cali_handle_ != nullptr) {
        adc_cali_raw_to_voltage(reinterpret_cast<adc_cali_handle_t>(cali_handle_), raw, &mv);
    } else {
        mv = raw * 3300 / 4095;  // 12 位、3.3V 参考的粗算
    }

    raw_mv_ = static_cast<uint32_t>(mv);
    float voltage = mv / 1000.0f * DOG_BATTERY_DIVIDER;

    float was_voltage = voltage_;

    valid_ = (voltage >= DOG_BATTERY_VALID_MIN_V && voltage <= DOG_BATTERY_VALID_MAX_V);
    if (valid_) {
        voltage_ = voltage;
        percent_ = PercentForVoltage(voltage);
    } else {
        voltage_ = -1.0f;
        percent_ = -1;
    }

    // 状态翻转才打日志 + 回调，避免 2 秒一条刷屏
    Level next = Level::Unknown;
    if (valid_) {
        if (voltage <= DOG_BATTERY_CUTOFF_V)        next = Level::Cutoff;
        else if (voltage <= DOG_BATTERY_RESTRICT_V) next = Level::Restrict;
        else if (voltage <= DOG_BATTERY_WARN_V)     next = Level::Warn;
        else                                        next = Level::Normal;
    }

    // Restrict/Cutoff 要连续几次都读低才认，防 ADC 抖动误关机；
    // 回到 Normal/Warn 立即生效（宁可多提醒，不可晚动作）。
    bool needs_confirm = (next == Level::Restrict || next == Level::Cutoff);
    if (needs_confirm && next != level_) {
        if (next == pending_level_) {
            pending_count_++;
        } else {
            pending_level_ = next;
            pending_count_ = 1;
        }
        if (pending_count_ < kConfirmSamples) {
            ESP_LOGW(TAG, "电池 %.2fV 疑似进入 %s（%d/%d 次确认中）",
                     static_cast<double>(voltage), BatteryLevelText(next),
                     pending_count_, kConfirmSamples);
            return;
        }
    } else {
        pending_level_ = Level::Unknown;
        pending_count_ = 0;
    }

    bool voltage_jumped = valid_ && std::fabs(voltage_ - was_voltage) > kReportDeltaV;
    if (next != level_ || voltage_jumped ||
        sample_count_ <= 1 || (sample_count_ % kHeartbeatSamples) == 0) {
        if (valid_) {
            ESP_LOGI(TAG, "电池 %.2fV (%d%%)，adc raw=%umV，分压比 %.2f -> %s",
                     static_cast<double>(voltage_), percent_, raw_mv_,
                     static_cast<double>(DOG_BATTERY_DIVIDER), BatteryLevelText(next));
        } else {
            // 无效读数也必须周期性打出来：一是用来标定分压比（拿这条的 raw 和万用表读数算），
            // 二是电池测试时能看出电压趋势。完全不打日志就没法排查了。
            ESP_LOGW(TAG, "电池读数 %.2fV 不在合理区间 [%.1f, %.1f]，按未接分压处理；adc raw=%umV",
                     static_cast<double>(voltage), static_cast<double>(DOG_BATTERY_VALID_MIN_V),
                     static_cast<double>(DOG_BATTERY_VALID_MAX_V), raw_mv_);
        }
        SetLevel(next);
    }
}

void BatteryMonitor::SetLevel(Level level) {
    if (level == level_) return;
    level_ = level;
    if (on_level_changed_) {
        on_level_changed_(level_, voltage_, percent_);
    }
}

void BatteryMonitor::SetOnLevelChanged(LevelChangedHandler handler) {
    on_level_changed_ = std::move(handler);
}

}  // namespace aidog
