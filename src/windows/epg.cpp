// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Full port of casu/epg.py (see header for parity notes). Dependency-free
// scanners replicate the reference regexes/semantics exactly.
#include "epg.hpp"

#include <QDate>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <algorithm>
#include <functional>

namespace mpcasu {

namespace {

// casu/epg.py _bounded(): strip, reject NUL and over-long UTF-8.
QString bounded_checked(const QString& raw, const char* label, int maximum,
                        bool* failed) {
    QString text = raw.trimmed();
    *failed = false;
    if (text.contains(QChar(u'\0')) ||
        text.toUtf8().size() > maximum) {
        *failed = true;
        return {};
    }
    return text;
}

// _decode(): UTF-8 (BOM tolerated) with Latin-1 fallback.
QString decode_bytes(const QByteArray& raw, bool* ok) {
    *ok = true;
    QByteArray body = raw;
    if (body.startsWith("\xEF\xBB\xBF")) body.remove(0, 3);
    const QString utf8 = QString::fromUtf8(body);
    // Strict check: re-encoding must round-trip byte-exactly (fromUtf8 would
    // otherwise have inserted U+FFFD replacements).
    if (utf8.toUtf8() == body) return utf8;
    return QString::fromLatin1(body);  // latin-1 is total
}

namespace attr {

bool is_key_char(QChar c) {
    return c.isLetterOrNumber() || c == u'_' || c == u'-';
}
bool is_space(QChar c) {
    return c == u' ' || c == u'\t' || c == u'\n' || c == u'\r' ||
           c == u'\f' || c == u'\v';
}

// Port of the attribute regex
// ([A-Za-z0-9_-]+)=(?:"([^"]*)"|'([^']*)'|([^\s]+)).
void scan(const QString& line,
          const std::function<void(const QString&, const QString&)>& on_attr) {
    qint64 i = 0;
    const qint64 n = line.size();
    while (i < n) {
        if (line[int(i)] != u'=') {
            ++i;
            continue;
        }
        const qint64 key_end = i - 1;
        qint64 key_start = key_end;
        while (key_start >= 0 && is_key_char(line[int(key_start)])) --key_start;
        ++key_start;
        if (key_end < key_start ||
            (key_start > 0 && is_key_char(line[int(key_start - 1)]))) {
            ++i;
            continue;
        }
        const QString key =
            line.mid(int(key_start), int(key_end - key_start + 1));
        ++i;
        if (i >= n) break;
        QString value;
        const QChar quote = line[int(i)];
        if (quote == u'"' || quote == u'\'') {
            ++i;
            qint64 j = i;
            while (j < n && line[int(j)] != quote) ++j;
            if (j >= n) break;  // unterminated: regex cannot match
            value = line.mid(int(i), int(j - i));
            i = j + 1;
        } else {
            qint64 j = i;
            while (j < n && !is_space(line[int(j)])) ++j;
            value = line.mid(int(i), int(j - i));
            i = j;
        }
        on_attr(key, value);
    }
}

}  // namespace attr

// The five standard entities plus numeric references (ElementTree parity).
QString decode_entities(const QString& text) {
    if (!text.contains(u'&')) return text;
    QString out;
    out.reserve(text.size());
    qint64 i = 0;
    const qint64 n = text.size();
    while (i < n) {
        if (text[int(i)] != u'&') {
            out.append(text[int(i)]);
            ++i;
            continue;
        }
        const int semi = int(text.indexOf(u';', int(i) + 1));
        if (semi < 0 || semi - i > 12) {
            out.append(text[int(i)]);
            ++i;
            continue;
        }
        const QString body = text.mid(int(i) + 1, int(semi - i - 1));
        if (body == QStringLiteral("amp")) out.append(u'&');
        else if (body == QStringLiteral("lt")) out.append(u'<');
        else if (body == QStringLiteral("gt")) out.append(u'>');
        else if (body == QStringLiteral("quot")) out.append(u'"');
        else if (body == QStringLiteral("apos")) out.append(u'\'');
        else if (body.startsWith(u'#')) {
            bool ok = false;
            uint code = 0;
            if (body.size() > 1 &&
                (body.at(1) == u'x' || body.at(1) == u'X'))
                code = body.mid(2).toUInt(&ok, 16);
            else
                code = body.mid(1).toUInt(&ok, 10);
            if (ok && code > 0 && code <= 0x10FFFF)
                out.append(QChar::fromUcs4(char32_t(code)));
            else
                out.append(text.mid(int(i), semi - i + 1));
        } else {
            out.append(text.mid(int(i), semi - i + 1));
        }
        i = semi + 1;
    }
    return out;
}

bool scheme_allowed(const QString& scheme) {
    static const QStringList allowed = {
        "http", "https", "ftp", "ftps", "rtsp", "rtsps", "rtmp", "rtmps",
        "rtp", "udp", "srt", "rist", "smb", "mmsh", "mmst"};
    return allowed.contains(scheme);
}

QString url_scheme(const QString& value) {
    const qint64 colon = value.indexOf(u':');
    if (colon <= 0) return {};
    const QString scheme = value.left(int(colon)).toLower();
    if (!scheme.at(0).isLetter()) return {};
    for (const QChar c : scheme)
        if (!(c.isLetterOrNumber() || c == u'+' || c == u'-' || c == u'.'))
            return {};
    return scheme;
}

QString basename_of_url_path(const QString& location) {
    QString path = location;
    const qint64 scheme_end = path.indexOf(QStringLiteral("://"));
    if (scheme_end >= 0) {
        const qint64 slash = path.indexOf(u'/', int(scheme_end) + 3);
        path = slash >= 0 ? path.mid(int(slash)) : QStringLiteral("/");
    }
    const qint64 query = path.indexOf(u'?');
    if (query >= 0) path.truncate(int(query));
    return QFileInfo(path).fileName();
}

struct XmlTag {
    bool is_close = false;
    bool is_self_closing = false;
    QString name;
    QHash<QString, QString> attrs;
};

bool parse_tag_at(const QString& text, qint64 pos, qint64* next, XmlTag* tag,
                  bool* malformed) {
    *malformed = false;
    qint64 i = pos + 1;
    if (i >= text.size()) return false;
    if (text[int(i)] == u'!' || text[int(i)] == u'?') return false;
    tag->is_close = false;
    tag->is_self_closing = false;
    if (text[int(i)] == u'/') {
        tag->is_close = true;
        ++i;
    }
    const qint64 start = i;
    while (i < text.size() && !attr::is_space(text[int(i)]) &&
           text[int(i)] != u'>' && text[int(i)] != u'/')
        ++i;
    if (i >= text.size()) {
        *malformed = true;
        return false;
    }
    tag->name = text.mid(int(start), int(i - start));
    if (tag->name.contains(u'}'))
        tag->name = tag->name.section(u'}', 1);
    if (tag->name.contains(u':'))
        tag->name = tag->name.section(u':', 1);
    QString blob;
    while (i < text.size() && text[int(i)] != u'>') {
        if (text[int(i)] == u'"' || text[int(i)] == u'\'') {
            const QChar q = text[int(i)];
            blob.append(q);
            ++i;
            while (i < text.size() && text[int(i)] != q) {
                blob.append(text[int(i)]);
                ++i;
            }
            if (i >= text.size()) {
                *malformed = true;
                return false;
            }
            blob.append(q);
        } else {
            if (text[int(i)] == u'/') tag->is_self_closing = true;
            blob.append(text[int(i)]);
        }
        ++i;
    }
    if (i >= text.size()) {
        *malformed = true;
        return false;
    }
    *next = i + 1;
    attr::scan(blob, [&](const QString& key, const QString& value) {
        tag->attrs.insert(key.toCaseFolded(), decode_entities(value));
    });
    return true;
}

// First direct-child element's full inner text (itertext semantics).
QString child_inner_text(const QString& doc, qint64 from, qint64 to,
                         const QString& name, bool* found) {
    *found = false;
    qint64 cursor = from;
    int depth = 0;
    while (cursor < to) {
        const qint64 lt = doc.indexOf(u'<', int(cursor));
        if (lt < 0 || lt >= to) break;
        if (doc.mid(int(lt), 4) == QStringLiteral("<!--")) {
            const qint64 end = doc.indexOf(QStringLiteral("-->"), int(lt));
            if (end < 0 || end >= to) break;
            cursor = end + 3;
            continue;
        }
        XmlTag tag;
        qint64 next = 0;
        bool malformed = false;
        if (!parse_tag_at(doc, lt, &next, &tag, &malformed)) {
            cursor = lt + 1;
            continue;
        }
        if (tag.is_close) {
            if (depth == 0) break;
            --depth;
            cursor = next;
            continue;
        }
        if (depth == 0 &&
            tag.name.compare(name, Qt::CaseInsensitive) == 0) {
            // Collect pcdata up to this child's matching close tag.
            QString collected;
            qint64 inner_cursor = next;
            int inner_depth = 1;
            while (inner_cursor < to && inner_depth > 0) {
                const qint64 nlt =
                    doc.indexOf(u'<', int(inner_cursor));
                if (nlt < 0 || nlt >= to) break;
                collected += doc.mid(int(inner_cursor),
                                     int(nlt - inner_cursor));
                if (doc.mid(int(nlt), 4) == QStringLiteral("<!--")) {
                    const qint64 end =
                        doc.indexOf(QStringLiteral("-->"), int(nlt));
                    if (end < 0 || end >= to) break;
                    inner_cursor = end + 3;
                    continue;
                }
                XmlTag inner_tag;
                qint64 inner_next = 0;
                bool bad = false;
                if (!parse_tag_at(doc, nlt, &inner_next, &inner_tag, &bad)) {
                    inner_cursor = nlt + 1;
                    continue;
                }
                if (inner_tag.is_close) --inner_depth;
                else if (!inner_tag.is_self_closing) ++inner_depth;
                inner_cursor = inner_next;
            }
            *found = true;
            return decode_entities(collected);
        }
        if (!tag.is_self_closing) ++depth;
        cursor = next;
    }
    return {};
}

}  // namespace

// ---------------------------------------------------------------------------
// Extended-M3U
// ---------------------------------------------------------------------------
QString parse_m3u(const QByteArray& data, const QString& base_dir,
                  StreamCatalog* out) {
    if (!out) return QStringLiteral("internal error");
    if (qint64(data.size()) > kMaxPlaylistBytes)
        return QStringLiteral("stream playlist exceeds its safety limit");
    bool decoded_ok = false;
    const QString text = decode_bytes(data, &decoded_ok);
    const QStringList lines = text.split(u'\n');
    for (const QString& raw_line : lines) {
        QString probe = raw_line;
        probe.remove(u'\r');
        if (probe.toUtf8().size() > kMaxLineBytes)
            return QStringLiteral(
                "stream playlist line exceeds its safety limit");
    }
    out->channels.clear();
    out->epg_urls.clear();
    bool pending_valid = false;
    QHash<QString, QString> pending;
    QString pending_name;
    auto reset_pending = [&] {
        pending.clear();
        pending_valid = false;
        pending_name.clear();
    };
    for (const QString& raw_line : lines) {
        const QString line = raw_line.trimmed();
        if (line.isEmpty()) continue;
        const QString upper = line.toUpper();
        if (upper.startsWith(QStringLiteral("#EXTM3U"))) {
            attr::scan(line, [&](const QString& key, const QString& value) {
                const QString folded = key.toCaseFolded();
                if (folded == QStringLiteral("url-tvg") ||
                    folded == QStringLiteral("x-tvg-url") ||
                    folded == QStringLiteral("tvg-url")) {
                    bool failed = false;
                    const QString checked =
                        bounded_checked(value, "EPG URL", kMaxUrlBytes,
                                        &failed).trimmed();
                    if (failed)
                        out->epg_urls.append(QString());
                    const QStringList parts = checked.split(u',');
                    for (const QString& part : parts) {
                        const QString candidate = part.trimmed();
                        if (!candidate.isEmpty() &&
                            !out->epg_urls.contains(candidate))
                            out->epg_urls.append(candidate);
                    }
                }
            });
            out->epg_urls.removeAll(QString());
            continue;
        }
        if (upper.startsWith(QStringLiteral("#EXTINF:"))) {
            const qint64 comma = line.indexOf(u',');
            const QString head =
                comma >= 0 ? line.left(int(comma)) : line;
            const QString name_part =
                comma >= 0 ? line.mid(int(comma) + 1) : QString();
            pending.clear();
            pending_valid = true;
            bool failed = false;
            attr::scan(head, [&](const QString& key, const QString& value) {
                const QString item =
                    bounded_checked(value, "playlist attribute",
                                    kMaxTextBytes, &failed);
                if (!failed) pending.insert(key.toCaseFolded(), item);
            });
            if (failed)
                return QStringLiteral(
                    "playlist attribute exceeds its safety limit");
            pending_name = bounded_checked(name_part, "channel name",
                                           kMaxTextBytes, &failed);
            if (failed)
                return QStringLiteral("channel name exceeds its safety limit");
            continue;
        }
        if (line.startsWith(u'#')) continue;
        bool failed = false;
        const QString location =
            bounded_checked(line, "stream location", kMaxUrlBytes, &failed);
        if (failed)
            return QStringLiteral("stream location exceeds its safety limit");
        const QString scheme = url_scheme(location);
        if (!scheme.isEmpty() && !scheme_allowed(scheme)) {
            reset_pending();
            continue;
        }
        QString resolved = location;
        if (scheme.isEmpty() && !base_dir.isEmpty())
            resolved =
                QDir::cleanPath(base_dir + QDir::separator() + location);
        const QHash<QString, QString> attrs =
            pending_valid ? pending : QHash<QString, QString>{};
        QString name = pending_name;
        if (name.isEmpty()) name = attrs.value(QStringLiteral("tvg-name"));
        if (name.isEmpty()) name = basename_of_url_path(resolved);
        if (name.isEmpty()) name = QStringLiteral("Unnamed stream");
        const QString safe_name =
            bounded_checked(name, "channel name", kMaxTextBytes, &failed);
        if (failed)
            return QStringLiteral("channel name exceeds its safety limit");
        StreamChannel channel;
        channel.url = resolved;
        channel.name = safe_name;
        channel.epg_id = bounded_checked(
            attrs.value(QStringLiteral("tvg-id")), "EPG channel id",
            kMaxTextBytes, &failed);
        if (failed)
            return QStringLiteral("EPG channel id exceeds its safety limit");
        channel.group = bounded_checked(
            attrs.value(QStringLiteral("group-title")), "channel group",
            kMaxTextBytes, &failed);
        if (failed)
            return QStringLiteral("channel group exceeds its safety limit");
        channel.logo = bounded_checked(
            attrs.value(QStringLiteral("tvg-logo")), "channel logo",
            kMaxUrlBytes, &failed);
        if (failed)
            return QStringLiteral("channel logo exceeds its safety limit");
        out->channels.append(channel);
        reset_pending();
        if (out->channels.size() > kMaxChannels)
            return QStringLiteral("stream playlist exceeds %1 channels")
                .arg(kMaxChannels);
    }
    while (out->epg_urls.size() > 32) out->epg_urls.removeLast();
    return {};
}

QString load_m3u_file(const QString& path, StreamCatalog* out) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return QStringLiteral("stream playlist is unavailable");
    return parse_m3u(f.read(kMaxPlaylistBytes + 1),
                     QFileInfo(path).absolutePath(), out);
}

