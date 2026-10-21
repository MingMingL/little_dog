#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/display.h"
#include "display/oled_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "led/led.h"
#include "ai_dog_control.h"
#include "ai_dog_battery.h"
#include "ai_dog_diag.h"
#include "ai_dog_mcp_tool.h"
#include "ai_dog_motor.h"

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
#include "ai_dog_summon.h"
#include "ai_dog_doa.h"
#include "ai_dog_mic_array.h"
#ifdef CONFIG_AI_DOG_VISION_ENABLE
#include "ai_dog_vision.h"
#endif
#endif

#include <esp_log.h>
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_lcd_panel_sh1106.h>
#include <esp_sleep.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <utility>

#define TAG "AiDogS3Board"

/**
 * AI Dog V1 板级实现
 *
 * - 屏幕：1.3" SH1106 OLED（9 针 SPI，GPIO1/2/46/48，CS 接 GND）
 * - 无普通 LED：官方 DevKitC-1 的 GPIO48 是 WS2812（v1.1 改到 GPIO38），先不驱动
 * - BOOT 键（GPIO0）：未配网时进入配网；已配网时切换语音对话（对应 PRD FR-17）
 *
 * 后续阶段在此文件里逐步加入：模式状态机、巡线/避障、看门声检测与狗叫。
 */
class AiDogS3Board : public WifiBoard {
private:
    Button boot_button_;
    NetworkEventCallback user_network_callback_;
    AiDogMcpTool mcp_tool_;

    /** 注册语音控制工具（MCP）：让用户能通过说话切换模式 */
    void InitializeTools() {
        mcp_tool_.Initialize();
    }

    /**
     * 遥控指令 → 左右轮速（差速转向）。
     *
     * 这是"状态机 → 电机"之间唯一的转换点：ControlServer 只告诉我们"该往哪走、
     * 多快"，真正写 GPIO 的只有 MotorDriver。任何绕过状态机直接调电机的改动
     * 都会让急停和模式互斥失效，不要这么干。
     */
    void ApplyMoveCommand(const aidog::MoveCommand& command) {
        auto& motor = aidog::MotorDriver::GetInstance();
        if (command.direction == aidog::MoveDirection::Stop) {
            motor.Stop();
            return;
        }

        // 速度百分比 → PWM 占空比（0..255）
        int duty = command.speed * 255 / 100;
        if (duty < 0)   duty = 0;
        if (duty > 255) duty = 255;

        // 转向时内侧轮降到 35%：转弯半径由这个系数据定。
        // 裸底盘重心偏高（电池压在后轴），转太急容易侧倾，先给温和值，
        // 真机调稳了再往上加。
        constexpr int kTurnInnerRatio = 35;
        const int inner = duty * kTurnInnerRatio / 100;

        int left = 0;
        int right = 0;
        switch (command.direction) {
            case aidog::MoveDirection::Forward:      left = duty;  right = duty;  break;
            case aidog::MoveDirection::Backward:     left = -duty; right = -duty; break;
            case aidog::MoveDirection::Left:         left = inner; right = duty;  break;
            case aidog::MoveDirection::Right:        left = duty;  right = inner; break;
            case aidog::MoveDirection::RotateLeft:   left = -duty; right = duty;  break;  // 原地左转：两轮反向
            case aidog::MoveDirection::RotateRight:  left = duty;  right = -duty; break;
            default:                                 left = 0;     right = 0;     break;
        }
        motor.SetSpeeds(left, right);
    }

