# 机器狗控制端（Android）

局域网直连机器狗的最小控制 App：状态面板、模式切换、急停、配网入口。
纯 Java + XML/ViewBinding，不用 Kotlin/Compose。

## 构建环境（本机实际验证过的组合）

| 项 | 版本 |
|---|---|
| Android Gradle Plugin | 8.7.3 |
| Gradle | 8.13（wrapper 已生成） |
| JDK | **21**（AGP 不支持 JDK 24，本机系统默认是 24，已在 `F:\software\jdk21\jdk-21.0.12.1+1` 另装 21） |
| compileSdk / targetSdk | 36 |
| minSdk | 26 |

验证结果：`app/build/outputs/apk/debug/app-debug.apk`（6.5 MB），`BUILD SUCCESSFUL`。

### 用 Android Studio 打开

1. File → Open 选 `android/` 目录。
2. Gradle JDK 选 **21**（Settings → Build Tools → Gradle → Gradle JDK）。系统默认的 JDK 24 会让 AGP 报 `Unsupported class file major version 68`。
3. Sync 后直接 Run。

### 命令行构建

```powershell
$env:JAVA_HOME = "F:\software\jdk21\jdk-21.0.12.1+1"
$env:PATH      = "$env:JAVA_HOME\bin;$env:PATH"
cd F:\software\workbuddy\AI-Dog\android
.\gradlew.bat assembleDebug
```

> 若在受限终端里遇到 `Could not write workspace metadata to C:\Users\<你>\.gradle\...`，
> 把 Gradle 缓存目录也指到工作区再跑：`$env:GRADLE_USER_HOME="F:\software\workbuddy\AI-Dog\.gradle-home"`。

## 使用

首次启动会要求填设备 IP（设置页），保存后主界面自动开始监听状态。
设备端需实现 `docs/Android控制端设计.md` 第 2 节的 4 个接口：`GET /api/status`、
`POST /api/mode`、`POST /api/stop`、`POST /api/wifi`（WebSocket `/ws` 可选，App 会自动降级为 2 秒轮询）。

## 目录

```
app/src/main/java/com/aidog/control/
├── MainActivity.java        # 状态面板 + 模式切换 + 急停
├── SetupActivity.java       # 设备 IP + 配网下发
├── api/DeviceApi.java       # OkHttp：HTTP + WebSocket + 自动降级轮询
└── model/DeviceStatus.java  # 状态模型（Gson）
```