// ---------------------------------------------------------------------------
// XMLTV
// ---------------------------------------------------------------------------

// Port of parse_xmltv_time: YYYYMMDDHHMMSS[ ('+'|'-')HHMM | Z ] -> UTC ms.
static bool xmltv_time_ms(const QString& raw, qint64* out_ms) {
    QString text = raw.trimmed();
    if (text.contains(u'\0') || text.toUtf8().size() > 64) return false;
    static const QString digits = QStringLiteral("0123456789");
    qint64 pos = 0;
    if (text.length() < 14) return false;
    for (int i = 0; i < 14; ++i)
        if (!digits.contains(text.at(i))) return false;
    const int year = text.mid(0, 4).toInt();
    const int month = text.mid(4, 2).toInt();
    const int day = text.mid(6, 2).toInt();
    const int hour = text.mid(8, 2).toInt();
    const int minute = text.mid(10, 2).toInt();
    const int second = text.mid(12, 2).toInt();
    const QDate date(year, month, day);
    const QTime time(hour, minute, second);
    if (!date.isValid() || !time.isValid()) return false;
    qint64 offset_seconds = 0;
    QString rest = text.mid(14);
    rest = rest.trimmed();
    if (!rest.isEmpty()) {
        if (rest == QStringLiteral("Z") || rest == QStringLiteral("z")) {
            offset_seconds = 0;
        } else if (rest.length() == 5 &&
                   (rest.at(0) == u'+' || rest.at(0) == u'-')) {
            const int oh = rest.mid(1, 2).toInt();
            const int om = rest.mid(3, 2).toInt();
            if (!digits.contains(rest.at(1)) || !digits.contains(rest.at(2)) ||
                !digits.contains(rest.at(3)) || !digits.contains(rest.at(4)))
                return false;
            offset_seconds = (oh * 3600 + om * 60) *
                             (rest.at(0) == u'-' ? -1 : 1);
        } else {
            return false;
        }
    } else {
        rest = QString();
    }
    const QDateTime as_utc(QDate(year, month, day),
                           QTime(hour, minute, second), Qt::UTC);
    if (!as_utc.isValid()) return false;
    *out_ms = as_utc.toMSecsSinceEpoch() - offset_seconds * 1000LL;
    return true;
}

