// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#include "playlist.hpp"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSet>
#include <QTextStream>
#include <QUrl>
#include <QXmlStreamReader>

#include <algorithm>

#include "casu/json.hpp"

namespace mpcasu {

// Resolve one playlist entry against the playlist's base directory,
// mirroring casu/playlist.py _entry: remote URLs stay verbatim (scheme AND
// netloc required, like urlparse), file:// and relative paths are resolved
// against base, ~ expands, absolute paths are normalized. Empty input yields
// an empty QString (skip).
namespace {
bool looks_remote(const QString& value) {
    const QUrl url(value, QUrl::StrictMode);
    return url.isValid() && !url.scheme().isEmpty() && !url.host().isEmpty()
        && url.scheme().compare(QStringLiteral("file"), Qt::CaseInsensitive) != 0;
}

QString resolve_entry(QString text, const QDir& base) {
    text = text.trimmed();
    if (text.isEmpty()) return QString();
    if (looks_remote(text)) return text;
    if (text.startsWith("file://", Qt::CaseInsensitive)) {
        const QUrl url(text);
        QString path_part = QUrl::fromPercentEncoding(url.path().toUtf8());
        // Windows drive-URI form: file:///C:/x yields path "/C:/x" — drop the
        // leading slash so the result is a valid local path again.
        if (path_part.size() >= 3 && path_part.at(0) == QLatin1Char('/')
            && path_part.at(1).isLetter() && path_part.at(2) == QLatin1Char(':'))
            path_part.remove(0, 1);
        if (!url.host().isEmpty() && url.host() != QLatin1String("localhost"))
            path_part = QStringLiteral("//%1%2").arg(url.host(), path_part);
        text = path_part;
    } else {
        text = QUrl::fromPercentEncoding(text.toUtf8());
    }
    if (text.startsWith('~')) {
        const QString home = QDir::homePath();
        text = home + text.mid(1);
    }
    QFileInfo cand(text);
    if (cand.isRelative()) {
        cand = QFileInfo(base.filePath(text));
    }
    // Path normalization equivalent of Path.resolve(): absolute + clean.
    QString resolved = QDir::cleanPath(cand.absoluteFilePath());
    if (!cand.exists()) return resolved;
    // Resolve symlinks when possible (best effort, mirrors resolve()).
    return QFileInfo(resolved).canonicalFilePath().isEmpty() ? resolved
           : QFileInfo(resolved).canonicalFilePath();
}
}  // namespace

QString display_title_for_path(const QString& path) {
    if (looks_remote(path)) return path;
    const QFileInfo info(path);
    return info.fileName().isEmpty() ? path : info.fileName();
}

namespace {
// casu/playlist.py _first_unquoted_comma: index of the first ',' that is not
// inside double quotes (EXTINF titles may contain quoted commas).
int first_unquoted_comma(const QString& value) {
    bool in_quote = false;
    for (int i = 0; i < value.size(); ++i) {
        const QChar c = value.at(i);
        if (c == QLatin1Char('"')) in_quote = !in_quote;
        else if (c == QLatin1Char(',') && !in_quote) return i;
    }
    return -1;
}

// casu/playlist.py _attr_ci: case-insensitive attribute lookup (ASX/WPL tags
// vary in case).
QString attr_ci(const QXmlStreamAttributes& attrs, const QString& name) {
    for (const QXmlStreamAttribute& a : attrs) {
        if (a.name().compare(name, Qt::CaseInsensitive) == 0)
            return a.value().toString();
    }
    return QString();
}
}  // namespace

void PlaylistModel::clear() {
    items_.clear();
    current_ = -1;
}

void PlaylistModel::add(const QString& path, const QString& title) {
    PlaylistItem item;
    item.path = path;
    item.is_url = looks_remote(path);
    item.title = title.isEmpty() ? display_title_for_path(path) : title.left(300);
    // casu/playlist.py types entries by VALUE: local playlists are Path
    // objects (suffix-typed), remote URLs stay strings. Existence is a
    // GUI-layer concern (existing_only), not part of the type.
    item.is_playlist = looks_like_playlist(path) && !looks_remote(path);
    items_.append(item);
}

void PlaylistModel::add_files(const QStringList& paths) {
    for (const QString& p : paths) add(p);
}

void PlaylistModel::remove(int index) {
    if (index < 0 || index >= items_.size()) return;
    items_.removeAt(index);
    if (current_ > index) --current_;
    else if (current_ == index) current_ = -1;
}

void PlaylistModel::remove_many(const QVector<int>& indices) {
    QVector<int> rows;
    for (int i : indices)
        if (i >= 0 && i < items_.size()) rows.append(i);
    std::sort(rows.begin(), rows.end(), std::greater<int>());
    for (int r : rows) remove(r);
}

void PlaylistModel::move(int from, int to) {
    if (from < 0 || from >= items_.size() || to < 0 || to >= items_.size() || from == to)
        return;
    items_.move(from, to);
}

void PlaylistModel::move_many(const QVector<int>& indices, int delta) {
    QVector<int> rows;
    for (int i : indices)
        if (i >= 0 && i < items_.size()) rows.append(i);
    std::sort(rows.begin(), rows.end());
    if (delta > 0) {
        for (auto it = rows.rbegin(); it != rows.rend(); ++it) move(*it, *it + 1);
    } else if (delta < 0) {
        for (int r : rows) move(r, r - 1);
    }
}

void PlaylistModel::reorder(const QStringList& paths) {
    QVector<PlaylistItem> reordered;
    for (const QString& path : paths) {
        const int idx = index_of(path);
        if (idx >= 0) reordered.append(items_[idx]);
    }
    for (const PlaylistItem& item : items_) {
        if (!paths.contains(item.path)) reordered.append(item);
    }
    items_ = std::move(reordered);
    current_ = qBound(-1, current_, static_cast<int>(items_.size()) - 1);
}

int PlaylistModel::index_of(const QString& path) const {
    for (int i = 0; i < items_.size(); ++i)
        if (items_[i].path == path) return i;
    return -1;
}

int PlaylistModel::next_index(bool automatic_end) const {
    if (items_.isEmpty()) return -1;
    if (current_ < 0) return 0;
    if (repeat == RepeatMode::One && automatic_end) return current_;
    int n = current_ + 1;
    if (n >= items_.size()) {
        if (repeat == RepeatMode::All || shuffle) n = 0;
        else return -1;
    }
    if (shuffle) {
        std::uniform_int_distribution<int> dist(0, items_.size() - 1);
        n = dist(rng_);
    }
    return n;
}

int PlaylistModel::previous_index() const {
    if (items_.isEmpty()) return -1;
    if (current_ < 0) return 0;
    int n = current_ - 1;
    if (n < 0) n = items_.size() - 1;
    return n;
}

std::string PlaylistModel::load_m3u(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    QString pending_title;
    const QDir base = QFileInfo(file).absoluteDir();
    out->clear();
    while (!ts.atEnd()) {
        QString line = ts.readLine().trimmed();
        if (line.isEmpty()) continue;
        if (line.startsWith('#')) {
            // casu/playlist.py: title = text after the FIRST UNQUOTED comma,
            // truncated to 300 chars; #EXTM3U and other directives skipped.
            if (line.startsWith("#EXTINF")) {
                const int comma = first_unquoted_comma(line);
                if (comma >= 0)
                    pending_title = line.mid(comma + 1).trimmed().left(300);
            }
            continue;
        }
        QString target = resolve_entry(line, base);
        if (!target.isEmpty()) out->add(target, pending_title);
        pending_title.clear();
    }
    return {};
}

std::string PlaylistModel::load_pls(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    QVector<std::pair<int, QString>> entries;
    QMap<int, QString> titles;
    const QDir base = QFileInfo(file).absoluteDir();
    while (!ts.atEnd()) {
        QString line = ts.readLine().trimmed();
        // casu/playlist.py: case-insensitive keys, optional whitespace around
        // '=', titles truncated to 300 chars.
        static const QRegularExpression reFile(
            QStringLiteral("^File(\\d+)\\s*=\\s*(.+)$"),
            QRegularExpression::CaseInsensitiveOption);
        static const QRegularExpression reTitle(
            QStringLiteral("^Title(\\d+)\\s*=\\s*(.*)$"),
            QRegularExpression::CaseInsensitiveOption);
        auto m = reFile.match(line);
        if (m.hasMatch()) {
            QString target = resolve_entry(m.captured(2).trimmed(), base);
            if (!target.isEmpty()) entries.append({m.captured(1).toInt(), target});
            continue;
        }
        m = reTitle.match(line);
        if (m.hasMatch()) titles[m.captured(1).toInt()] = m.captured(2).trimmed().left(300);
    }
    std::sort(entries.begin(), entries.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });
    out->clear();
    for (const auto& [idx, target] : entries)
        out->add(target, titles.value(idx));
    return {};
}

