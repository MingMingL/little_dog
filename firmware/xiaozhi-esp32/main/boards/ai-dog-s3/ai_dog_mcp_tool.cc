#include "ai_dog_mcp_tool.h"

#include <application.h>

#include <esp_log.h>
#include <string>

#define TAG "AiDogMcpTool"

void AiDogMcpTool::Initialize() {
    auto& mcp_server = McpServer::GetInstance();

    mcp_server.AddTool(
        "self.dog.set_mode",
        "Set the robot dog's working mode (切换机器狗的工作模式).\n"
        "The mode must be one of: `idle` (待机，电机停止), `chat` (语音聊天), "
        "`patrol` (巡逻，沿固定路线行驶), `guard` (看门，设防监听门口声音).\n"
        "IMPORTANT: `patrol` and `guard` make the robot MOVE or watch, so before calling "
        "this tool with those two modes you MUST first repeat the request back to the user "
        "and get an explicit confirmation (例如：“要开始巡逻吗？”). Only call after the user "
        "says yes. `idle` and `chat` are safe and can be applied immediately.",
        PropertyList({
            Property("mode", kPropertyTypeString)
        }),
        [](const PropertyList& properties) -> ToolResult {
            auto mode_text = properties["mode"].value<std::string>();

            aidog::Mode mode;
            if (!ModeFromString(mode_text, mode)) {
                return std::unexpected("Invalid mode: " + mode_text +
                                       " (expected idle|chat|patrol|guard)");
            }

            auto& control = aidog::ControlServer::GetInstance();
            control.SetMode(mode);

            // 给用户明确的语音/屏幕反馈（Alert 会走 TTS 播报 + OLED 显示）
            const char* message = nullptr;
            switch (mode) {
                case aidog::Mode::Idle:   message = "已回到待机";   break;
                case aidog::Mode::Chat:   message = "进入聊天模式"; break;
                case aidog::Mode::Patrol: message = "开始巡逻";     break;
                case aidog::Mode::Guard:  message = "已设防看门";   break;
                default:                  message = "模式已切换";   break;
            }
            Application::GetInstance().Alert("模式", message, "gear");

            ESP_LOGI(TAG, "语音切换模式 -> %s", ModeToString(mode));
            return std::string(message);
        });

    ESP_LOGI(TAG, "语音模式切换工具已注册：self.dog.set_mode");
}
