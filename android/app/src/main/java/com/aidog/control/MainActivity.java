package com.aidog.control;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;

import com.aidog.control.api.DeviceApi;
import com.aidog.control.databinding.ActivityMainBinding;
import com.aidog.control.model.DeviceStatus;
import com.google.android.material.button.MaterialButton;

/**
 * 主控界面：状态面板 + 模式切换 + 急停。
 * 设备 IP 存在 SharedPreferences，没配置过就跳设置页。
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "ai_dog";
    private static final String KEY_IP = "device_ip";

    private ActivityMainBinding binding;
    private DeviceApi api;
    private SharedPreferences prefs;

    private String currentMode = "idle";

    /**
     * 设置页返回回调。
     *
     * 这里是"侧滑退不出 App"这个 bug 的关键：早期版本在 onResume 里无条件
     * startActivity(SetupActivity)，于是用户在设置页（也就是打开 App 看到的第一个页面）
     * 侧滑返回后，MainActivity 的 onResume 立刻又把设置页拉起来 —— 设置页被反复重建，
     * 看起来就是"怎么滑都退不出去"。
     *
     * 改成由 launcher 收结果：返回时 IP 仍为空说明用户放弃配置，直接结束主界面退出 App。
     */
    private final ActivityResultLauncher<Intent> setupLauncher =
            registerForActivityResult(new ActivityResultContracts.StartActivityForResult(),
                    result -> {
                        if (prefs.getString(KEY_IP, "").isEmpty()) {
                            finish();
                        }
                    });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        binding = ActivityMainBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        api = new DeviceApi();

        setupModeButtons();
        setupActions();

        // 只在首次创建时引导一次，交给 launcher 收结果；onResume 不再重复拉起
        if (prefs.getString(KEY_IP, "").isEmpty()) {
            setupLauncher.launch(new Intent(this, SetupActivity.class));
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        String ip = prefs.getString(KEY_IP, "");
        if (ip.isEmpty()) {
            // 没配置 IP：不再自动拉起设置页（否则返回手势会被死循环吃掉），
            // 只显示未配置状态，退出动作交给系统返回键 / 侧滑手势
            binding.textDeviceIp.setText(R.string.device_unset);
            return;
        }
        api.setDeviceIp(ip);
        binding.textDeviceIp.setText("设备：" + ip);
        startListening();
    }

    @Override
    protected void onPause() {
        super.onPause();
        api.stopStatusPush();
    }

    private void setupModeButtons() {
        View.OnClickListener modeClick = v -> {
            String mode = (String) v.getTag();
            if (mode == null || mode.equals(currentMode)) return;
            setMode(mode);
        };

        binding.buttonModeIdle.setTag("idle");
        binding.buttonModeChat.setTag("chat");
        binding.buttonModePatrol.setTag("patrol");
        binding.buttonModeGuard.setTag("guard");

        binding.buttonModeIdle.setOnClickListener(modeClick);
        binding.buttonModeChat.setOnClickListener(modeClick);
        binding.buttonModePatrol.setOnClickListener(modeClick);
        binding.buttonModeGuard.setOnClickListener(modeClick);
    }

    private void setupActions() {
        binding.buttonStop.setOnClickListener(v -> {
            api.emergencyStop((ok, message) -> {
                Toast.makeText(this, ok ? "已急停" : ("急停失败：" + message), Toast.LENGTH_SHORT).show();
                if (ok) {
                    currentMode = "idle";
                    highlightMode(currentMode);
                }
            });
        });

        binding.buttonRefresh.setOnClickListener(v -> startListening());
        binding.buttonSetup.setOnClickListener(v ->
                setupLauncher.launch(new Intent(this, SetupActivity.class)));
    }

    private void startListening() {
        api.startStatusPush(new DeviceApi.StatusCallback() {
            @Override
            public void onStatus(DeviceStatus status) {
                renderStatus(status);
            }

            @Override
            public void onError(String message) {
                renderOffline(message);
            }
        });
    }

    private void setMode(String mode) {
        api.setMode(mode, (ok, message) -> {
            if (ok) {
                currentMode = mode;
                highlightMode(mode);
                Toast.makeText(this, "已切换到" + modeLabel(mode), Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, "切换失败：" + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void renderStatus(DeviceStatus status) {
        binding.textConnection.setText(R.string.status_online);
        binding.textConnection.setTextColor(getColor(R.color.status_online));

        binding.textMode.setText(status.modeLabel());
        binding.textBattery.setText(status.batteryLabel());
        binding.textWifi.setText(status.wifiLabel());
        binding.textFault.setText(status.faultLabel());

        currentMode = status.mode;
        highlightMode(status.mode);
    }

    private void renderOffline(String message) {
        binding.textConnection.setText(R.string.status_offline);
        binding.textConnection.setTextColor(getColor(R.color.status_offline));
        binding.textMode.setText(R.string.value_unknown);
        binding.textBattery.setText(R.string.value_unknown);
        binding.textWifi.setText(R.string.value_unknown);
        binding.textFault.setText(message);
    }

    /** 高亮当前模式按钮 */
    private void highlightMode(String mode) {
        resetButton(binding.buttonModeIdle);
        resetButton(binding.buttonModeChat);
        resetButton(binding.buttonModePatrol);
        resetButton(binding.buttonModeGuard);

        MaterialButton target = null;
        if ("idle".equals(mode)) target = binding.buttonModeIdle;
        else if ("chat".equals(mode)) target = binding.buttonModeChat;
        else if ("patrol".equals(mode)) target = binding.buttonModePatrol;
        else if ("guard".equals(mode)) target = binding.buttonModeGuard;

        if (target != null) {
            target.setBackgroundColor(getColor(R.color.primary));
            target.setTextColor(getColor(android.R.color.white));
        }
    }

    private void resetButton(MaterialButton button) {
        button.setBackgroundColor(getColor(android.R.color.darker_gray));
        button.setTextColor(getColor(android.R.color.white));
    }

    private String modeLabel(String mode) {
        switch (mode) {
            case "idle":   return "待机";
            case "chat":   return "聊天";
            case "patrol": return "巡逻";
            case "guard":  return "看门";
            default:       return mode;
        }
    }
}
