package com.voiney.lab;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
import com.voiney.lab.alarm.TimerAlarmPlugin;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // The app's own plugins (lane APP1); registered before the bridge
        // starts so the page sees them in Capacitor.Plugins.
        registerPlugin(TimerAlarmPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
