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
            ESP_LOGI(TAG, "模式切换 -> %s，电机已停止", aidog::ModeToString(mode));
        });
        control.SetEmergencyStopHandler([]() {
            aidog::MotorDriver::GetInstance().Stop();
            ESP_LOGW(TAG, "急停：电机已停止");
        });
    }

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
        if (spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO) != ESP_OK) {
            ESP_LOGE(TAG, "OLED SPI 总线初始化失败，屏幕不可用");
            display_ = new NoDisplay();
            return;
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

                case aidog::BatteryMonitor::Level::Unknown:
                default:
                    control.SetBatteryLevelText("unknown");
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
        InitializeBattery();
        ESP_LOGI(TAG, "AI Dog S3 board ready (voice: xiaozhi-esp32, OLED + LAN control + 语音模式切换 + 遥控 + 电池监测)");
    }

    virtual Led* GetLed() override {
        static NoLed led;
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
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