namespace {
// casu/playlist.py detect_playlist_format: unknown extensions are detected
// by CONTENT (first bytes), mirroring the reference sniffer.
enum class PlFormat { M3u, Pls, Wpl, Xspf, Jspf, Asx, Json, Unknown };
PlFormat sniff_format(const QString& file) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly)) return PlFormat::Unknown;
    QByteArray head = f.read(4096);
    f.close();
    while (head.size() && (head.at(0) == ' ' || head.at(0) == '\t'
                           || head.at(0) == '\r' || head.at(0) == '\n'))
        head.remove(0, 1);
    const QByteArray lower = head.toLower();
    if (head.startsWith('{')) {
        return lower.contains("\"playlist\"") || lower.contains("\"track\"")
                   ? PlFormat::Jspf : PlFormat::Json;
    }
    if (head.startsWith('<')) {
        if (lower.contains("xspf") || lower.contains("<tracklist")) return PlFormat::Xspf;
        if (lower.contains("<asx") || lower.contains("<entry") || lower.contains("<ref"))
            return PlFormat::Asx;
        if (lower.contains("<?wpl") || lower.contains("<media ")) return PlFormat::Wpl;
        if (lower.contains("<track")) return PlFormat::Xspf;
        return PlFormat::Unknown;
    }
    if (head.startsWith("#EXTM3U") || head.startsWith("#EXTINF")) return PlFormat::M3u;
    if (head.startsWith("[playlist]")) return PlFormat::Pls;
    const int nl = static_cast<int>(head.indexOf('\n'));
    const QByteArray line = nl < 0 ? head : head.left(nl);
    if (line.trimmed().startsWith("#")) return PlFormat::M3u;
    if (line.toLower().contains("file1=")) return PlFormat::Pls;
    if (!line.isEmpty() && line.at(0) != '#') return PlFormat::M3u;
    return PlFormat::Unknown;
}
}  // namespace