    /**
     * 初始化电机驱动，并把运动指令接到模式状态机的回调上。
     *
     * 关键安全约定：换模式、急停一律先停车。遥控/巡逻/看门谁接手都从静止开始，
     * 不会带着上一个模式的残余速度继续跑。
     */
    void InitializeMotor() {
        auto& motor = aidog::MotorDriver::GetInstance();
        motor.Initialize();
        motor.Stop();  // 上电确保是停的

        auto& control = aidog::ControlServer::GetInstance();
        control.SetMoveCommandHandler([this](const aidog::MoveCommand& command) {
            ApplyMoveCommand(command);
        });
        control.SetModeChangedHandler([](aidog::Mode mode) {
            aidog::MotorDriver::GetInstance().Stop();
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
            if (mode != aidog::Mode::Summon) {
                // 离开召唤（急停/被抢/到达切 Chat）：停 DOA 释放 CPU
                aidog::DoaEngine::GetInstance().SetActive(false);
            }
#endif
            ESP_LOGI(TAG, "模式切换 -> %s，电机已停止", aidog::ModeToString(mode));
        });
        control.SetEmergencyStopHandler([]() {
            aidog::MotorDriver::GetInstance().Stop();
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
            aidog::DoaEngine::GetInstance().SetActive(false);
#endif
            ESP_LOGW(TAG, "急停：电机已停止");
        });
    }

#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
    /**
     * 初始化 V1.5 召唤栈（SW-204）。顺序有技验：
     *   1) AiDogVision 先建 SPI2 总线（带 MISO）——OLED 后建时跳过总线初始化，
     *      否则 OLED 先建的总线没有 MISO，摄像头读不了数据（T2 共存关键）；
     *   2) 然后才是阵列/DOA/口令/状态机。
     * 失败不致命： 召唤不可用，语音/遥控/显示照常。
     */
    void InitializeSummon() {
        if (aidog::AiDogSummon::GetInstance().Initialize() != ESP_OK) {
            ESP_LOGW(TAG, "召唤栈初始化失败：召唤功能不可用，其余功能不受影响");
            return;
        }
#ifdef CONFIG_AI_DOG_VISION_ENABLE
        // 摄像头用完重初始化 OLED（SW-304 软件兜底；飞线后幂等跳过）
        aidog::AiDogVision::GetInstance().SetPostUseCallback([this]() {
            ReinitOledAfterCamera();
        });
#endif
        ESP_LOGI(TAG, "召唤栈就绪（唤醒词 + 口令 + DOA 寻声 + 视觉确认）");
    }

#ifdef CONFIG_AI_DOG_VISION_ENABLE
    /**
     * SW-304 软件兜底：OLED CS 未飞线（接 GND）时，摄像头抓帧的 SPI 字节会串到
     * OLED（同总线无片选），屏幕显示乱码。飞线方案（Kconfig
     * AI_DOG_OLED_CS_GPIO45）下两个从设各有片选，无需本函数；未飞线时摄像头用完
     * 重初始化一次屏幕。代价：召唤过程中屏幕会闪一下。
     */
    void ReinitOledAfterCamera() {
#ifdef CONFIG_AI_DOG_OLED_CS_GPIO45
        return;  // 已飞线：片选隔离，不需要兜底
#else
        if (panel_ == nullptr || display_ == nullptr) {
            return;
        }
        ESP_LOGI(TAG, "OLED CS 未飞线：摄像头用后重初始化屏幕（软件兜底）");
        esp_lcd_panel_reset(panel_);
        esp_lcd_panel_init(panel_);
        esp_lcd_panel_invert_color(panel_, false);
        esp_lcd_panel_disp_on_off(panel_, true);
        display_->UpdateStatusBar(true);
#endif
    }
#endif  // CONFIG_AI_DOG_VISION_ENABLE
#endif  // CONFIG_AI_DOG_ENABLE_SUMMON

    // SH1106 OLED（SPI）：句柄与显示对象在 InitDisplay() 里创建
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    Display* display_ = nullptr;
    bool display_init_done_ = false;

