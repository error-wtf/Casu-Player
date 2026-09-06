// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
package org.casu.mpcasu;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.widget.Toast;

/** Shared provider navigation: never send protected players into a WebView. */
public final class ProviderBrowser {
    private ProviderBrowser() { }

    public static boolean requiresExternal(String url) {
        Uri uri = Uri.parse(url == null ? "" : url);
        String scheme = uri.getScheme();
        if (!"https".equalsIgnoreCase(scheme) && !"http".equalsIgnoreCase(scheme)) return false;
        String host = uri.getHost();
        if (host == null) return false;
        host = host.toLowerCase(java.util.Locale.ROOT);
        return domain(host, "netflix.com") || domain(host, "spotify.com") || domain(host, "tidal.com");
    }

    private static boolean domain(String host, String domain) {
        return host.equals(domain) || host.endsWith("." + domain);
    }

    public static void open(Activity activity, String url) {
        Uri uri = Uri.parse(url);
        Intent intent = new Intent(Intent.ACTION_VIEW, uri);
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        String host = uri.getHost();
        boolean netflix = host != null && domain(host.toLowerCase(java.util.Locale.ROOT), "netflix.com");
        // Netflix on Android uses the official app, not a desktop-browser UA.
        if (netflix) intent.setPackage("com.netflix.mediaclient");
        try {
            activity.startActivity(intent);
        } catch (ActivityNotFoundException | SecurityException e) {
            if (netflix) {
                new AlertDialog.Builder(activity)
                    .setTitle("Netflix-App erforderlich")
                    .setMessage("Netflix auf Android benötigt die offizielle Netflix-App. Bitte installiere sie und öffne Netflix anschließend erneut.")
                    .setPositiveButton("App installieren", (dialog, which) -> {
                        try {
                            activity.startActivity(new Intent(Intent.ACTION_VIEW,
                                Uri.parse("https://play.google.com/store/apps/details?id=com.netflix.mediaclient")));
                        } catch (ActivityNotFoundException | SecurityException unavailable) {
                            Toast.makeText(activity, "Kein Browser oder App-Store verfügbar", Toast.LENGTH_LONG).show();
                        }
                    }).setNegativeButton("Schließen", null).show();
            } else {
                Toast.makeText(activity, "Kein unterstützter Browser oder Anbieter-App verfügbar", Toast.LENGTH_LONG).show();
            }
        }
    }
}
