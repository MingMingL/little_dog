#include "ai_dog_diag.h"

#include <esp_log.h>
#include <esp_partition.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>

#define TAG "DeviceLog"

namespace {

// 定长记录布局（256 B，全部小端）：
//   [0..3]  magic 'D','L','O','G'
//   [4..7]  序号（单调递增，跨重启不清零）
//   [8..11] 写入时的 uptime（ms）
//   [12]    级别 0=I 1=W 2=E
//   [13]    写入时的复位原因（esp_reset_reason 枚举值）
//   [14..15] 保留
//   [16..255] 文本，NUL 结尾
constexpr char kMagic[4] = {'D', 'L', 'O', 'G'};

uint32_t Le32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

void PutLe32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
    p[2] = static_cast<uint8_t>(v >> 16);
    p[3] = static_cast<uint8_t>(v >> 24);
}

// 按格式串渲染一行日志。va_list 不能重复消费，内部必须 va_copy
void FormatLine(char* buffer, size_t size, const char* fmt, va_list args) {
    va_list copy;
    va_copy(copy, args);
    vsnprintf(buffer, size, fmt, copy);
    va_end(copy);
}

// 按序号升序（插入排序）。flash 环和内存环打印时都要"按时间顺序看"，
// 两处各写一遍排序逻辑迟早会写成两个顺序；条目最多几百条、只在打印时跑一次，
// 插入排序足够。要求 Item 带 seq 字段。
template <typename Item>
void SortBySeq(Item* items, size_t count) {
    for (size_t i = 1; i < count; ++i) {
        Item key = items[i];
        size_t j = i;
        while (j > 0 && items[j - 1].seq > key.seq) {
            items[j] = items[j - 1];
            --j;
        }
        items[j] = key;
    }
}

}  // namespace

namespace aidog {

ResetInfo DescribeReset(int raw) {
    ResetInfo info;
    info.raw = raw;
    switch (raw) {
        case 1:  info.code = "POWERON";     info.text = "正常上电"; break;
        case 2:  info.code = "EXT";         info.text = "外部复位（RESET 键/复位电路）"; break;
        case 3:  info.code = "SW";          info.text = "软件复位（esp_restart）"; break;
        case 4:  info.code = "PANIC";       info.text = "程序崩溃（Guru Meditation / Panic）"; break;
        case 5:  info.code = "INT_WDT";     info.text = "中断看门狗"; break;
        case 6:  info.code = "TASK_WDT";    info.text = "任务看门狗（有任务卡死）"; break;
        case 7:  info.code = "WDT";         info.text = "其他看门狗"; break;
        case 8:  info.code = "DEEPSLEEP";   info.text = "深度睡眠后唤醒"; break;
        case 9:  info.code = "BROWNOUT";    info.text = "掉压复位（电压掉到 brownout 门限）"; break;
        case 10: info.code = "SDIO";        info.text = "SDIO 复位"; break;
        case 11: info.code = "USB";         info.text = "USB 复位"; break;
        case 12: info.code = "JTAG";        info.text = "JTAG 复位"; break;
        case 13: info.code = "EFUSE";       info.text = "eFuse 错误"; break;
        case 14: info.code = "PWR_GLITCH";  info.text = "电源毛刺（power glitch）"; break;
        case 15: info.code = "CPU_LOCKUP";  info.text = "CPU 锁死"; break;
        default: info.code = "UNKNOWN";     info.text = "未知"; break;
    }
    info.is_brownout = (raw == 9);
    return info;
}

DeviceLog& DeviceLog::GetInstance() {
    static DeviceLog instance;
    return instance;
}

void DeviceLog::Init() {
    if (initialized_) return;
    initialized_ = true;

    reset_info_ = DescribeReset(static_cast<int>(esp_reset_reason()));

    // 找 doglog 分区。没找到（比如只烧了 app 没烧分区表）就退化成"仅串口"，
    // 功能照常，只是不持久化。
    partition_ = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "doglog");
    if (partition_ == nullptr) {
        ESP_LOGW(TAG, "未找到 doglog 分区，设备日志只走串口、不掉电保存（需要重新烧写分区表）");
    } else {
        flash_capacity_records_ = partition_->size / kFlashRecordSize;
        ScanFlash();
        DumpFlash();
    }

    ESP_LOGW(TAG, "本次启动：复位原因 = %s (%d) %s", reset_info_.code, reset_info_.raw, reset_info_.text);
    if (reset_info_.is_brownout) {
        ESP_LOGE(TAG, "*** 上次是掉压复位：供电链路扛不住负载，查 5V 轨和 MP1584 输入电压 ***");
    }

    // 先 dump 再挂钩子：dumpy 用 printf 直写 stdout，本来就不会被钩子收到，
    // 但顺序反了会让钩子漏掉启动早期那几条，没必要。
    original_vprintf_ = esp_log_set_vprintf(LogHook);

