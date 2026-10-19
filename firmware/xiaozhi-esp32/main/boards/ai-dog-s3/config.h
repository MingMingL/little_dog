#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>

/* AI Dog V1 —— 乐鑫原装 ESP32-S3-DevKitC-1（N16R8）+ INMP441 麦克风 + MAX98357A 功放 + SH1106 OLED
 *
 * 音频引脚沿用 xiaozhi 官方「面包板精简 WiFi」方案（bread-compact-wifi），
 * 这样可以直接照官方面包板接线图连线，减少自己踩坑。
 */

#define AUDIO_INPUT_SAMPLE_RATE  16000
#define AUDIO_OUTPUT_SAMPLE_RATE 24000

// INMP441 与 MAX98357A 使用两路独立 I2S（Simplex）。若改成单路全双工，注释下一行
#define AUDIO_I2S_METHOD_SIMPLEX

#ifdef AUDIO_I2S_METHOD_SIMPLEX

#define AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_4
#define AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_5
#define AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_6
#define AUDIO_I2S_SPK_GPIO_DOUT GPIO_NUM_7
#define AUDIO_I2S_SPK_GPIO_BCLK GPIO_NUM_15
#define AUDIO_I2S_SPK_GPIO_LRCK GPIO_NUM_16

#else

#define AUDIO_I2S_GPIO_WS   GPIO_NUM_4
#define AUDIO_I2S_GPIO_BCLK GPIO_NUM_5
#define AUDIO_I2S_GPIO_DIN  GPIO_NUM_6
#define AUDIO_I2S_GPIO_DOUT GPIO_NUM_7

#endif

// BOOT 键（GPIO0）：未配网时进入配网，已配网时切换语音对话（对应 FR-17）
#define BOOT_BUTTON_GPIO GPIO_NUM_0

// 传感器 I2C（VL53L0X；屏幕走 SPI，I2C 独占给传感器）
// VL53L0X 支架前倾 30° 安装：测桌面/地板边缘做防跌落（触发距离 = 安装高度 / tan30°），
// 兼顾探头前方近距离防撞；代价是看不到 300mm 以外的前向障碍（2026-10-19 决策，B 方案）
#define DOG_I2C_SDA_GPIO GPIO_NUM_41
#define DOG_I2C_SCL_GPIO GPIO_NUM_42

// ===== 电池电压监测（GPIO8 / ADC1_CH7）=====
// 硬件接法：电池正 → R_upper → (GPIO8) → R_lower → GND，ADC 量的是 R_lower 上的分压。
// 分压比 = (R_upper + R_lower) / R_lower。例：100k 上 / 47k 下 → 3.13。
//
// 这个值必须和实际电阻对上，否则算出来的电压是错的。标定方法：
//   ① 万用表量一节电池（2S 整组）的真实电压 Vbat；
//   ② 看启动日志里 ai_dog_battery 打印的 "adc raw=xxx mV"（Vadc）；
//   ③ 分压比 = Vbat / Vadc，填回下面的 DOG_BATTERY_DIVIDER。
#define DOG_BATTERY_ADC_GPIO       GPIO_NUM_8
#define DOG_BATTERY_DIVIDER        3.0f

// 量程合理性门限。读出来不在这个区间，一律认为"分压没接好/接错了"，
// 直接放弃低电压保护（battery 上报 -1），避免ADC悬空读数乱跳把狗误关机。
#define DOG_BATTERY_VALID_MIN_V    5.5f
#define DOG_BATTERY_VALID_MAX_V    9.6f

// 分档门限（2S 锂电：满电 8.4V，标称 7.4V）。
// MP1584 要稳定输出 5V，输入至少得 ~6.5V —— 这就是"用到后面就循环重启"的根子，
// 所以 RESTRICT/CUTOFF 都压在 6.5V 以下，赶在 5V 轨崩之前动作。
#define DOG_BATTERY_WARN_V         6.9f   // 告警：日志 + OLED 提示
#define DOG_BATTERY_RESTRICT_V     6.6f   // 限制：拒绝进入巡逻/看门/遥控
#define DOG_BATTERY_CUTOFF_V       6.3f   // 截止：停电机 + 提示 + 深度睡眠

/* ===== 以下为后续阶段预留，首版固件尚未初始化这些外设 ===== */

// TB6612FNG 电机驱动（左/右各 PWM + 方向两线）
#define MOTOR_LEFT_PWM_GPIO   GPIO_NUM_11
#define MOTOR_LEFT_IN1_GPIO   GPIO_NUM_12
#define MOTOR_LEFT_IN2_GPIO   GPIO_NUM_13
#define MOTOR_RIGHT_PWM_GPIO  GPIO_NUM_14
#define MOTOR_RIGHT_IN1_GPIO  GPIO_NUM_21
#define MOTOR_RIGHT_IN2_GPIO  GPIO_NUM_47
#define MOTOR_STANDBY_GPIO    GPIO_NUM_10

// 模式切换按键（待机/聊天/巡逻/看门）
#define MODE_BUTTON_GPIO GPIO_NUM_9

/* ===== OLED 屏幕（1.3 寸，128x64，9 针 SPI 版本）=====
 *
 * 这块屏的驱动 IC 是 SH1106（模块丝印常写作 SSD1106），不要和 0.96 寸的 SSD1306 混：
 * 两者初始化命令不同，用错表现为"屏幕亮但无内容/花屏"。
 *   - 1.3 寸 → SH1106（本配置）
 *   - 0.96 寸 → SSD1306（把下面 DISPLAY_DRIVER_SH1106 注释掉即可）
 *
 * 可用 GPIO 已很紧（GPIO1/2/46/48 是仅剩的安全脚：3/45 是 strapping、
 * 43/44 留给串口日志、19/20 是 USB、26-37 被模组内部 SPI flash 占用），
 * 因此 CS 片选脚直接接 GND —— SPI 总线上只有这一个从设备，不需要片选。
 * 这样 4 个 GPIO + 3.3V + GND 就能驱动整块屏。
 */
#define DISPLAY_DRIVER_SH1106

#define DISPLAY_SPI_SCLK_PIN GPIO_NUM_1   // 9针屏的 CLK/SCL/D0
#define DISPLAY_SPI_MOSI_PIN GPIO_NUM_2   // 9针屏的 DIN/MOSI/D1
#define DISPLAY_DC_PIN       GPIO_NUM_46  // 数据/命令选择（DC）
#define DISPLAY_RES_PIN      GPIO_NUM_48  // 复位（RES/RST）
#define DISPLAY_CS_PIN       GPIO_NUM_NC  // CS 接 GND，不占用 GPIO
#define DISPLAY_WIDTH        128
#define DISPLAY_HEIGHT       64
#define DISPLAY_MIRROR_X     true
#define DISPLAY_MIRROR_Y     true

#endif // _BOARD_CONFIG_H_
