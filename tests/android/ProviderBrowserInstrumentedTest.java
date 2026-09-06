package org.casu.mpcasu;
import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ProviderBrowserInstrumentedTest {
    @Test public void userAgentRetainsRealPlatformAndEngine() {
        String original = "Mozilla/5.0 (Linux; Android 14; Phone; wv) AppleWebKit/537.36 Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36";
        String compatible = ProviderActivity.compatibleUserAgent(original);
        assertFalse(compatible.contains("; wv"));
        assertFalse(compatible.contains("Version/4.0"));
        assertTrue(compatible.contains("Android 14"));
        assertTrue(compatible.contains("Chrome/120.0.0.0"));
    }

    @Test public void providerButtonsStayInApplication() throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        Activity main = instrumentation.startActivitySync(new Intent(
            instrumentation.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        java.lang.reflect.Method open = MainActivity.class.getDeclaredMethod("openProvider", String.class, String.class);
        open.setAccessible(true);
        try {
            for (String provider : new String[]{"NETFLIX", "SPOTIFY"}) {
                Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(ProviderActivity.class.getName(), null, false);
                instrumentation.runOnMainSync(() -> {
                    try { open.invoke(main, provider, "about:blank"); }
                    catch (Exception e) { throw new RuntimeException(e); }
                });
                Activity browser = instrumentation.waitForMonitorWithTimeout(monitor, 15000);
                assertNotNull("Provider must open inside MPCASU", browser);
                assertEquals(instrumentation.getTargetContext().getPackageName(), browser.getPackageName());
                instrumentation.runOnMainSync(browser::finish);
                instrumentation.removeMonitor(monitor);
            }
        } finally { instrumentation.runOnMainSync(main::finish); }
    }
}
