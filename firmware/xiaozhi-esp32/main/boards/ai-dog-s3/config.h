#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>
#include <sdkconfig.h>

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

/* ===== 运动执行机构：已在 AiDogS3Board::InitializeMotor() 里初始化 ===== */

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
 * 因此 CS 片选脚默认直接接 GND —— SPI 总线上只有 OLED 一个从设备，不需要片选。
 * 这样 4 个 GPIO + 3.3V + GND 就能驱动整块屏。
 *
 * V1.5 召唤功能要挂 ArduCAM 到同一条 SPI 总线（最终实施方案 §3.1 第 3 条）：
 * OLED 模块 CS 常接地时两个从设备会同时响应，必须把 OLED CS 飞线到 GPIO45
 * （strapping 脚，上电保持高电平即可正常启动）。Kconfig
 * AI_DOG_OLED_CS_GPIO45 打开后 DISPLAY_CS_PIN 改为 GPIO45；不飞线的现场
 * 用软件兜底（摄像头用完后重新初始化 OLED，见 ai_dog_s3_board.cc）。
 */
#define DISPLAY_DRIVER_SH1106

#define DISPLAY_SPI_SCLK_PIN GPIO_NUM_1   // 9针屏的 CLK/SCL/D0
#define DISPLAY_SPI_MOSI_PIN GPIO_NUM_2   // 9针屏的 DIN/MOSI/D1
#define DISPLAY_DC_PIN       GPIO_NUM_46  // 数据/命令选择（DC）
#define DISPLAY_RES_PIN      GPIO_NUM_48  // 复位（RES/RST）
#ifdef CONFIG_AI_DOG_OLED_CS_GPIO45
#define DISPLAY_CS_PIN       GPIO_NUM_45  // CS 飞线到 GPIO45（与摄像头共总线）
#else
#define DISPLAY_CS_PIN       GPIO_NUM_NC  // CS 接 GND，不占用 GPIO
#endif
#define DISPLAY_WIDTH        128
#define DISPLAY_HEIGHT       64
#define DISPLAY_MIRROR_X     true
#define DISPLAY_MIRROR_Y     true

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
/* ===== V1.5 召唤功能新增外设（main/Kconfig.projbuild → AI Dog Summon）=====
 * 引脚口径见 output/召唤狗狗最终实施方案-v1.0.md §3.2；未到货期间只编译不初始化。
 */

/* 4 麦方阵：MIC1 沿用 GPIO6；MIC2/3/4 用 PCF8574 释放的 GPIO38/39/40。
 * 采集后端 (a)/(c) 单数据线 TDM 时仅 GPIO6 作 DIN；后端 (b) 双立体声线时
 * GPIO6（MIC1/MIC2 L/R）+ GPIO38（MIC3/MIC4 L/R）各作一条数据线。 */
#define MIC_ARRAY_SD2_GPIO GPIO_NUM_38
#define MIC_ARRAY_SD3_GPIO GPIO_NUM_39
#define MIC_ARRAY_SD4_GPIO GPIO_NUM_40

/* 巡线 5 路改接 PCF8574（TCRT5000 数字输出，开漏兼容准双向口）：
 * 挂现有 I2C（GPIO41/42），与 VL53L0X 0x29 并联。 */
// 轮询周期不在这里定：巡线是调用方按需 Read()（见 ai_dog_line_pcf8574.cc），
// 模块内部没有自己的定时器，写个用不上的 POLL_MS 只会让人以为改它就能改频率。
#define LINE_SENSOR_PCF8574_ADDR  0x20

/* VL53L0X 前向测程（避障 + 接近段触发） */
#define VL53L0X_I2C_ADDR 0x29

/* ArduCAM Mini 2MP Plus（OV2640）：与 OLED 共 SPI 总线，
 * SCK/MOSI 共享 GPIO1/2，MISO=GPIO17、CS=GPIO18（PCF8574 释放）。
 * PWDN 接 GND、RESET 接 3.3V（静态，不占 GPIO）。 */
#define CAMERA_SPI_SCLK_PIN DISPLAY_SPI_SCLK_PIN
#define CAMERA_SPI_MOSI_PIN DISPLAY_SPI_MOSI_PIN
#define CAMERA_SPI_MISO_PIN GPIO_NUM_17
#define CAMERA_SPI_CS_PIN   GPIO_NUM_18
#define CAMERA_SPI_CLOCK_HZ 8 * 1000 * 1000

#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

#endif // _BOARD_CONFIG_H_