std::string PlaylistModel::load_file(const QString& file, PlaylistModel* out) {
    QString lower = file.toLower();
    if (lower.endsWith(".pls")) return load_pls(file, out);
    if (lower.endsWith(".xspf")) return load_xspf(file, out);
    if (lower.endsWith(".wpl")) return load_wpl(file, out);
    if (lower.endsWith(".jspf")) return load_jspf(file, out);
    if (lower.endsWith(".asx") || lower.endsWith(".wmx") ||
        lower.endsWith(".wvx") || lower.endsWith(".axs")) return load_asx(file, out);
    if (lower.endsWith(".rmp")) return load_rmp(file, out);
    if (lower.endsWith(".ram")) return load_ram(file, out);
    if (lower.endsWith(".json")) return load_mpcasu_json(file, out);
    if (lower.endsWith(".m3u") || lower.endsWith(".m3u8")) return load_m3u(file, out);
    // Content detection fallback (unknown extension).
    switch (sniff_format(file)) {
        case PlFormat::Pls:  return load_pls(file, out);
        case PlFormat::Xspf: return load_xspf(file, out);
        case PlFormat::Wpl:  return load_wpl(file, out);
        case PlFormat::Jspf: return load_jspf(file, out);
        case PlFormat::Asx:  return load_asx(file, out);
        case PlFormat::Json: return load_mpcasu_json(file, out);
        case PlFormat::M3u:  return load_m3u(file, out);
        case PlFormat::Unknown: break;
    }
    return ("unknown playlist format: " + file).toStdString();
}

