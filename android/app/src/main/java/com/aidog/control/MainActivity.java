package com.aidog.control;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;
import android.view.View;
import android.widget.SeekBar;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;

import com.aidog.control.api.DeviceApi;
import com.aidog.control.databinding.ActivityMainBinding;
import com.aidog.control.model.DeviceStatus;
import com.google.android.material.button.MaterialButton;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 主控界面：状态面板 + 模式切换 + 急停。
 * 设备 IP 存在 SharedPreferences，没配置过就跳设置页。
 */
public class MainActivity extends AppCompatActivity {

    private ActivityMainBinding binding;
    private DeviceApi api;
    private Prefs prefs;

    private String currentMode = "idle";

    /** 模式名 -> 按钮。既用来打 tag 分发点击，也用来高亮，避免两处各列一遍按钮 */
    private final Map<String, MaterialButton> modeButtons = new LinkedHashMap<>();

    // ---- 遥控状态 ----
    /** 重复发送间隔：10Hz。设备端看门狗是 1.5s，正常网络下绝不超时 */
    private static final long MOVE_REPEAT_MS = 100L;
    private final Handler moveHandler = new Handler(Looper.getMainLooper());
    private Runnable moveRepeat;
    private String holdingDir = null;   // 当前按住的方向，null 表示没在按
    private int currentSpeed;           // 初值直接取自 SeekBar，避免"默认值"在三处各写一遍

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
                        if (!prefs.hasDeviceIp()) {
                            finish();
                        }
                    });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        binding = ActivityMainBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        prefs = new Prefs(this);
        api = new DeviceApi();

        setupModeButtons();
        setupActions();
        setupRemote();

        // 只在首次创建时引导一次，交给 launcher 收结果；onResume 不再重复拉起
        if (!prefs.hasDeviceIp()) {
            setupLauncher.launch(new Intent(this, SetupActivity.class));
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        String ip = prefs.getDeviceIp();
        if (ip.isEmpty()) {
            // 没配置 IP：不再自动拉起设置页（否则返回手势会被死循环吃掉），
            // 只显示未配置状态，退出动作交给系统返回键 / 侧滑手势
            binding.textDeviceIp.setText(R.string.device_unset);
            return;
        }
        api.setDeviceIp(ip);
        binding.textDeviceIp.setText(getString(R.string.device_ip_value, ip));
        startListening();
    }

    @Override
    protected void onPause() {
        super.onPause();
        // 退后台 / 锁屏必须停车：手机侧已经没人按着了，
        // 设备端看门狗（1.5s）是兜底，这里主动发 stop 才是第一道保险
        stopMove();
        api.stopStatusPush();
    }

    private void setupModeButtons() {
        modeButtons.put("idle", binding.buttonModeIdle);
        modeButtons.put("chat", binding.buttonModeChat);
        modeButtons.put("patrol", binding.buttonModePatrol);
        modeButtons.put("guard", binding.buttonModeGuard);
        modeButtons.put("remote", binding.buttonModeRemote);

        View.OnClickListener modeClick = v -> {
            String mode = (String) v.getTag();
            if (mode == null || mode.equals(currentMode)) return;
            setMode(mode);
        };
        for (Map.Entry<String, MaterialButton> entry : modeButtons.entrySet()) {
            entry.getValue().setTag(entry.getKey());
            entry.getValue().setOnClickListener(modeClick);
        }
    }

    // ------------------------------------------------------------------
    // 遥控：按住方向键持续走，松手即停
    // ------------------------------------------------------------------

    private void setupRemote() {
        // 默认速度只有一处定义：布局里 SeekBar 的 android:progress。
        // 这里读回来，省掉"Java 常量 + 布局初值 + 文案初值"三处各写一个 60 的老问题。
        currentSpeed = binding.seekSpeed.getProgress();
        updateSpeedLabel();

        bindDirButton(binding.buttonForward, "forward");
        bindDirButton(binding.buttonBackward, "backward");
        bindDirButton(binding.buttonTurnLeft, "left");
        bindDirButton(binding.buttonTurnRight, "right");
        bindDirButton(binding.buttonRotateLeft, "rotate_left");
        bindDirButton(binding.buttonRotateRight, "rotate_right");

        binding.seekSpeed.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar seekBar, int progress, boolean fromUser) {
                currentSpeed = progress;
                updateSpeedLabel();
            }

            @Override
            public void onStartTrackingTouch(SeekBar seekBar) {
            }

            @Override
            public void onStopTrackingTouch(SeekBar seekBar) {
            }
        });
        updateRemoteHint();
    }

    /**
     * 用 OnTouch 而不是 OnClick：遥控要的是"按住持续走、松手停"，
     * 点击事件只有按下没有持续。ACTION_CANCEL 也要停 ——
     * 手指滑出按钮边界时系统只会给 CANCEL，漏掉它狗会一直跑。
     */
    private void bindDirButton(MaterialButton button, String dir) {
        button.setOnTouchListener((v, event) -> {
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    v.setPressed(true);
                    startMove(dir);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                case MotionEvent.ACTION_POINTER_UP:
                    v.setPressed(false);
                    stopMove();
                    return true;
                default:
                    return false;
            }
        });
    }

    private void startMove(String dir) {
        if (!"remote".equals(currentMode)) {
            Toast.makeText(this, R.string.msg_remote_needed, Toast.LENGTH_SHORT).show();
            return;
        }
        holdingDir = dir;
        moveHandler.removeCallbacks(moveRepeat);
        moveRepeat = new Runnable() {
            @Override
            public void run() {
                if (holdingDir == null) {
                    return;  // 已经松手了，别再发
                }
                api.move(holdingDir, currentSpeed, (ok, message) -> {
                    // 只有设备明确说"不是遥控模式"才需要中断重复发送并提示；
                    // 连接失败/超时是网络问题，让它继续重试（设备端看门狗会兜底停车），
                    // 否则每按一次方向键就弹一次 toast，很烦人
                    if (!ok && message != null && message.contains("not in remote mode")) {
                        moveHandler.removeCallbacks(moveRepeat);
                        holdingDir = null;
                        Toast.makeText(MainActivity.this,
                                getString(R.string.msg_remote_rejected, message),
                                Toast.LENGTH_SHORT).show();
                    }
                });
                moveHandler.postDelayed(this, MOVE_REPEAT_MS);
            }
        };
        moveHandler.post(moveRepeat);
    }

    private void stopMove() {
        moveHandler.removeCallbacks(moveRepeat);
        if (holdingDir == null) {
            return;  // 本来就没在走，没必要发 stop
        }
        holdingDir = null;
        api.move("stop", 0, (ok, message) -> {
            // stop 失败也不用弹窗打扰：设备端看门狗 1.5s 后会自己停
            if (!ok) {
                Toast.makeText(this, getString(R.string.msg_stop_failed, message),
                        Toast.LENGTH_SHORT).show();
            }
        });
    }

    /**
     * 只掐掉重复发送，不发 stop —— 切模式/急停时设备端自己会停电机，
     * 再补一条 stop 反而可能因为已不是 remote 模式被拒绝，弹出无意义的报错。
     */
    private void cancelMoveRepeat() {
        moveHandler.removeCallbacks(moveRepeat);
        holdingDir = null;
    }

    private void updateSpeedLabel() {
        binding.textSpeed.setText(getString(R.string.speed_value, currentSpeed));
    }

    /** 根据当前模式刷新遥控区提示：非遥控模式要明确告诉用户方向键为什么按不动 */
    private void updateRemoteHint() {
        boolean remote = "remote".equals(currentMode);
        binding.textRemoteHint.setText(remote ? R.string.remote_active : R.string.remote_inactive);
    }

    private void setupActions() {
        binding.buttonStop.setOnClickListener(v -> {
            cancelMoveRepeat();   // 急停最高优先级，先把本地的重复发送掐掉
            api.emergencyStop((ok, message) -> {
                Toast.makeText(this,
                        ok ? getString(R.string.msg_estop_ok)
                           : getString(R.string.msg_estop_failed, message),
                        Toast.LENGTH_SHORT).show();
                if (ok) {
                    currentMode = "idle";
                    highlightMode(currentMode);
                    updateRemoteHint();
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
                // 离开遥控模式：设备端换模式时会自动停电机，这里只掐掉本地重复发送
                if (!"remote".equals(mode)) {
                    cancelMoveRepeat();
                }
                currentMode = mode;
                highlightMode(mode);
                updateRemoteHint();
                Toast.makeText(this, getString(R.string.msg_mode_switched, DeviceStatus.modeLabel(mode)),
                        Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, getString(R.string.msg_mode_failed, message),
                        Toast.LENGTH_SHORT).show();
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
        updateRemoteHint();
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
        for (MaterialButton button : modeButtons.values()) {
            resetButton(button);
        }
        // 文字颜色不用再设一遍：resetButton 已经把所有按钮刷成白色，
        // 高亮只是换背景色，重复设同一个值是纯冗余。
        MaterialButton target = modeButtons.get(mode);
        if (target != null) {
            target.setBackgroundColor(getColor(R.color.primary));
        }
    }

    private void resetButton(MaterialButton button) {
        button.setBackgroundColor(getColor(android.R.color.darker_gray));
        button.setTextColor(getColor(android.R.color.white));
    }
}
