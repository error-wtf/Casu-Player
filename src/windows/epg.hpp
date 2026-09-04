// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Bounded Extended-M3U and XMLTV support — full port of casu/epg.py.
// Parity notes: identical limits (8 MiB playlist / 32 MiB XMLTV / 10k
// channels / 100k programmes / 4 KiB lines+text / 8 KiB URLs), UTF-8(BOM)→
// Latin-1 decode fallback, Extended-M3U attribute surface (tvg-id/tvg-name/
// group-title/tvg-logo plus url-tvg/x-tvg-url/tvg-url EPG lists), stream
// scheme whitelist, XMLTV timestamps WITH UTC offset conversion, stop<=start
// filtering, deterministic sort, DTD/entity rejection.
#pragma once
#include <QHash>
#include <QString>
#include <QStringList>
#include <QVector>

namespace mpcasu {

constexpr qint64 kMaxPlaylistBytes = 8LL * 1024 * 1024;
constexpr qint64 kMaxXmltvBytes = 32LL * 1024 * 1024;
constexpr int kMaxChannels = 10'000;
constexpr int kMaxProgrammes = 100'000;
constexpr int kMaxLineBytes = 4096;
constexpr int kMaxTextBytes = 4096;
constexpr int kMaxUrlBytes = 8192;

struct StreamChannel {
    QString url;
    QString name;
    QString epg_id;
    QString group;
    QString logo;
};

struct StreamCatalog {
    QVector<StreamChannel> channels;
    QStringList epg_urls;
};

struct Programme {
    QString channel_id;
    qint64 start_ms = 0;  // UTC epoch milliseconds
    qint64 stop_ms = 0;   // UTC epoch milliseconds
    QString title;
    QString description;
    QString category;
};

struct EpgGuide {
    QHash<QString, QString> channel_names;
    QVector<Programme> programmes;  // sorted by (channel_id, start, stop)

    // First `limit` programmes of the channel that end after now.
    QVector<Programme> schedule(const QString& channel_id, qint64 now_ms,
                                int limit = 20) const;
    void now_next(const QString& channel_id, qint64 now_ms,
                  const Programme** active, const Programme** upcoming) const;
};

// All parsers return an empty QString on success, otherwise a human-readable
// error mirroring the reference messages.
QString parse_m3u(const QByteArray& data, const QString& base_dir,
                  StreamCatalog* out);
QString load_m3u_file(const QString& path, StreamCatalog* out);
QString parse_xmltv_guide(const QByteArray& data, EpgGuide* out);
QString load_xmltv_file(const QString& path, EpgGuide* out);

// Legacy bridge kept for MainWindow: builds a renderable catalog from a
// parsed guide (channel id -> display name, no URLs — playback requires an
// M3U catalog exactly like the reference front end).
QString parse_xmltv(const QByteArray& data, StreamCatalog* out,
                    EpgGuide* guide);

}  // namespace mpcasu
