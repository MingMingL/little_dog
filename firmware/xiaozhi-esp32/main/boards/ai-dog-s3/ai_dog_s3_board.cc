#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/display.h"
#include "display/oled_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "led/led.h"
#include "ai_dog_control.h"

#include <esp_log.h>
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_lcd_panel_sh1106.h>
#include <utility>

#define TAG "AiDogS3Board"

/**
 * AI Dog V1 板级实现
 *
 * - 屏幕：0.96" SSD1306 OLED（7 针 SPI，GPIO1/2/46/48，CS 接 GND）
 * - 无普通 LED：官方 DevKitC-1 的 GPIO48 是 WS2812（v1.1 改到 GPIO38），先不驱动
 * - BOOT 键（GPIO0）：未配网时进入配网；已配网时切换语音对话（对应 PRD FR-17）
 *
 * 后续阶段在此文件里逐步加入：模式状态机、巡线/避障、看门声检测与狗叫。
 */
class AiDogS3Board : public WifiBoard {
private:
    Button boot_button_;
    NetworkEventCallback user_network_callback_;

    // SSD1306 OLED（SPI）：句柄与显示对象在 InitDisplay() 里创建
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    Display* display_ = nullptr;
    bool display_init_done_ = false;

    /**
     * 初始化 1.3 寸 OLED 屏（128x64，7 针 SPI）。
     *
     * 驱动 IC 是 SH1106（丝印常写 SSD1106）——注意 0.96 寸屏才是 SSD1306，两者命令集不同。
     * ID F v6.1 内置 esp_lcd 只有 SSD1306；SH1106 由组件 tny-robotics/sh1106-esp-idf 提供
     * （main/idf_component.yml 已声明，全项目可用）。
     *
     * 与官方面包板的区别：官方用 I2C 接口（4 针），我们手上的屏是 SPI 接口（7 针），
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
                break;
            case NetworkEvent::WifiConfigModeEnter:
                control.Stop();
                break;
            case NetworkEvent::Disconnected:
                control.SetFault("Wi-Fi 已断开");
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

public:
    AiDogS3Board() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeButtons();
        ESP_LOGI(TAG, "AI Dog S3 board ready (voice: xiaozhi-esp32, no display)");
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
            if (event == NetworkEvent::Connected) {
                aidog::ControlServer::GetInstance().ClearFault();
            }
            if (user_network_callback_) {
                user_network_callback_(event, data);
            }
        });
    }
};

DECLARE_BOARD(AiDogS3Board);
