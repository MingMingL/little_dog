#ifndef AI_DOG_MCP_TOOL_H
#define AI_DOG_MCP_TOOL_H

#include "ai_dog_control.h"
#include "mcp_server.h"

/**
 * 语音控制模式切换（MCP 工具）。
 *
 * 原理：设备端把工具注册到 MCP 服务，云端 LLM 收到用户语音后
 * （例如“开始巡逻”）会调用本工具，由设备真正切换模式。
 * 这样语音链路复用 xiaozhi 既有的 ASR→LLM→TTS 通道，不额外造轮子。
 *
 * 安全设计（产品需求确定）：
 * - 涉及运动的 patrol / guard 两个模式，**必须由 LLM 先向用户复述确认、
 *   得到明确同意后才允许调用本工具**（见工具描述，LLM 会遵守）；
 * - idle / chat 无机械动作，可直接切换；
 * - 与 App/HTTP 指令的优先级：**最后到达者优先**（急停除外，急停只走 App）。
 */
class AiDogMcpTool {
public:
    // 不写构造/析构：本类只有 Initialize() 一个动作、没有成员要管，
    // 编译器生成的就够用，显式写 = default 只是噪音。

    /** 注册 MCP 工具（构造函数之后、Application 注册内置工具之前调用） */
    void Initialize();
};

#endif  // AI_DOG_MCP_TOOL_H
