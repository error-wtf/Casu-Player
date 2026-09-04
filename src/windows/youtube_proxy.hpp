// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// YoutubeProxy — loopback byte/range transport for resolved YouTube media
// (WP-MPCASU-041, port of mpcasu_qt/youtube_proxy.py). QTcpServer on
// 127.0.0.1 with a random token path; Range/206 handling via
// casu::network::range. Serves a local file directly (offline-testable) or
// proxies a resolved remote URL upstream. Never resolves YouTube itself.
// No Q_OBJECT (cross build without host moc).
#pragma once
#include <QObject>
#include <QString>

#include <functional>

class QTcpServer;
class QNetworkAccessManager;

namespace mpcasu {

class YoutubeProxy final : public QObject {
public:
    explicit YoutubeProxy(QObject* parent = nullptr);
    ~YoutubeProxy() override;

    // Serve a local media file over loopback (offline path used by the
    // transport self-test). Returns false + *error on failure.
    bool start_local(const QString& file_path, QString* error);
    // Serve an already-resolved HTTP(S) media URL; `refresh` re-resolves on
    // 403/410 (once per request). Returns false + *error on failure.
    bool start_remote(const QString& media_url,
                      std::function<QString()> refresh, QString* error);
    void stop();

    // Loopback URL libVLC opens (valid while running).
    QString media_url() const { return media_url_; }
    bool is_running() const { return server_ != nullptr; }
    int port() const;

private:
    bool bind_and_prepare(QString* error);
    class Connection;
    friend class Connection;
    QTcpServer* server_ = nullptr;
    QNetworkAccessManager* nam_ = nullptr;
    QString token_;
    QString media_url_;
    QString upstream_url_;
    std::function<QString()> refresh_;
    QString local_file_;
    bool remote_ = false;
};

}  // namespace mpcasu
