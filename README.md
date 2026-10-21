# AI-Dog · 机器狗 V1

基于 **ESP32-S3** 的仿四足机器狗：语音聊天 + 路径巡逻 + 看门模式（听到门口声音就狗叫报警）。

本项目以开源项目 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)（MIT）为语音基座，
新增了自定义板级 `ai-dog-s3`、局域网控制服务、Android 控制端，以及完整的接线/架构文档。

V1.5 新增**召唤功能**：喊唤醒词"你好小智"再说口令"过来/到这里来"，机器狗判断方向、
低速开到你面前（会调摄像头确认是你本人）。

## 当前状态

> 2026-10-15 首次真机启动通过；2026-10-18 新增**电池监测 + 复位原因 + 设备本地日志**：
> 掉电前的日志存在板子自己的 `doglog` 分区，下次插串口自动打出来；电量分四档保护，
> 赶在 5V 轨崩掉之前动作。用法与标定见 `docs/电池监测与设备日志.md`。

| 模块 | 状态 |
|---|---|
| 语音聊天（xiaozhi.me 云端） | 固件编译 + 烧录 + 启动已验证，云端会话待联调 |
| 自定义板级 `ai-dog-s3` | 真机确认 `SKU=ai-dog-s3`，引脚/音频/OLED 已完成 |
| 1.3 寸 OLED 屏（SH1106 / SPI） | 真机确认 `OledDisplay: Adding OLED display`（128x64 SPI），接线见文档 |
| 电池监测 / 低电压保护 | 已实现并真机验证（GPIO8 ADC + 分压，四档保护）；**分压比待标定** |
| 设备本地日志 + 复位原因 | 已实现并真机验证（`doglog` 64K 分区，掉电不丢，启动自动 dump） |
| 局域网控制服务（HTTP API） | 已完成，待联调 |
| Android 控制端（Java） | 已编译出 APK，待联调 |
| **召唤功能（V1.5）** | **代码完成**（24 项任务 SW-001~SW-404，见 `output/召唤狗狗功能软件开发任务计划-v1.0.md` §8）；仿真 11 场景全过；整机验收 AT-S01~S11 待硬件到货 |
| 电机 / 巡线 / 避障 / 看门 | TB6612 驱动已在真机初始化（PWM 20kHz，左 GPIO11 / 右 GPIO14），其余按接线方案 v7 逐级上电验证 |

## 目录结构

```
docs/       文档：PRD 索引、固件环境、Android 设计、电池监测与设备日志、接线方案 v1–v7、代码架构与改动说明
android/    Android 控制端（Java + ViewBinding，Gradle 工程，可编译）
output/     产品需求文档（PRD，15 章）
firmware/   固件：xiaozhi-esp32 工作副本 + 构建脚本 + 板级定义
```

- 固件构建：`firmware/build-ai-dog.ps1`（PowerShell）或 `firmware/build-ai-dog.py`（Python，PowerShell
  不可用时用它；会自动切到 IDF venv、处理 MSYS/PROCESSOR_ARCHITECTURE、关 ccache、带响应文件定义）。
  含 Windows 环境下的必要修正，见 `docs/固件开发环境.md`。
- 上游参考版本：xiaozhi-esp32 @ `e076ac4`（vendor/ 目录在本地保留，未入库）

## 快速开始

1. 烧录：见 `docs/固件开发环境.md`
2. 接线：见 `docs/硬件接线方案-v7.html`（830 点面包板；新手从"小白导读"和第二节"逐级上电验证教程"读起——内含调压/限流/防烧步骤）
3. 联调：手机装 `android/` 工程产出的 APK，与机器狗连同一 Wi-Fi

## 协议

设备端在局域网开放 4 个 HTTP 接口（`GET /api/status`、`POST /api/mode`、`POST /api/stop`、
`POST /api/wifi`），协议定义见 `docs/Android控制端设计.md`。

## V1.5 召唤功能（软件）

- 总开关：menuconfig → `AI Dog Summon` → `CONFIG_AI_DOG_ENABLE_SUMMON`，**默认 OFF**；
  OFF 时构建产物与 V1 完全一致。参数（口令词表/超时/步长/占空比/死区/频带/阈值……）
  全部在同一个菜单里 Kconfig 化，禁硬编码。
- 新增模块（`main/boards/ai-dog-s3/`）：`ai_dog_mic_array`（4 麦采集三后端）、
  `ai_dog_doa`（esp_apa_doa 方阵定向）、`ai_dog_voiceprint`（MultiNet L1 口令）、
  `ai_dog_summon`（S1–S5 状态机）、`ai_dog_vision`（ArduCAM + pedestrian_detect）、
  `ai_dog_vl53l0x`（测距）、`ai_dog_line_pcf8574`（巡线 I²C 化）。
- 无硬件验证：`python tools/sim_dog.py --scenario happy,wrong-passphrase,...`（11 个场景回归，
  覆盖主流程 + 口令失败/超时/丢源/避障/视觉无人/低压/看门禁用/抢占/急停/总时限）。
- 仿真过程固化并已修复两个状态机缺陷（S5 重搜无限循环、重搜阶段被总时限反复打回），
  详见任务计划文档 §8。
- 构建：`firmware\build-ai-dog.ps1 -Action build-summon` 一键切 ON（含分区表切换；
  嵌 esp-dl 人形模型后固件 5MB，默认 3.94MB 的 OTA 槽放不下，需用 `16m_summon.csv`）。

## 固件改动记录

本项目对上游的改动全部集中在 `firmware/xiaozhi-esp32/main/boards/ai-dog-s3/`：

| 提交 | 内容 |
|---|---|
| `5ccd7f1` | 新增 ai-dog-s3 板级（无屏 → 后加屏） |
| `deab432` | 局域网控制服务（4 个 HTTP 接口 + 模式状态 + 回调） |
| `ba31813` | 接入 OLED 屏（SPI 接口） |
| `9e01776` | 更正屏驱动为 SH1106（1.3 寸） |

## 许可

- 本项目自研代码：MIT
- 派生自 xiaozhi-esp32：保留其 MIT 许可（见 `firmware/xiaozhi-esp32/LICENSE`）
