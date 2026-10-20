#include "audio_codec.h"
#include "board.h"
#include "settings.h"

#include <esp_log.h>
#include <cstring>
#include <driver/i2s_common.h>

#define TAG "AudioCodec"

AudioCodec::AudioCodec() {
}

AudioCodec::~AudioCodec() {
}

void AudioCodec::OutputData(std::vector<int16_t>& data) {
    Write(data.data(), data.size());
}

bool AudioCodec::InputData(std::vector<int16_t>& data) {
    int samples = Read(data.data(), data.size());
    if (samples > 0) {
        return true;
    }
    return false;
}

void AudioCodec::Start() {
    Settings settings("audio", false);
    // 注意：必须用真实默认值读一次才知道 NVS 里到底有没有存过音量。
    // 之前只打印 "Audio codec started" 而不报音量值，导致"音量到底是不是 100"
    // 这件事在日志里完全看不出来——重启后 NVS 里的旧值会被静默加载。
    int saved_volume = settings.GetInt("output_volume", -1);
    output_volume_ = settings.GetInt("output_volume", output_volume_);
    if (output_volume_ <= 0) {
        ESP_LOGW(TAG, "Output volume value (%d) is too small, setting to default (10)", output_volume_);
        output_volume_ = 10;
    }

    ESP_LOGI(TAG, "Audio codec started, output volume: %d%s, input gain: %.1f",
             output_volume_,
             saved_volume < 0 ? "（未存过，用默认值）" : "（NVS 恢复）",
             input_gain_);
}

void AudioCodec::SetOutputVolume(int volume) {
    output_volume_ = volume;
    ESP_LOGI(TAG, "Set output volume to %d", output_volume_);
    
    Settings settings("audio", true);
    settings.SetInt("output_volume", output_volume_);
}

void AudioCodec::SetInputGain(float gain) {
    input_gain_ = gain;
    ESP_LOGI(TAG, "Set input gain to %.1f", input_gain_);
}

void AudioCodec::EnableInput(bool enable) {
    if (enable == input_enabled_) {
        return;
    }
    input_enabled_ = enable;
    ESP_LOGI(TAG, "Set input enable to %s", enable ? "true" : "false");
}

void AudioCodec::EnableOutput(bool enable) {
    if (enable == output_enabled_) {
        return;
    }
    output_enabled_ = enable;
    ESP_LOGI(TAG, "Set output enable to %s", enable ? "true" : "false");
    // 播报开始/结束的那一刻把音量一起打出来：排查"播报中崩溃"时，
    // 这里记录的是崩溃前最后一刻的真实音量，比启动时的音量更能说明问题
    // （用户可能在运行中途改过音量，NVS 里存的又是另一个值）。
    ESP_LOGI(TAG, "Output %s, current volume: %d", enable ? "started" : "stopped", output_volume_);
}
