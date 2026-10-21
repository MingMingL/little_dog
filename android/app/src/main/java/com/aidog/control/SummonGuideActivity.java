package com.aidog.control;

import android.os.Bundle;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 召唤口令向导（SW-401）。
 *
 * Q-01 决策 A：唤醒词用预置"你好小智"，口令用 MultiNet 命令词
 * "过来/到这里来"（Kconfig AI_DOG_SUMMON_COMMAND_WORDS 配置，
 * menuconfig 免费自定义）。口令不是按用户训练的声纹，所以向导的
 * 职责是"教会用户怎么说 + 说明抢占/降级规则"，不是录音注册。
 */
public class SummonGuideActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_summon_guide);

        if (getSupportActionBar() != null) {
            getSupportActionBar().setTitle(R.string.title_summon_guide);
            getSupportActionBar().setDisplayHomeAsUpEnabled(true);
        }

        TextView steps = findViewById(R.id.text_summon_steps);
        steps.setText(String.format("%s\n\n%s\n\n%s\n\n%s",
                getString(R.string.summon_guide_step1),
                getString(R.string.summon_guide_step2),
                getString(R.string.summon_guide_step3),
                getString(R.string.summon_guide_step4)));

        TextView tip = findViewById(R.id.text_summon_tip);
        tip.setText(R.string.summon_guide_tip);
    }

    @Override
    public boolean onSupportNavigateUp() {
        finish();
        return true;
    }
}