    /**
     * 初始化 1.3 寸 OLED 屏（128x64，9 针 SPI）。
     *
     * 驱动 IC 是 SH1106（丝印常写 SSD1106）——注意 0.96 寸屏才是 SSD1306，两者命令集不同。
     * ID F v6.1 内置 esp_lcd 只有 SSD1306；SH1106 由组件 tny-robotics/sh1106-esp-idf 提供
     * （main/idf_component.yml 已声明，全项目可用）。
     *
     * 与官方面包板的区别：官方用 I2C 接口（4 针），我们手上的屏是 SPI 接口（9 针），
     * 所以这里自己建 SPI 总线和 panel_io。
     *
     * 引脚见 config.h：SCLK=GPIO1、MOSI=GPIO2、DC=GPIO46、RES=GPIO48、CS 接 GND。
     */
    void InitDisplay() {
        if (display_init_done_) return;
        display_init_done_ = true;

        // 1) SPI 总线：只发不收，MOSI 单数据线
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_SPI_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_SPI_SCLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT / 8 + 16;
        // 开关召唤时摄像头已经建好同一条 SPI2 总线（带 MISO），
        // 此时 initialize 会返回 ESP_ERR_INVALID_STATE ——复用即可（T2 总线共存关键）。
        const esp_err_t bus_err = spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO);
        if (bus_err != ESP_OK && bus_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "OLED SPI 总线初始化失败，屏幕不可用");
            display_ = new NoDisplay();
            return;
        }
        if (bus_err == ESP_ERR_INVALID_STATE) {
            ESP_LOGI(TAG, "SPI2 总线已由摄像头初始化（MISO 已开），OLED 复用");
        }

        // 2) panel IO（SH1106 SPI 时钟 10MHz 足够；CS 已接 GND 故填 NC）
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = 0;
        io_config.pclk_hz = 10 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        if (esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &panel_io_) != ESP_OK) {
            ESP_LOGE(TAG, "OLED panel IO 创建失败，屏幕不可用");
            display_ = new NoDisplay();
            return;
        }

        // 3) 面板：单色 1bpp + 复位脚（PPI 使用的 height 由 LVGL 尺寸决定，驱动不要求传）
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RES_PIN;
        panel_config.bits_per_pixel = 1;

