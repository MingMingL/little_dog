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
  "mode": "idle",          // idle | chat | patrol | guard | remote
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

### 2.4 遥控运动（2026-10-15 新增）

```
POST /api/move
{"dir": "forward", "speed": 60}
```

| 字段 | 取值 |
|------|------|
| `dir` | `stop` / `forward` / `backward` / `left` / `right` / `rotate_left` / `rotate_right` |
| `speed` | 0..100 百分比，选填，缺省 60（缺省故意给保守值，误触时不会冲太快） |

成功：`{"ok": true}`。

**只在 `remote` 模式下被接受**，其他模式返回 `{"ok": false, "error": "not in remote mode"}`
（App 收到会提示"请先切到遥控模式"），巡逻/看门自己有运动逻辑，不能被 App 抢电机。

**三层停车保护，缺一不可：**

1. App 松手 / 锁屏 / 退后台 → 立刻发 `{"dir":"stop"}`（第一道，最及时）；
2. App 只在同一时刻没有在途请求时才发新指令（**允许丢包、不允许堆积**）——
   否则网络一慢，请求会在客户端排队，恢复瞬间狗连着执行一批旧指令自己乱跑；
3. 设备端看门狗：`remote` 模式下 1.5s 没收到"让狗动"的指令就自动停车
   （防 App 被杀 / Wi-Fi 断了 / 手机锁屏），另在 Wi-Fi 断开和进配网模式时也立即停车。

speed→轮速的差速映射在设备端 `ai_dog_s3_board.cc`：`forward/backward` 双侧等速；
`left/right` 内侧轮降到 35%；`rotate_*` 两轮反向（原地转）。

### 2.5 急停

```
POST /api/stop
```
响应：`{"ok": true}`。设备收到后**立即停电机并退出当前模式进入 idle**，该指令优先级高于一切。

### 2.6 配网

```
POST /api/wifi
{"ssid": "MyHome", "password": "xxxx"}
```
响应：`{"ok": true, "ip": "192.168.1.50"}`。设备保存凭据并尝试连接，成功后回传新 IP。

### 2.7 语音控制模式切换（设备端 MCP 工具，2026-10-14 新增）

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

### 2.8 状态推送（可选，App 已实现自动降级）

```
WS ws://<ip>/ws
```
服务端每 1 秒推送一次与 `/api/status` 相同的 JSON。
**App 逻辑**：先尝试 WebSocket；连不上则自动降级为每 2 秒轮询 `GET /api/status`。因此设备端可以先只实现 2.1–2.4，App 也能正常工作。

## 3. App 结构

```
android/
└── app/src/main/java/com/aidog/control/
    ├── MainActivity.java       # 状态面板 + 模式切换 + 遥控方向键 + 急停
    ├── SetupActivity.java      # 设备 IP 设置 + 配网入口
    ├── api/DeviceApi.java      # OkHttp：HTTP 请求 + WebSocket + 自动降级轮询 + /api/move
    └── model/DeviceStatus.java # 状态数据模型（Gson 解析）
```

要点：

- 设备 IP 存 `SharedPreferences`，首次启动（`onCreate`）通过 `ActivityResultLauncher` 拉起 `SetupActivity`；
  回调里若 IP 仍为空说明用户放弃配置，主界面直接 `finish()` 退出 —— `onResume` 里
  **不要**再无条件 `startActivity(SetupActivity)`，否则用户从设置页侧滑/返回后会被
  立刻重新拉回去，App 永远退不出去。
- 状态展示：在线/离线、当前模式、电量、Wi-Fi、故障原因。
- 模式切换五个按钮（待机/聊天/巡逻/看门/**遥控**）；**急停按钮常驻且最高优先级**（红色，独立请求）。
- 遥控：方向键用 `OnTouch`（不是 `OnClick`）实现"按住持续走、松手停"，按住期间按 10Hz
  重复发同一条指令。`ACTION_CANCEL` 也必须停 —— 手指滑出按钮边界时系统只给 CANCEL，
  漏掉它狗会一直跑。退后台（`onPause`）同样停车。
- 配网页填写 SSID/密码后下发，成功后自动更新保存的 IP。
- 所有网络请求走子线程，回调切回主线程更新 UI。

## 4. 设备端实现（已完成，待真机验证）

文件：`firmware/xiaozhi-esp32/main/boards/ai-dog-s3/`

| 文件 | 职责 |
|------|------|
| `ai_dog_control.{h,cc}` | 局域网 HTTP 控制服务：收指令 + 报状态 + 遥控看门狗 |
| `ai_dog_motor.{h,cc}`   | TB6612 双电机驱动（GPIO + LEDC PWM），只翻译速度不决策 |
| `ai_dog_s3_board.cc`    | 板级接线：把指令接到状态机回调，做 dir→左右轮速的差速映射 |

- `esp_http_server` 监听 80 端口，注册 5 个 URI（status/mode/stop/move/wifi）；JSON 用 cJSON 编解码。
- `ControlServer` 单例只做"收指令 + 报状态"，**不直接驱动电机**；
  运动模块通过 `SetModeChangedHandler` / `SetEmergencyStopHandler` / `SetMoveCommandHandler` 接入，
  保证指令永远经过模式状态机（这是项目硬性约定，绕过它急停和模式互斥全会失效）。
- 启停时机（在 `ai_dog_s3_board.cc` 里包装网络事件回调）：
  - STA 连上路由器 → `Start()`（同时启动遥控看门狗定时器）；
  - 进入配网热点 → `Stop()`（热点自带 captive portal 占 80 端口，必须让开）；
  - 断开 → **立即停电机** + 上报故障（App already 发不出指令了，设备必须自己停）。
- 配网：`SsidManager::AddSsid()` 写入 NVS 后 `WifiManager::StartStation()` 重连，失败会自动回退到配网模式，不会失联。
- 依赖已加进 `main/CMakeLists.txt`（`esp_http_server`、`esp_wifi`、`espressif__cjson`、`esp_driver_ledc`）。
- 电池采样（GPIO8 ADC）尚未接入，`battery` 暂返回 -1（App 显示"无采样"）。

电机引脚（config.h）：左 PWM=GPIO11 / IN1=12 / IN2=13，右 PWM=GPIO14 / IN1=21 / IN2=47，
STANDBY=GPIO10（低电平关断整片 TB6612）。PWM 20kHz（超出人耳听感，避免啸叫）+ 8bit。

### 已完成的验证

1. 固件编译：0 error / 0 warning，`xiaozhi.bin` 2,582,016 B，分区占用 63%（37% 余量）。
2. App 用模拟设备（`tools/sim_dog.py`）端到端验证过：模式切换、7 个方向、按住 10Hz 连续发送、
   松手即停、非遥控模式被拒并弹提示，全部符合预期。
   用法：`python tools/sim_dog.py 8080` + `adb reverse tcp:8080 tcp:8080`，把 App 的设备 IP
   临时填成 `127.0.0.1:8080` 即可在无硬件时跑通全链路（模拟器的拒绝逻辑与真机一致）。
3. 电机 GPIO 行为尚未真机验证（TB6612 和底盘未焊接）。
4. 配网写 NVS 后调用 `esp_wifi` 重连，失败要回退到原配置，避免"配错网就失联"。
5. 该服务只在局域网监听，**不要做端口转发暴露到公网**。

## 5. 待办 / 风险

- 设备 IP 会随 DHCP 变化 → 后续可加 mDNS（`ai-dog.local`）或在路由器上绑定静态租约。
- App 目前按 IP 直连，无鉴权。局域网内风险可控，若后续要远程控制需加 token。