QString parse_xmltv_guide(const QByteArray& data, EpgGuide* out) {
    if (!out) return QStringLiteral("internal error");
    if (qint64(data.size()) > kMaxXmltvBytes)
        return QStringLiteral("XMLTV guide exceeds its safety limit");
    const QByteArray prefix = data.left(4096).toUpper();
    if (prefix.contains("<!DOCTYPE") || prefix.contains("<!ENTITY"))
        return QStringLiteral("XMLTV DTD/entities are not accepted");
    bool decoded_ok = false;
    const QString doc = decode_bytes(data, &decoded_ok);

    qint64 pos = 0;
    XmlTag root;
    bool root_found = false;
    while (true) {
        const qint64 lt = doc.indexOf(u'<', int(pos));
        if (lt < 0) break;
        if (doc.mid(int(lt), 4) == QStringLiteral("<!--")) {
            const qint64 end = doc.indexOf(QStringLiteral("-->"), int(lt));
            if (end < 0) break;
            pos = end + 3;
            continue;
        }
        if (doc.mid(int(lt), 2) == QStringLiteral("<?")) {
            const qint64 end = doc.indexOf(QStringLiteral("?>"), int(lt));
            if (end < 0) break;
            pos = end + 2;
            continue;
        }
        if (doc.mid(int(lt), 2) == QStringLiteral("<!")) break;
        qint64 next = 0;
        bool malformed = false;
        if (!parse_tag_at(doc, lt, &next, &root, &malformed)) break;
        root_found = true;
        pos = next;
        break;
    }
    if (!root_found || root.is_close ||
        root.name.compare(QStringLiteral("tv"), Qt::CaseInsensitive) != 0)
        return QStringLiteral("XMLTV root element must be tv");

    out->channel_names.clear();
    out->programmes.clear();

    qint64 cursor = pos;
    const qint64 n = doc.size();
    while (cursor < n) {
        const qint64 lt = doc.indexOf(u'<', int(cursor));
        if (lt < 0) break;
        if (doc.mid(int(lt), 4) == QStringLiteral("</tv")) break;
        if (doc.mid(int(lt), 4) == QStringLiteral("<!--")) {
            const qint64 end = doc.indexOf(QStringLiteral("-->"), int(lt));
            if (end < 0) break;
            cursor = end + 3;
            continue;
        }
        XmlTag tag;
        qint64 next = 0;
        bool malformed = false;
        if (!parse_tag_at(doc, lt, &next, &tag, &malformed)) {
            if (malformed)
                return QStringLiteral("XMLTV guide is malformed");
            ++cursor;
            continue;
        }
        const bool is_channel =
            tag.name.compare(QStringLiteral("channel"),
                             Qt::CaseInsensitive) == 0;
        const bool is_programme =
            tag.name.compare(QStringLiteral("programme"),
                             Qt::CaseInsensitive) == 0;
        if (!is_channel && !is_programme) {
            cursor = next;
            continue;
        }
        qint64 inner_begin = next;
        qint64 close_lt = -1;
        if (!tag.is_self_closing) {
            int depth = 1;
            qint64 inner = next;
            while (inner < n && depth > 0) {
                const qint64 nlt = doc.indexOf(u'<', int(inner));
                if (nlt < 0) break;
                if (doc.mid(int(nlt), 4) == QStringLiteral("<!--")) {
                    const qint64 end =
                        doc.indexOf(QStringLiteral("-->"), int(nlt));
                    if (end < 0) break;
                    inner = end + 3;
                    continue;
                }
                XmlTag inner_tag;
                qint64 inner_next = 0;
                bool bad = false;
                if (!parse_tag_at(doc, nlt, &inner_next, &inner_tag, &bad)) {
                    inner = nlt + 1;
                    continue;
                }
                if (inner_tag.is_close) {
                    --depth;
                    if (depth == 0) {
                        close_lt = nlt;
                        break;
                    }
                } else if (!inner_tag.is_self_closing) {
                    ++depth;
                }
                inner = inner_next;
            }
            if (close_lt < 0)
                return QStringLiteral("XMLTV guide is malformed");
            inner_begin = next;
        }

        if (is_channel) {
            const QString identifier =
                tag.attrs.value(QStringLiteral("id"));
            bool failed = false;
            const QString checked =
                bounded_checked(identifier, "XMLTV channel id",
                                kMaxTextBytes, &failed);
            if (failed)
                return QStringLiteral(
                    "XMLTV channel id exceeds its safety limit");
            if (!checked.isEmpty()) {
                bool found = false;
                const QString display = child_inner_text(
                    doc, inner_begin, close_lt < 0 ? n : close_lt,
                    QStringLiteral("display-name"), &found);
                const QString bounded_display = bounded_checked(
                    display, "XMLTV display-name", kMaxTextBytes, &found);
                if (found)
                    return QStringLiteral(
                        "XMLTV display-name exceeds its safety limit");
                out->channel_names.insert(
                    checked,
                    bounded_display.isEmpty() ? checked : bounded_display);
                if (out->channel_names.size() > kMaxChannels)
                    return QStringLiteral("XMLTV exceeds %1 channels")
                        .arg(kMaxChannels);
            }
            cursor = tag.is_self_closing ? next
                                         : doc.indexOf(u'>',
                                                       int(close_lt)) + 1;
            continue;
        }

        // programme
        const QString channel_id =
            tag.attrs.value(QStringLiteral("channel"));
        bool failed = false;
        const QString checked_channel = bounded_checked(
            channel_id, "XMLTV channel id", kMaxTextBytes, &failed);
        if (failed)
            return QStringLiteral("XMLTV channel id exceeds its safety limit");
        bool title_found = false;
        const QString title = child_inner_text(
            doc, inner_begin, close_lt < 0 ? n : close_lt,
            QStringLiteral("title"), &title_found);
        const QString checked_title =
            bounded_checked(title, "XMLTV title", kMaxTextBytes, &failed);
        if (failed)
            return QStringLiteral("XMLTV title exceeds its safety limit");
        if (checked_channel.isEmpty() || checked_title.isEmpty()) {
            cursor = tag.is_self_closing
                         ? next
                         : doc.indexOf(u'>', int(close_lt)) + 1;
            continue;
        }
        qint64 start_ms = 0;
        qint64 stop_ms = 0;
        if (!xmltv_time_ms(tag.attrs.value(QStringLiteral("start")),
                           &start_ms))
            return QStringLiteral("XMLTV timestamp is invalid");
        if (!xmltv_time_ms(tag.attrs.value(QStringLiteral("stop")), &stop_ms))
            return QStringLiteral("XMLTV timestamp is invalid");
        if (stop_ms <= start_ms) {
            cursor = tag.is_self_closing
                         ? next
                         : doc.indexOf(u'>', int(close_lt)) + 1;
            continue;
        }
        Programme programme;
        programme.channel_id = checked_channel;
        programme.start_ms = start_ms;
        programme.stop_ms = stop_ms;
        programme.title = checked_title;
        bool desc_found = false;
        programme.description = child_inner_text(
            doc, inner_begin, close_lt < 0 ? n : close_lt,
            QStringLiteral("desc"), &desc_found);
        programme.category = child_inner_text(
            doc, inner_begin, close_lt < 0 ? n : close_lt,
            QStringLiteral("category"), &desc_found);
        programme.description = bounded_checked(
            programme.description, "XMLTV desc", kMaxTextBytes, &desc_found);
        if (desc_found)
            return QStringLiteral("XMLTV desc exceeds its safety limit");
        programme.category =
            bounded_checked(programme.category, "XMLTV category",
                            kMaxTextBytes, &desc_found);
        if (desc_found)
            return QStringLiteral("XMLTV category exceeds its safety limit");
        out->programmes.append(std::move(programme));
        if (out->programmes.size() > kMaxProgrammes)
            return QStringLiteral("XMLTV exceeds %1 programmes")
                .arg(kMaxProgrammes);
        cursor = tag.is_self_closing ? next
                                     : doc.indexOf(u'>', int(close_lt)) + 1;
    }
    std::stable_sort(out->programmes.begin(), out->programmes.end(),
                     [](const Programme& a, const Programme& b) {
                         if (a.channel_id != b.channel_id)
                             return a.channel_id < b.channel_id;
                         if (a.start_ms != b.start_ms)
                             return a.start_ms < b.start_ms;
                         return a.stop_ms < b.stop_ms;
                     });
    return {};
}