#ifdef DISPLAY_DRIVER_SH1106
        // 1.3 寸：SH1106（丝印 SSD1106）
        if (esp_lcd_new_panel_sh1106(panel_io_, &panel_config, &panel_) != ESP_OK) {
#else
        // 0.96 寸：SSD1306（需要额外传 height 给 vendor_config）
        esp_lcd_panel_ssd1306_config_t ssd1306_config = {
            .height = static_cast<uint8_t>(DISPLAY_HEIGHT),
        };
        panel_config.vendor_config = &ssd1306_config;
        if (esp_lcd_new_panel_ssd1306(panel_io_, &panel_config, &panel_) != ESP_OK) {
#endif
            ESP_LOGE(TAG, "OLED 驱动安装失败，屏幕不可用");
            display_ = new NoDisplay();
            return;
        }

        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
        if (esp_lcd_panel_init(panel_) != ESP_OK) {
            ESP_LOGE(TAG, "OLED 初始化失败，屏幕不可用");
            display_ = new NoDisplay();
            return;
        }
        ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_, false));
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_, true));

        display_ = new OledDisplay(panel_io_, panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT,
                                   DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
#ifdef DISPLAY_DRIVER_SH1106
        ESP_LOGI(TAG, "SH1106 OLED 已就绪（128x64 SPI，1.3 寸）");
#else
        ESP_LOGI(TAG, "SSD1306 OLED 已就绪（128x64 SPI，0.96 寸）");
#endif
    }

    /**
     * 控制服务只在 STA 连上路由器后启动；进入配网热点前必须停掉，
     * 否则会和热点自带的 captive portal 抢 80 端口。
     */
    void HandleNetworkEvent(NetworkEvent event) {
        auto& control = aidog::ControlServer::GetInstance();
        switch (event) {
            case NetworkEvent::Connected:
                control.Start();
                // 重连成功就清掉断网时挂的故障。以前这行写在回调包装里，
                // Connected 的处理被拆成两处，加逻辑时容易只改一边。
                control.ClearFault();
                break;
            case NetworkEvent::WifiConfigModeEnter:
                // 进配网热点前先停车：热点模式下 App 连不上，狗不该继续跑
                aidog::MotorDriver::GetInstance().Stop();
                control.Stop();
                break;
            case NetworkEvent::Disconnected:
                control.SetFault("Wi-Fi 已断开");
                // 断网时 App 已经发不出指令了，设备端必须自己停，
                // 不能带着上一个速度一直跑下去（看门狗是兜底，这里才是第一道）
                aidog::MotorDriver::GetInstance().Stop();
                break;
            default:
                break;
        }
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            // 首版半双工：按键进入/退出一轮语音对话
            app.ToggleChatState();
        });
    }

    /**
     * 电池监测：把读数送进 /api/status，按档位做保护和提示。
     *
     * 这套东西是为"电池供电时反复重新初始化"做的：以前电量掉下去没有任何反应，
     * 一路跑到 5V 轨崩掉、被 brownout 打重启；现在会在崩之前先告警、再限制动作、
     * 最后主动停机，并且每次都写进设备本地日志，插上串口就能看到当时发生了什么。
     */
    void InitializeBattery() {
        auto& battery = aidog::BatteryMonitor::GetInstance();
        auto& control = aidog::ControlServer::GetInstance();
        auto& diag = aidog::DeviceLog::GetInstance();

        battery.SetOnLevelChanged([this, &control, &diag](aidog::BatteryMonitor::Level level,
                                                          float volts, int percent) {
            control.SetBatteryPercent(percent);
            control.SetBatteryVoltage(volts);
            control.SetBatteryLevelText(aidog::BatteryLevelText(level));

            switch (level) {
                case aidog::BatteryMonitor::Level::Normal:
                    control.ClearFault();
                    break;

                case aidog::BatteryMonitor::Level::Warn:
                    ESP_LOGW(TAG, "电池电压低：%.2fV（%d%%），该充电了", static_cast<double>(volts), percent);
                    diag.Warn("电池电压低 %.2fV (%d%%)，建议充电", static_cast<double>(volts), percent);
                    control.SetFault("电池电压低");
                    Notify("电池电压低，请充电");
                    break;

                case aidog::BatteryMonitor::Level::Restrict:
                    ESP_LOGW(TAG, "电池电压 %.2fV（%d%%）：禁止进入巡逻/看门/遥控",
                             static_cast<double>(volts), percent);
                    diag.Warn("电池 %.2fV (%d%%)，已禁止巡逻/看门/遥控", static_cast<double>(volts), percent);
                    control.SetFault("电池电压低，仅待机/聊天");
                    // 已经在跑运动模式的话立刻拉回待机，避免带着速度耗电
                    control.RequestEmergencyStop();
                    Notify("电量低，已限制运动");
                    break;

                case aidog::BatteryMonitor::Level::Cutoff:
                    // 到了这里 5V 轨随时可能崩，继续跑有写坏 flash 的风险。
                    // 先停、先落盘、再睡，日志才能在下次启动时读出来。
                    ESP_LOGE(TAG, "电池电压 %.2fV（%d%%）低于截止值 %.2fV，停机",
                             static_cast<double>(volts), percent,
                             static_cast<double>(DOG_BATTERY_CUTOFF_V));
                    diag.Error("电池 %.2fV 低于截止电压 %.2fV，主动停机（避免掉压复位）",
                               static_cast<double>(volts), static_cast<double>(DOG_BATTERY_CUTOFF_V));
                    aidog::MotorDriver::GetInstance().Stop();
                    control.SetFault("电池电压过低，已停机");
                    diag.Flush();  // 关键：落盘后再睡，否则白记
                    Notify("电量耗尽，停机", 6000);
                    vTaskDelay(pdMS_TO_TICKS(6000));
                    diag.Flush();
                    esp_deep_sleep_start();
                    break;

                default:
                    // Unknown：回调第一行的 SetBatteryLevelText(BatteryLevelText(level))
                    // 对它就是 "unknown"，再写一遍是重复赋值；保留 default 只为满足
                    // -Werror=switch（枚举漏分支会直接编译失败）。
                    break;
            }
        });

        // 电量到 Restrict/Cutoff 时，禁止再切进会动电机的模式。
        // 急停和这里的自停走另一条路，不受闸门影响，别改成走闸门。
        control.SetModeGuardHandler([](aidog::Mode mode) {
            auto level = aidog::BatteryMonitor::GetInstance().level();
            if (level != aidog::BatteryMonitor::Level::Restrict &&
                level != aidog::BatteryMonitor::Level::Cutoff) {
                return true;
            }
            if (mode == aidog::Mode::Patrol || mode == aidog::Mode::Guard ||
                mode == aidog::Mode::Remote) {
                ESP_LOGW(TAG, "电量受限，拒绝切到 %s", aidog::ModeToString(mode));
                return false;
            }
            return true;
        });

        battery.Init();
    }

    /** OLED 提示；屏幕还没被 Application 取走（display_ 为空）时就跳过 */
    void Notify(const char* text, int duration_ms = 4000) {
        if (display_ != nullptr) {
            display_->ShowNotification(text, duration_ms);
        }
    }