// XSPF (http://xspf.org/ns/0/) — casu/playlist.py _parse_xspf_entries.
std::string PlaylistModel::load_xspf(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QDir base = QFileInfo(file).absoluteDir();
    QXmlStreamReader xml(&f);
    out->clear();
    QString title;
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement()) {
            const auto name = xml.name();
            if (name == QLatin1String("title")) {
                title = xml.readElementText().trimmed().left(300);
            } else if (name == QLatin1String("location")) {
                const QString target = resolve_entry(xml.readElementText(), base);
                if (!target.isEmpty()) out->add(target, title);
            }
        } else if (xml.isEndElement() && xml.name() == QLatin1String("track")) {
            title.clear();
        }
    }
    if (xml.hasError() && xml.error() != QXmlStreamReader::PrematureEndOfDocumentError)
        return ("invalid XSPF playlist: " + file).toStdString();
    return {};
}

// WPL (Windows Media Player) — casu/playlist.py _parse_wpl_entries.
std::string PlaylistModel::load_wpl(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QDir base = QFileInfo(file).absoluteDir();
    QXmlStreamReader xml(&f);
    out->clear();
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement() && xml.name() == QLatin1String("media")) {
            const QString src = attr_ci(xml.attributes(), QStringLiteral("src"));
            if (src.isEmpty()) continue;
            QString t = attr_ci(xml.attributes(), QStringLiteral("title")).trimmed().left(300);
            const QString target = resolve_entry(src, base);
            if (!target.isEmpty()) out->add(target, t);
        }
    }
    if (xml.hasError() && xml.error() != QXmlStreamReader::PrematureEndOfDocumentError)
        return ("invalid WPL playlist: " + file).toStdString();
    return {};
}

// JSPF (JSON XSPF) — casu/playlist.py _parse_jspf_entries.
std::string PlaylistModel::load_jspf(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QByteArray raw = f.readAll();
    const QDir base = QFileInfo(file).absoluteDir();
    out->clear();
    casu::JsonValue doc;
    try {
        doc = casu::parse_json(raw.constData(), static_cast<std::size_t>(raw.size()));
    } catch (const casu::JsonError& e) {
        return ("invalid JSPF playlist: " + file).toStdString();
    }
    const casu::JsonValue* playlist = doc.is_object() ? doc.find("playlist") : nullptr;
    const casu::JsonValue* tracks = nullptr;
    if (playlist && playlist->is_object()) tracks = playlist->find("track");
    if (!tracks) tracks = doc.is_object() ? doc.find("track") : nullptr;
    if (tracks && tracks->is_array()) {
        for (const casu::JsonValue& track : tracks->as_array().items) {
            if (!track.is_object()) continue;
            const casu::JsonValue* tv = track.find("title");
            QString title = tv && tv->is_string()
                ? QString::fromStdString(tv->as_string()).trimmed().left(300) : QString();
            const casu::JsonValue* loc = track.find("location");
            if (loc && loc->is_array()) {
                for (const casu::JsonValue& item : loc->as_array().items) {
                    if (!item.is_string()) continue;
                    const QString target = resolve_entry(QString::fromStdString(item.as_string()), base);
                    if (!target.isEmpty()) out->add(target, title);
                }
            } else if (loc && loc->is_string()) {
                const QString target = resolve_entry(QString::fromStdString(loc->as_string()), base);
                if (!target.isEmpty()) out->add(target, title);
            }
        }
    }
    return {};
}