QString load_xmltv_file(const QString& path, EpgGuide* out) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return QStringLiteral("XMLTV guide is unavailable");
    return parse_xmltv_guide(f.read(kMaxXmltvBytes + 1), out);
}

QString parse_xmltv(const QByteArray& data, StreamCatalog* catalog_out,
                    EpgGuide* guide_out) {
    EpgGuide guide;
    const QString err = parse_xmltv_guide(data, &guide);
    if (!err.isEmpty()) return err;
    if (guide_out) *guide_out = std::move(guide);
    if (catalog_out) {
        catalog_out->channels.clear();
        catalog_out->epg_urls.clear();
        for (auto it = guide.channel_names.constBegin();
             it != guide.channel_names.constEnd(); ++it) {
            StreamChannel channel;
            channel.url.clear();
            channel.name = it.value();
            channel.epg_id = it.key();
            catalog_out->channels.append(channel);
        }
    }
    return {};
}

// ---------------------------------------------------------------------------
// Guide queries
// ---------------------------------------------------------------------------
QVector<Programme> EpgGuide::schedule(const QString& channel_id,
                                      qint64 now_ms, int limit) const {
    const int maximum = std::max(1, std::min(200, limit));
    QVector<Programme> values;
    for (const Programme& item : programmes) {
        if (item.channel_id != channel_id || item.stop_ms <= now_ms) continue;
        values.append(item);
        if (values.size() >= maximum) break;
    }
    return values;
}

void EpgGuide::now_next(const QString& channel_id, qint64 now_ms,
                        const Programme** active,
                        const Programme** upcoming) const {
    *active = nullptr;
    *upcoming = nullptr;
    QVector<const Programme*> values;
    for (const Programme& item : programmes) {
        if (item.channel_id != channel_id || item.stop_ms <= now_ms) continue;
        values.append(&item);
    }
    for (const Programme* item : values)
        if (item->start_ms <= now_ms && now_ms < item->stop_ms) {
            *active = item;
            break;
        }
    for (const Programme* item : values) {
        if (item->start_ms >= now_ms && item != *active) {
            *upcoming = item;
            break;
        }
    }
}

}  // namespace mpcasu