public:
    AiDogS3Board() : boot_button_(BOOT_BUTTON_GPIO) {
        // 顺序有讲究：DeviceLog 最先起，后面所有人的 ESP_LOG 才进得了本地日志；
        // 电池监测最后起，起的时候控制服务和电机都已经就位。
        aidog::DeviceLog::GetInstance().Init();
        InitializeButtons();
        InitializeTools();
        InitializeMotor();
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
        // 召唤栈（含摄像头 SPI 总线）必须在 GetDisplay() 前起来
        InitializeSummon();
#endif
        InitializeBattery();
        ESP_LOGI(TAG, "AI Dog S3 board ready (voice: xiaozhi-esp32, OLED + LAN control + 语音模式切换 + 遥控 + 电池监测)");
    }

    virtual Led* GetLed() override {
        static NoLed led;
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
#ifdef CONFIG_AI_DOG_ENABLE_SUMMON
        // Q-03 决策 A：4 麦阵列拥有麦克风 I²S 口，AFE 从阵列通道 0 取数。
        // 阵列没起来（硬件未接/后端申请失败）时退回 V1 的单麦 codec。
        if (aidog::MicArray::GetInstance().IsEnabled()) {
            static AudioCodec* summon_codec = static_cast<AudioCodec*>(
                aidog::MicArray::GetInstance().CreateAudioCodec(
                    AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
                    AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT));
            return summon_codec;
        }
        ESP_LOGW(TAG, "4 麦阵列未启用：音频链路退回 V1 单麦 codec");
#endif
#ifdef AUDIO_I2S_METHOD_SIMPLEX
        static NoAudioCodecSimplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT,
            AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
#else
        static NoAudioCodecDuplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS, AUDIO_I2S_GPIO_DOUT, AUDIO_I2S_GPIO_DIN);
#endif
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        // 延迟初始化：拿到屏句柄时才真正建 SPI 总线，省内存也便于回退
        InitDisplay();
        return display_;
    }

    /**
     * 包装网络事件回调：先处理控制服务的启停，再转发给上层（Application）。
     */
    virtual void SetNetworkEventCallback(NetworkEventCallback callback) override {
        user_network_callback_ = std::move(callback);
        WifiBoard::SetNetworkEventCallback([this](NetworkEvent event, const std::string& data) {
            HandleNetworkEvent(event);
            if (user_network_callback_) {
                user_network_callback_(event, data);
            }
        });
    }
};

DECLARE_BOARD(AiDogS3Board);
