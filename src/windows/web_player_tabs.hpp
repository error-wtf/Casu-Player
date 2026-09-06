// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Tabbed embedded web-player views (Spotify/Hearthis/Tidal/Netflix/BROWSE).
// Exact port of mpcasu_qt/webplayers.py supporting Microsoft Edge WebView2
// (with DRM/Widevine on Windows) and QtWebEngine (Chromium).
#pragma once

#include "casu/web/webproviders.hpp"

#include <QMap>
#include <QString>
#include <QWidget>

class QLineEdit;
class QTabWidget;

namespace mpcasu {

class WebContainerWidget;

class WebPlayerTabs final : public QWidget {
public:
    explicit WebPlayerTabs(QWidget* parent = nullptr);
    ~WebPlayerTabs() override;

    // Load a provider's web player at a search query or direct URL.
    void open(const QString& provider, const QString& query = {},
              const QString& url = {});

    // Stream a direct media URL in an embedded <video> element (yt-dlp).
    bool play_video(const QString& url, const QString& title = {});

    // Switch to a provider's entry field (select-all + focus).
    void focus_entry(const QString& provider);

    QTabWidget* tabs() const { return tabs_; }

private:
    void build_tabs();
    void submit(const QString& key);
    void submit_browse();
    QWidget* make_page(const QString& key, const QString& label,
                       QLineEdit** entry_out, QWidget** view_out);

    QTabWidget* tabs_ = nullptr;
    // provider key -> URL/search entry
    QMap<QString, QLineEdit*> entries_;
    // provider key -> embedded view (WebContainerWidget or QWebEngineView; owned by page widget)
    QMap<QString, QWidget*> views_;
};

}  // namespace mpcasu