// ASX/WMX/WVX — casu/playlist.py _parse_asx_entries.
std::string PlaylistModel::load_asx(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QDir base = QFileInfo(file).absoluteDir();
    QXmlStreamReader xml(&f);
    out->clear();
    QSet<QString> seen;

    struct AsxSource { QString title; QString target; };
    QVector<AsxSource> collected;

    // Walk <entry> nodes. For each, capture its <title> then all descendant
    // <ref href> and <param name=url value> as sources.
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement()) {
            const QString name = xml.name().toString().toLower();
            if (name != "entry") continue;
            QString entryTitle;
            QVector<QString> sources;
            while (!xml.atEnd()) {
                xml.readNext();
                if (xml.isEndElement() && xml.name().toString().toLower() == "entry") break;
                if (!xml.isStartElement()) continue;
                const QString cname = xml.name().toString().toLower();
                if (cname == "title") {
                    entryTitle = xml.readElementText().trimmed().left(300);
                } else if (cname == "ref") {
                    const QString href = attr_ci(xml.attributes(), QStringLiteral("href"));
                    if (!href.isEmpty()) sources.append(href);
                } else if (cname == "param") {
                    const QString pname = attr_ci(xml.attributes(), QStringLiteral("name")).toLower();
                    const QString pval = attr_ci(xml.attributes(), QStringLiteral("value"));
                    if (pname == "url" && !pval.isEmpty()) sources.append(pval);
                }
            }
            for (const QString& s : sources) {
                const QString target = resolve_entry(s, base);
                if (!target.isEmpty() && !seen.contains(target)) {
                    seen.insert(target);
                    collected.append({entryTitle, target});
                }
            }
        }
    }

    // Fall back to root-level <ref href> only if no entry produced anything.
    if (collected.isEmpty()) {
        QXmlStreamReader xml2(&f);
        while (!xml2.atEnd()) {
            xml2.readNext();
            if (xml2.isStartElement() && xml2.name().toString().toLower() == "ref") {
                const QString href = attr_ci(xml2.attributes(), QStringLiteral("href"));
                const QString target = resolve_entry(href, base);
                if (!target.isEmpty() && !seen.contains(target)) {
                    seen.insert(target);
                    collected.append({QString(), target});
                }
            }
        }
    }

    for (const AsxSource& s : collected) out->add(s.target, s.title);
    if (xml.hasError() && xml.error() != QXmlStreamReader::PrematureEndOfDocumentError)
        return ("invalid ASX playlist: " + file).toStdString();
    return {};
}

// RMP (RealMedia metafile, XML) — casu/playlist.py _parse_rmp_entries; on
// parse failure falls back to RAM (plain text).
std::string PlaylistModel::load_rmp(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QByteArray raw = f.readAll();
    const QDir base = QFileInfo(file).absoluteDir();
    out->clear();
    QXmlStreamReader xml(raw);
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement()) {
            const QString name = xml.name().toString().toLower();
            const bool isRef = name.endsWith("ref") || name == "audio" ||
                               name == "video" || name == "media" || name == "entry";
            if (isRef) {
                QString src = attr_ci(xml.attributes(), QStringLiteral("src"));
                if (src.isEmpty()) src = attr_ci(xml.attributes(), QStringLiteral("href"));
                const QString target = resolve_entry(src, base);
                if (!target.isEmpty()) out->add(target, QString());
            }
        }
    }
    if (xml.hasError() && xml.error() != QXmlStreamReader::PrematureEndOfDocumentError)
        return load_ram(file, out);  // not XML → treat as RAM text
    return {};
}

// RAM (RealAudio metafile, plain text) — casu/playlist.py _parse_ram_entries.
std::string PlaylistModel::load_ram(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    const QDir base = QFileInfo(file).absoluteDir();
    out->clear();
    while (!ts.atEnd()) {
        QString line = ts.readLine().trimmed();
        if (line.isEmpty() || line.startsWith('#')) continue;
        const QString target = resolve_entry(line, base);
        if (!target.isEmpty()) out->add(target, QString());
    }
    return {};
}

// MPCASU JSON — casu/playlist.py PlaylistModel.from_payload
// ({ "version": 1, "items": [...] }).
std::string PlaylistModel::load_mpcasu_json(const QString& file, PlaylistModel* out) {
    QFile f(file);
    if (!f.open(QIODevice::ReadOnly))
        return ("could not open playlist: " + file).toStdString();
    const QByteArray raw = f.readAll();
    const QDir base = QFileInfo(file).absoluteDir();
    out->clear();
    casu::JsonValue doc;
    try {
        doc = casu::parse_json(raw.constData(), static_cast<std::size_t>(raw.size()));
    } catch (const casu::JsonError& e) {
        return ("invalid playlist document: " + file).toStdString();
    }
    if (!doc.is_object() || !doc.find("items") || !doc.find("items")->is_array())
        return ("unsupported playlist document: " + file).toStdString();
    const casu::JsonValue* version = doc.find("version");
    if (!version || !version->is_int() || version->as_int() != 1)
        return ("unsupported playlist document: " + file).toStdString();
    for (const casu::JsonValue& item : doc.find("items")->as_array().items) {
        if (!item.is_string()) continue;
        const QString target = resolve_entry(QString::fromStdString(item.as_string()), base);
        if (!target.isEmpty()) out->add(target, QString());
    }
    return {};
}

