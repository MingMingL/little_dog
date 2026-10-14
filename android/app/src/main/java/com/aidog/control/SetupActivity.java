package com.aidog.control;

import android.content.SharedPreferences;
import android.os.Bundle;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.aidog.control.api.DeviceApi;
import com.aidog.control.databinding.ActivitySetupBinding;

/**
 * 设备设置：填写设备 IP，以及给设备下发 Wi-Fi 凭据（配网入口）。
 * 配网成功后会把设备回传的新 IP 自动保存。
 */
public class SetupActivity extends AppCompatActivity {

    private static final String PREFS = "ai_dog";
    private static final String KEY_IP = "device_ip";

    private ActivitySetupBinding binding;
    private DeviceApi api;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        binding = ActivitySetupBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());
        setTitle(R.string.title_setup);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        api = new DeviceApi();

        binding.editDeviceIp.setText(prefs.getString(KEY_IP, ""));

        binding.buttonSave.setOnClickListener(v -> saveIp());
        binding.buttonProvision.setOnClickListener(v -> provision());
    }

    private void saveIp() {
        String ip = binding.editDeviceIp.getText().toString().trim();
        if (ip.isEmpty()) {
            Toast.makeText(this, R.string.msg_ip_required, Toast.LENGTH_SHORT).show();
            return;
        }
        prefs.edit().putString(KEY_IP, ip).apply();
        Toast.makeText(this, R.string.msg_saved, Toast.LENGTH_SHORT).show();
        finish();
    }

    private void provision() {
        String ip = binding.editDeviceIp.getText().toString().trim();
        String ssid = binding.editSsid.getText().toString().trim();
        String password = binding.editPassword.getText().toString();

        if (ip.isEmpty()) {
            Toast.makeText(this, R.string.msg_ip_required, Toast.LENGTH_SHORT).show();
            return;
        }
        if (ssid.isEmpty()) {
            binding.editSsid.setError("请填写 Wi-Fi 名称");
            return;
        }

        api.setDeviceIp(ip);
        binding.buttonProvision.setEnabled(false);
        api.provisionWifi(ssid, password, (ok, message) -> {
            binding.buttonProvision.setEnabled(true);
            if (ok) {
                // 成功时 message 可能是新 IP，也可能是原始响应体
                prefs.edit().putString(KEY_IP, ip).apply();
                Toast.makeText(this, R.string.msg_provision_ok + "（" + message + "）",
                        Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(this, "下发失败：" + message, Toast.LENGTH_LONG).show();
            }
        });
    }
}
