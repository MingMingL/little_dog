#include "ai_dog_motor.h"

#include <esp_log.h>
#include <driver/gpio.h>
#include <driver/ledc.h>

#include "config.h"

#include <algorithm>

#define TAG "AiDogMotor"

namespace aidog {

// PWM 配置：20kHz 超出人耳听感范围，避免电机发出啸叫；8bit 分辨率对应 0..255
static constexpr int kPwmFreqHz = 20000;
static constexpr ledc_timer_bit_t kPwmResolution = LEDC_TIMER_8_BIT;
static constexpr int kMaxDuty = 255;

// 两个轮子各占一个 LEDC 通道，共用一个定时器（频率必须一致，共用定时器天然保证）
static constexpr ledc_timer_t kPwmTimer = LEDC_TIMER_0;
static constexpr ledc_channel_t kLeftChannel = LEDC_CHANNEL_0;
static constexpr ledc_channel_t kRightChannel = LEDC_CHANNEL_1;

MotorDriver& MotorDriver::GetInstance() {
    static MotorDriver instance;
    return instance;
}

void MotorDriver::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (initialized_) {
        return;
    }
    initialized_ = true;

    // 1) STANDBY：TB6612 的 STBY 脚低电平时整片关断，先拉高使能
    gpio_config_t stby = {};
    stby.pin_bit_mask = (1ULL << MOTOR_STANDBY_GPIO);
    stby.mode = GPIO_MODE_OUTPUT;
    stby.pull_up_en = GPIO_PULLUP_DISABLE;
    stby.pull_down_en = GPIO_PULLDOWN_DISABLE;
    stby.intr_type = GPIO_INTR_DISABLE;
    if (gpio_config(&stby) != ESP_OK) {
        ESP_LOGE(TAG, "STANDBY 引脚配置失败，电机不可用");
        return;
    }
    gpio_set_level(MOTOR_STANDBY_GPIO, 1);

    // 2) 方向线 IN1/IN2：推挽输出，初始全 0（停止）
    const gpio_num_t dir_pins[] = {
        MOTOR_LEFT_IN1_GPIO, MOTOR_LEFT_IN2_GPIO,
        MOTOR_RIGHT_IN1_GPIO, MOTOR_RIGHT_IN2_GPIO,
    };
    gpio_config_t dir = {};
    dir.mode = GPIO_MODE_OUTPUT;
    dir.pull_up_en = GPIO_PULLUP_DISABLE;
    dir.pull_down_en = GPIO_PULLDOWN_DISABLE;
    dir.intr_type = GPIO_INTR_DISABLE;
    for (gpio_num_t pin : dir_pins) {
        dir.pin_bit_mask = (1ULL << pin);
        if (gpio_config(&dir) != ESP_OK) {
            ESP_LOGE(TAG, "方向引脚 GPIO%d 配置失败，电机不可用", pin);
            return;
        }
        gpio_set_level(pin, 0);
    }

    // 3) PWM 定时器
    ledc_timer_config_t timer = {};
    timer.speed_mode = LEDC_LOW_SPEED_MODE;
    timer.duty_resolution = kPwmResolution;
    timer.timer_num = kPwmTimer;
    timer.freq_hz = kPwmFreqHz;
    timer.clk_cfg = LEDC_AUTO_CLK;
    if (ledc_timer_config(&timer) != ESP_OK) {
        ESP_LOGE(TAG, "PWM 定时器配置失败，电机不可用");
        gpio_set_level(MOTOR_STANDBY_GPIO, 0);
        return;
    }

    // 4) 两个 PWM 通道（同一定时器保证频率一致）
    // 注意：不给 intr_type 赋值。ESP-IDF v6.1 起该字段已弃用，写它会触发
    // -Wdeprecated-declarations 警告，而在 v5.x 上不写它就是默认禁用中断，
    // 行为完全一致。
    ledc_channel_config_t left_ch = {};
    left_ch.speed_mode = LEDC_LOW_SPEED_MODE;
    left_ch.channel = kLeftChannel;
    left_ch.timer_sel = kPwmTimer;
    left_ch.gpio_num = MOTOR_LEFT_PWM_GPIO;
    left_ch.duty = 0;
    left_ch.hpoint = 0;

    ledc_channel_config_t right_ch = left_ch;
    right_ch.channel = kRightChannel;
    right_ch.gpio_num = MOTOR_RIGHT_PWM_GPIO;

    if (ledc_channel_config(&left_ch) != ESP_OK || ledc_channel_config(&right_ch) != ESP_OK) {
        ESP_LOGE(TAG, "PWM 通道配置失败，电机不可用");
        gpio_set_level(MOTOR_STANDBY_GPIO, 0);
        return;
    }

    enabled_ = true;
    ESP_LOGI(TAG, "TB6612 电机驱动就绪：PWM %dHz，左 GPIO%d / 右 GPIO%d",
             kPwmFreqHz, MOTOR_LEFT_PWM_GPIO, MOTOR_RIGHT_PWM_GPIO);
}

void MotorDriver::ApplyOne(bool is_left, int speed) {
    const gpio_num_t in1 = is_left ? MOTOR_LEFT_IN1_GPIO : MOTOR_RIGHT_IN1_GPIO;
    const gpio_num_t in2 = is_left ? MOTOR_LEFT_IN2_GPIO : MOTOR_RIGHT_IN2_GPIO;
    const ledc_channel_t channel = is_left ? kLeftChannel : kRightChannel;

    // 限幅：调用方传超范围的值不该把 PWM 寄存器写坏
    speed = std::clamp(speed, -kMaxDuty, kMaxDuty);

    // 方向线：正转 IN1=1/IN2=0，反转 IN1=0/IN2=1，停止 00（惰行）
    // 注意先设方向再给 PWM，避免换向前瞬间出现短刹车电流
    if (speed > 0) {
        gpio_set_level(in1, 1);
        gpio_set_level(in2, 0);
    } else if (speed < 0) {
        gpio_set_level(in1, 0);
        gpio_set_level(in2, 1);
    } else {
        gpio_set_level(in1, 0);
        gpio_set_level(in2, 0);
    }

    ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, static_cast<uint32_t>(std::abs(speed)));
    ledc_update_duty(LEDC_LOW_SPEED_MODE, channel);
}

void MotorDriver::SetSpeeds(int left, int right) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_) {
        return;  // 电机没接好也不能让上层崩，静默丢弃
    }
    left = std::clamp(left, -kMaxDuty, kMaxDuty);
    right = std::clamp(right, -kMaxDuty, kMaxDuty);

    // 状态没变就跳过写寄存器——遥控指令 10Hz 重复发同一方向时省掉大量无谓的 GPIO 操作
    if (left == last_left_ && right == last_right_) {
        return;
    }

    ApplyOne(true, left);
    ApplyOne(false, right);
    last_left_ = left;
    last_right_ = right;
}

void MotorDriver::Stop() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled_) {
        return;
    }
    // last_* 也要清零，否则下一次 SetSpeeds 传 0 会因为"状态没变"被跳过，停不下来
    last_left_ = 0;
    last_right_ = 0;
    ApplyOne(true, 0);
    ApplyOne(false, 0);
}

}  // namespace aidog