// casu/playlist.py save_playlist_file: #EXTM3U header, blank line, then one
// path per line — no #EXTINF lines (titles are not persisted).
std::string PlaylistModel::save_m3u(const QString& file, const PlaylistModel& model) {
    QFile f(file);
    // No QIODevice::Text: Python write_text() emits bare LF — byte parity.
    if (!f.open(QIODevice::WriteOnly))
        return ("could not write playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    ts << "#EXTM3U\n\n";
    for (const PlaylistItem& item : model.items())
        ts << item.path << "\n";
    return {};
}

// casu/playlist.py save_playlist_file: [playlist], NumberOfEntries first,
// FileN=/TitleN= pairs with Title = filename, Version=2 last.
std::string PlaylistModel::save_pls(const QString& file, const PlaylistModel& model) {
    QFile f(file);
    // No QIODevice::Text: bare LF for byte parity with save_playlist_file.
    if (!f.open(QIODevice::WriteOnly))
        return ("could not write playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    ts << "[playlist]\n";
    ts << "NumberOfEntries=" << model.items().size() << "\n";
    for (int i = 0; i < model.items().size(); ++i) {
        ts << "File" << (i + 1) << "=" << model.items()[i].path << "\n";
        ts << "Title" << (i + 1) << "=" << display_title_for_path(model.items()[i].path) << "\n";
    }
    ts << "Version=2\n";
    return {};
}

// casu/playlist.py save_playlist_file XSPF branch.
namespace {
QString xml_escape(const QString& value) {
    QString out = value;
    out.replace(QLatin1Char('&'), QStringLiteral("&amp;"));
    out.replace(QLatin1Char('<'), QStringLiteral("&lt;"));
    out.replace(QLatin1Char('>'), QStringLiteral("&gt;"));
    out.replace(QLatin1Char('"'), QStringLiteral("&quot;"));
    return out;
}
}  // namespace

std::string PlaylistModel::save_xspf(const QString& file, const PlaylistModel& model) {
    QFile f(file);
    if (!f.open(QIODevice::WriteOnly))
        return ("could not write playlist: " + file).toStdString();
    QTextStream ts(&f);
    ts.setEncoding(QStringConverter::Utf8);
    ts << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    ts << "<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\">\n";
    ts << "  <trackList>\n";
    for (const PlaylistItem& item : model.items()) {
        const QString title = display_title_for_path(item.path);
        ts << "    <track>\n";
        ts << "      <location>" << xml_escape(item.path) << "</location>\n";
        ts << "      <title>" << xml_escape(title) << "</title>\n";
        ts << "    </track>\n";
    }
    ts << "  </trackList>\n";
    ts << "</playlist>\n";
    return {};
}

// casu/playlist.py save_playlist_file fallback: MPCASU JSON payload
// {"version": 1, "items": [...]}.
std::string PlaylistModel::save_json(const QString& file, const PlaylistModel& model) {
    QJsonArray items;
    for (const PlaylistItem& item : model.items()) items.append(item.path);
    QJsonObject payload;
    payload.insert(QStringLiteral("version"), 1);
    payload.insert(QStringLiteral("items"), items);
    QFile f(file);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text))
        return ("could not write playlist: " + file).toStdString();
    const QByteArray data = QJsonDocument(payload).toJson(QJsonDocument::Indented);
    if (f.write(data) != data.size())
        return ("could not write playlist: " + file).toStdString();
    return {};
}

