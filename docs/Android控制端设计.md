# 机器狗 V1 —— Android 控制端设计

> 状态：**Android 工程已生成并编译通过**（`app-debug.apk` 6.5 MB）；**设备端控制服务已实现并编译通过**；**OLED 屏幕已接入并编译通过**（v4 固件）。
> 设备现为"有屏"状态：Wi-Fi/配网/对话内容会显示在 0.96" SSD1306 上（SPI 接口，GPIO1/2/46/48，CS 接地）。
> 尚未烧录到真实硬件验证（主控仍在途中），联调待硬件到货。
> 构建注意：AGP 不支持 JDK 24，本机已另装 JDK 21 于 `F:\software\jdk21\jdk-21.0.12.1+1`，详见 `android/README.md`。

## 1. 总体方案

| 项 | 选择 |
|---|---|
| 通信方式 | **局域网直连**（设备起轻量 HTTP + WebSocket 服务，App 直连 IP） |
| UI 技术栈 | Java + XML + ViewBinding（用户主用 Java，不用 Kotlin/Compose） |
| V1 功能 | 状态面板、模式切换、急停、配网入口 |
| 工程位置 | `android/`（Android Studio 打开即构建） |
| 编译配置 | compileSdk 36 / minSdk 26 / targetSdk 36，AGP 8.7.3 + Gradle 8.13 |

选局域网直连的理由：延迟低、不依赖公网与云端、断网也能控；代价是设备端要新增一个本地控制服务（约 200 行 C++，用 ESP-IDF 自带 `esp_http_server`）。

## 2. 通信协议（设备端需实现）

设备在同一局域网内监听 **80 端口**。所有响应均为 `application/json`。

### 2.1 查询状态

```
GET /api/status
```

```json
{
  "mode": "idle",          // idle | chat | patrol | guard
  "moving": false,
  "battery": 85,           // 百分比；-1 表示无电池采样
  "charging": false,
  "wifi": { "ssid": "MyHome", "rssi": -55 },
  "fault": null,           // 字符串描述，null 表示无故障
  "uptime": 12345          // 秒
}
```

### 2.2 切换模式

```
POST /api/mode
{"mode": "patrol"}
```
响应：`{"ok": true, "mode": "patrol"}`；失败：`{"ok": false, "error": "..."}`。

### 2.3 急停

```
POST /api/stop
```
响应：`{"ok": true}`。设备收到后**立即停电机并退出当前模式进入 idle**，该指令优先级高于一切。

### 2.4 配网

```
POST /api/wifi
{"ssid": "MyHome", "password": "xxxx"}
```
响应：`{"ok": true, "ip": "192.168.1.50"}`。设备保存凭据并尝试连接，成功后回传新 IP。

### 2.5 语音控制模式切换（设备端 MCP 工具，2026-10-14 新增）

设备端注册了一个 MCP 工具，**语音指令复用 xiaozhi 既有的 ASR→LLM→TTS 通道**，不需要新增链路：

| 项 | 内容 |
|---|---|
| 工具名 | `self.dog.set_mode` |
| 参数 | `mode`（字符串，`idle` / `chat` / `patrol` / `guard`） |
| 生效路径 | 用户说话 → 云端 ASR/LLM 判定意图 → 下发 `tools/call` → 设备 `ControlServer::SetMode()` |
| 反馈 | 切换后设备播放语音提示（"开始巡逻" 等）+ OLED 显示 |

**安全规则（产品决策）：**
- `patrol` / `guard` 涉及机械动作，工具描述中要求 **LLM 必须先向用户复述确认、得到明确同意后才调用**；用户说"确认"后才真正执行。
- `idle` / `chat` 无机械动作，可直接切换。
- **优先级：最后到达者优先**——App 与语音同时下达，以最后一次为准（急停例外：急停只从 App 下发，任何模式立即停车）。

**口令示例：**"开始巡逻" → 设备反问"要开始巡逻吗？" → "确认" → 开始巡逻。

### 2.6 状态推送（可选，App 已实现自动降级）

```
WS ws://<ip>/ws
```
服务端每 1 秒推送一次与 `/api/status` 相同的 JSON。
**App 逻辑**：先尝试 WebSocket；连不上则自动降级为每 2 秒轮询 `GET /api/status`。因此设备端可以先只实现 2.1–2.4，App 也能正常工作。

## 3. App 结构

```
android/
└── app/src/main/java/com/aidog/control/
    ├── MainActivity.java       # 状态面板 + 模式切换 + 急停
    ├── SetupActivity.java      # 设备 IP 设置 + 配网入口
    ├── api/DeviceApi.java      # OkHttp：HTTP 请求 + WebSocket + 自动降级轮询
    └── model/DeviceStatus.java # 状态数据模型（Gson 解析）
```

要点：

- 设备 IP 存 `SharedPreferences`，首次启动自动跳转设置页。
- 状态展示：在线/离线、当前模式、电量、Wi-Fi、故障原因。
- 模式切换四个按钮；**急停按钮常驻且最高优先级**（红色，独立请求）。
- 配网页填写 SSID/密码后下发，成功后自动更新保存的 IP。
- 所有网络请求走子线程，回调切回主线程更新 UI。

## 4. 设备端实现（已完成，待真机验证）

文件：`firmware/xiaozhi-esp32/main/boards/ai-dog-s3/ai_dog_control.{h,cc}`

- `esp_http_server` 监听 80 端口，注册 4 个 URI；JSON 用 cJSON 编解码。
- `ControlServer` 单例只做"收指令 + 报状态"，**不直接驱动电机**；
  运动模块后续通过 `SetModeChangedHandler` / `SetEmergencyStopHandler` 接入，保证指令永远经过模式状态机。
- 启停时机（在 `ai_dog_s3_board.cc` 里包装网络事件回调）：
  - STA 连上路由器 → `Start()`；
  - 进入配网热点 → `Stop()`（热点自带 captive portal 占 80 端口，必须让开）；
  - 断开 → 上报故障。
- 配网：`SsidManager::AddSsid()` 写入 NVS 后 `WifiManager::StartStation()` 重连，失败会自动回退到配网模式，不会失联。
- 依赖已加进 `main/CMakeLists.txt`（`esp_http_server`、`esp_wifi`、`espressif__cjson`）。
- 电池采样（GPIO8 ADC）尚未接入，`battery` 暂返回 -1（App 显示"无采样"）。

### 真机验证清单（硬件到货后）

1. 用 `esp_http_server` 注册上述 4 个 URI；`POST` 需读取 body（注意 `httpd_req_recv` 循环读完）。
2. 控制指令要**经过模式状态机**，不能直接改电机输出；急停走最高优先级分支（对应 PRD FR-09）。
3. 状态里的 `battery` 来自 GPIO8 的 ADC 采样（需先标定分压比）。
4. 配网写 NVS 后调用 `esp_wifi` 重连，失败要回退到原配置，避免"配错网就失联"。
5. 该服务只在局域网监听，**不要做端口转发暴露到公网**。

## 5. 待办 / 风险

- 设备 IP 会随 DHCP 变化 → 后续可加 mDNS（`ai-dog.local`）或在路由器上绑定静态租约。
- App 目前按 IP 直连，无鉴权。局域网内风险可控，若后续要远程控制需加 token。