    // 落盘任务：钩子里只做内存拷贝（微秒级），慢活（擦除+写 flash）都在这儿。
    TaskHandle_t task = nullptr;
    BaseType_t ok = xTaskCreatePinnedToCore(FlushTask, "doglog", 3072, this,
                                            tskIDLE_PRIORITY + 2, &task, 0);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "doglog 落盘任务创建失败，日志只进内存环");
    }

    Flush();  // 复位原因这条立刻落盘，最要紧
}

int DeviceLog::LogHook(const char* fmt, va_list args) {
    GetInstance().OnEspLogLine(fmt, args);
    if (GetInstance().original_vprintf_ != nullptr) {
        return GetInstance().original_vprintf_(fmt, args);
    }
    return vprintf(fmt, args);
}

void DeviceLog::OnEspLogLine(const char* fmt, va_list args) {
    char buffer[224]{};
    FormatLine(buffer, sizeof(buffer), fmt, args);

    // 级别取行首字母（CONFIG_LOG_COLORS 未开，格式固定是 "E (123) tag: msg"）
    uint8_t level = 0;
    if (buffer[0] == 'E') level = 2;
    else if (buffer[0] == 'W') level = 1;

    AppendRam(level, buffer);
}

/** 格式化 + 进内存环。Warn/Error 只差级别，别把 vsnprintf 复制两份 */
void DeviceLog::AppendFormatted(uint8_t level, const char* fmt, va_list args) {
    char buffer[224]{};
    FormatLine(buffer, sizeof(buffer), fmt, args);
    AppendRam(level, buffer);
}

void DeviceLog::Warn(const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    AppendFormatted(1, fmt, args);
    va_end(args);
}

void DeviceLog::Error(const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    AppendFormatted(2, fmt, args);
    va_end(args);
}

void DeviceLog::AppendRam(uint8_t level, const char* text) {
    portENTER_CRITICAL(&spinlock_);
    RamEntry& entry = ram_[ram_next_seq_ % kRamEntries];
    entry.seq = ram_next_seq_;
    entry.uptime_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    entry.level = level;
    strncpy(entry.text, text, sizeof(entry.text) - 1);
    entry.text[sizeof(entry.text) - 1] = '\0';
    ram_next_seq_++;
    portEXIT_CRITICAL(&spinlock_);
}

void DeviceLog::FlushPending() {
    if (partition_ == nullptr) return;

    for (uint32_t seq = persisted_seq_ + 1; seq < ram_next_seq_; ++seq) {
        const RamEntry& entry = ram_[seq % kRamEntries];
        if (entry.seq != seq) continue;  // 已被更新的记录挤出环外
        if (!AppendFlash(entry.level, entry.uptime_ms, entry.text)) return;
        persisted_seq_ = seq;
    }
}

void DeviceLog::Flush() {
    FlushPending();
}

bool DeviceLog::AppendFlash(uint8_t level, uint32_t uptime_ms, const char* text) {
    if (partition_ == nullptr) return false;

    // 中断里不能做 flash 操作（会阻塞），直接放弃这条
    if (xPortInIsrContext()) return false;

    size_t offset = next_index_ * kFlashRecordSize;
    size_t sector = offset / kSectorSize;

    // 每条记录写之前必须保证所在 4K 是擦净的。环形写，跨扇区第一条先擦。
    if (!sector_valid_ || sector != current_sector_) {
        size_t erase_offset = sector * kSectorSize;
        size_t erase_size = kSectorSize;
        // 最后一段可能不满 4K，按分区尾部截断
        if (erase_offset + erase_size > partition_->size) {
            erase_size = partition_->size - erase_offset;
        }
        if (erase_size < kSectorSize) return false;
        if (esp_partition_erase_range(partition_, erase_offset, erase_size) != ESP_OK) {
            ESP_LOGE(TAG, "doglog 擦除失败 (offset=%u)，本条丢弃", static_cast<unsigned>(erase_offset));
            return false;
        }
        current_sector_ = sector;
        sector_valid_ = true;
    }

    uint8_t record[kFlashRecordSize]{};
    memcpy(record, kMagic, 4);
    PutLe32(record + 4, ++flash_seq_);
    PutLe32(record + 8, uptime_ms);
    record[12] = level;
    record[13] = static_cast<uint8_t>(reset_info_.raw & 0xFF);
    strncpy(reinterpret_cast<char*>(record + 16), text, kFlashTextSize - 1);

    if (esp_partition_write(partition_, offset, record, kFlashRecordSize) != ESP_OK) {
        ESP_LOGE(TAG, "doglog 写入失败 (index=%u)，本条丢弃", static_cast<unsigned>(next_index_));
        return false;
    }

    next_index_ = (next_index_ + 1) % flash_capacity_records_;
    valid_records_++;
    return true;
}