// Format-preserving dispatch mirroring save_playlist_file: extension decides,
// unknown extensions get the JSON payload (never silently M3U content).
std::string PlaylistModel::save_file(const QString& file, const PlaylistModel& model) {
    const QString lower = file.toLower();
    if (lower.endsWith(".m3u") || lower.endsWith(".m3u8")) return save_m3u(file, model);
    if (lower.endsWith(".pls")) return save_pls(file, model);
    if (lower.endsWith(".xspf")) return save_xspf(file, model);
    return save_json(file, model);
}

bool PlaylistModel::looks_like_playlist(const QString& path) {
    QString lower = path.toLower();
    return lower.endsWith(".m3u") || lower.endsWith(".m3u8") || lower.endsWith(".pls") ||
           lower.endsWith(".xspf") || lower.endsWith(".wpl") || lower.endsWith(".jspf") ||
           lower.endsWith(".asx") || lower.endsWith(".wmx") || lower.endsWith(".wvx") ||
           lower.endsWith(".rmp") || lower.endsWith(".ram") || lower.endsWith(".json");
}

// ---- Pure queue-group semantics ------------------------------------------

namespace {
// Entries of one playlist file (empty on error), resolved like the loader.
QStringList playlist_file_entries(const QString& path) {
    PlaylistModel tmp;
    if (!path.isEmpty() && QFileInfo::exists(path))
        if (PlaylistModel::load_file(path, &tmp).empty()) {
            QStringList entries;
            for (const PlaylistItem& item : tmp.items()) entries.append(item.path);
            return entries;
        }
    return {};
}
}  // namespace

QVector<QString> playlist_logical_sequence(const QVector<PlaylistItem>& items) {
    QVector<QString> seq;
    for (const PlaylistItem& item : items) {
        if (item.is_playlist) {
            const QStringList entries = playlist_file_entries(item.path);
            for (const QString& entry : entries) seq.append(entry);
            continue;
        }
        seq.append(item.path);
    }
    return seq;
}

int playlist_row_to_seq(const QVector<PlaylistItem>& items, int row) {
    if (row < 0 || row >= items.size()) return -1;
    int pos = 0;
    for (int i = 0; i < row; ++i) {
        if (items[i].is_playlist)
            pos += playlist_file_entries(items[i].path).size();
        else
            pos += 1;
    }
    return pos;
}

int playlist_seq_owner_row(const QVector<PlaylistItem>& items, int target) {
    if (target < 0) return -1;
    int seen = 0;
    for (int i = 0; i < items.size(); ++i) {
        int count = 1;
        if (items[i].is_playlist)
            count = qMax(1, playlist_file_entries(items[i].path).size());
        if (target < seen + count) return i;
        seen += count;
    }
    return -1;
}

QStringList playlist_group_paths(const QVector<PlaylistItem>& items) {
    QStringList groups;
    for (const PlaylistItem& item : items)
        if (item.is_playlist) groups.append(item.path);
    return groups;
}

QString playlist_containing_playlist(const QVector<PlaylistItem>& items,
                                     const QString& entry) {
    for (const PlaylistItem& item : items) {
        if (!item.is_playlist) continue;
        if (playlist_file_entries(item.path).contains(entry)) return item.path;
    }
    return QString();
}

PlaylistBatchPlan playlist_batch_plan(const QStringList& paths) {
    PlaylistBatchPlan plan;
    // covered: entries of every playlist chosen in the SAME batch.
    QStringList covered;
    for (const QString& p : paths) {
        if (!looks_remote(p) && PlaylistModel::looks_like_playlist(p)
            && QFileInfo::exists(p)) {
            for (const QString& entry : playlist_file_entries(p)) covered.append(entry);
        }
    }
    for (const QString& p : paths) {
        if (plan.rows.contains(p)) continue;  // dedup within the batch
        const bool is_remote = looks_remote(p);
        if (!is_remote && PlaylistModel::looks_like_playlist(p) && QFileInfo::exists(p)) {
            plan.rows.append(p);  // ONE visible group row, input position kept
            continue;
        }
        if (is_remote) {  // URLs queue as-is (even .m3u8 HLS streams!)
            plan.rows.append(p);
            continue;
        }
        if (!QFileInfo::exists(p)) continue;          // existing_only parity
        if (covered.contains(p)) continue;            // child of a batch playlist
        plan.rows.append(p);
    }
    return plan;
}

}  // namespace mpcasu
