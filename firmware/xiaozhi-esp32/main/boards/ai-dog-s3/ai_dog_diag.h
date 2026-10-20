#ifndef AI_DOG_DIAG_H
#define AI_DOG_DIAG_H

#include <cstddef>
#include <cstdint>
#include <cstdarg>

#include <esp_partition.h>
#include <freertos/FreeRTOS.h>

namespace aidog {

/**
 * 复位原因（esp_reset_reason() 的中文包装）。
 *
 * 排查"电池供电时反复重新初始化"就看 is_brownout：
 * 为 true 说明芯片是被低压检测器打重启的，问题在供电链路，不在代码。
 */
struct ResetInfo {
    int raw = 0;                 // esp_reset_reason() 的枚举值
    const char* code = "UNKNOWN";
    const char* text = "未知";
    bool is_brownout = false;
};

/**
 * 设备本地日志（DeviceLog）
 *
 * 解决的问题：机器狗用电池跑的时候没有串口，等插上串口时现场已经没了。
 * 所以日志要落到板子自己的 flash 里，掉电不丢，下次启动自动打到控制台。
 *
 * 组成：
 *   - esp_log 钩子：所有 ESP_LOGx 输出都进内存环缓冲（不打断原输出）；
 *   - 内存环：只是"待落盘"的暂存（后台任务 2s 刷一次），不对外提供读取接口；
 *     需要看历史就启动时由 DumpFlash 打上次的 flash 记录，别再引入第二个读取口子；
 *   - flash 环：doglog 分区，256 B 一条定长记录，环形覆盖；
 *   - 启动时先把上次的记录打到串口，再继续正常运行。
 *
 * 电源安全性：写 flash 前先擦除 4K 扇区，记录带 magic + 单调序号，
 * 掉电写在半条记录上会被下次启动的扫描识别为无效并跳过，不会读到乱码。
 */
class DeviceLog {
public:
    static DeviceLog& GetInstance();

    /** 安装 esp_log 钩子 + 读取并打印上次的持久化日志。重复调用安全。 */
    void Init();

    const ResetInfo& reset_info() const { return reset_info_; }

    /** 记一条自己的诊断日志（同时进内存环，由后台任务落盘）。
     *  目前只用到 Warn/Error：ESP_LOGW/ESP_LOGE 已经覆盖 I 级，
     *  不需要再挂一个没人调的 Info。 */
    void Warn(const char* fmt, ...) __attribute__((format(printf, 2, 3)));
    void Error(const char* fmt, ...) __attribute__((format(printf, 2, 3)));

    /** 立即把内存环里还没落盘的记录写进 flash（优雅停机前调用） */
    void Flush();

    bool persistent_enabled() const { return partition_ != nullptr; }
    size_t persistent_records() const { return valid_records_; }

private:
    DeviceLog() = default;
    DeviceLog(const DeviceLog&) = delete;
    DeviceLog& operator=(const DeviceLog&) = delete;

    struct RamEntry {
        uint32_t seq = 0;         // 单调递增，0 表示空槽
        uint32_t uptime_ms = 0;
        uint8_t level = 0;        // 0=I 1=W 2=E
        char text[192]{};
    };

    static constexpr size_t kRamEntries = 96;
    static constexpr size_t kFlashRecordSize = 256;
    static constexpr size_t kFlashTextSize = kFlashRecordSize - 16;
    static constexpr size_t kSectorSize = 4096;

    void OnEspLogLine(const char* fmt, va_list args);
    void AppendFormatted(uint8_t level, const char* fmt, va_list args);
    void AppendRam(uint8_t level, const char* text);
    void FlushPending();
    bool AppendFlash(uint8_t level, uint32_t uptime_ms, const char* text);
    void ScanFlash();
    void DumpFlash();

    static int LogHook(const char* fmt, va_list args);
    static void FlushTask(void* arg);

    ResetInfo reset_info_;
    bool initialized_ = false;

    // esp_log 钩子
    int (*original_vprintf_)(const char*, va_list) = nullptr;

    // flash（doglog 分区）
    const esp_partition_t* partition_ = nullptr;
    size_t flash_capacity_records_ = 0;
    size_t next_index_ = 0;
    uint32_t flash_seq_ = 0;
    size_t valid_records_ = 0;
    size_t current_sector_ = 0;
    bool sector_valid_ = false;

    // 内存环
    RamEntry ram_[kRamEntries]{};
    uint32_t ram_next_seq_ = 1;   // 下一条的序号
    uint32_t persisted_seq_ = 0;  // 已落盘到的最新序号

    portMUX_TYPE spinlock_ = portMUX_INITIALIZER_UNLOCKED;
};

}  // namespace aidog

#endif  // AI_DOG_DIAG_H