void DeviceLog::ScanFlash() {
    valid_records_ = 0;
    next_index_ = 0;
    flash_seq_ = 0;
    sector_valid_ = false;

    if (partition_ == nullptr || flash_capacity_records_ == 0) return;

    uint8_t record[kFlashRecordSize];
    uint32_t best_seq = 0;
    size_t best_index = 0;

    for (size_t i = 0; i < flash_capacity_records_; ++i) {
        if (esp_partition_read(partition_, i * kFlashRecordSize, record, kFlashRecordSize) != ESP_OK) {
            break;
        }
        if (memcmp(record, kMagic, 4) != 0) continue;  // 空槽或写坏的一条
        uint32_t seq = Le32(record + 4);
        if (seq == 0) continue;  // 序号 0 不用，视为无效
        valid_records_++;
        if (seq > best_seq) {
            best_seq = seq;
            best_index = i;
        }
    }

    if (valid_records_ > 0) {
        flash_seq_ = best_seq;
        next_index_ = (best_index + 1) % flash_capacity_records_;
    }
    // 起始槽所在扇区会在第一次 AppendFlash 时擦掉，sector_valid_ 保持 false
}

void DeviceLog::DumpFlash() {
    if (partition_ == nullptr || valid_records_ == 0) {
        printf("===== 设备本地日志：无历史记录 =====\n");
        return;
    }

    // 按序号排序后打印，这样环形覆盖过也能按时间顺序看
    struct Item { uint32_t seq; size_t index; };
    static Item items[512];
    size_t count = 0;

    uint8_t record[kFlashRecordSize];
    for (size_t i = 0; i < flash_capacity_records_ && count < 512; ++i) {
        if (esp_partition_read(partition_, i * kFlashRecordSize, record, kFlashRecordSize) != ESP_OK) continue;
        if (memcmp(record, kMagic, 4) != 0) continue;
        uint32_t seq = Le32(record + 4);
        if (seq == 0) continue;
        items[count].seq = seq;
        items[count].index = i;
        count++;
    }

    SortBySeq(items, count);  // 环形覆盖过也要按时间顺序看

    printf("===== 设备本地日志（上次掉电前，%u 条） =====\n", static_cast<unsigned>(count));
    for (size_t i = 0; i < count; ++i) {
        if (esp_partition_read(partition_, items[i].index * kFlashRecordSize, record, kFlashRecordSize) != ESP_OK) {
            continue;
        }
        record[kFlashRecordSize - 1] = '\0';
        const char* level = record[12] == 2 ? "E" : (record[12] == 1 ? "W" : "I");
        uint32_t uptime = Le32(record + 8);
        // xtensa 上 uint32_t 是 unsigned long，%u 要显式收窄，否则 -Werror=format 直接拦
        printf("[%s %6u.%03u] %s\n", level,
               static_cast<unsigned int>(uptime / 1000), static_cast<unsigned int>(uptime % 1000),
               reinterpret_cast<const char*>(record + 16));
    }
    printf("===== 设备本地日志结束 =====\n");
}

const char* DeviceLog::Dump(size_t max_lines) {
    dump_buffer_[0] = '\0';
    portENTER_CRITICAL(&spinlock_);

    // 收集有效条目
    struct Item { uint32_t seq; const RamEntry* entry; };
    static Item items[kRamEntries];
    size_t count = 0;
    uint32_t start = (ram_next_seq_ > kRamEntries) ? (ram_next_seq_ - kRamEntries) : 1;
    for (uint32_t seq = start; seq < ram_next_seq_; ++seq) {
        const RamEntry& entry = ram_[seq % kRamEntries];
        if (entry.seq != seq) continue;
        items[count].seq = seq;
        items[count].entry = &entry;
        count++;
    }
    SortBySeq(items, count);

    size_t begin = 0;
    if (max_lines > 0 && count > max_lines) begin = count - max_lines;

    size_t used = 0;
    for (size_t i = begin; i < count; ++i) {
        const RamEntry& e = *items[i].entry;
        int n = snprintf(dump_buffer_ + used, sizeof(dump_buffer_) - used,
                         "[%s %6u.%03u] %s\n",
                         e.level == 2 ? "E" : (e.level == 1 ? "W" : "I"),
                         static_cast<unsigned int>(e.uptime_ms / 1000),
                         static_cast<unsigned int>(e.uptime_ms % 1000), e.text);
        if (n < 0 || static_cast<size_t>(n) >= sizeof(dump_buffer_) - used) break;
        used += static_cast<size_t>(n);
    }
    portEXIT_CRITICAL(&spinlock_);
    return dump_buffer_;
}

void DeviceLog::FlushTask(void* arg) {
    auto* self = static_cast<DeviceLog*>(arg);
    // 2 秒轮询 + 外部通知：既不漏掉掉电前的最后几秒，也不会每条日志都写一次 flash
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(2000));
        self->FlushPending();
    }
}

}  // namespace aidog
