// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#include "youtube_proxy.hpp"

#include "casu/network/range.hpp"

#include <QFile>
#include <QHostAddress>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRandomGenerator>
#include <QTcpServer>
#include <QTcpSocket>

namespace mpcasu {

namespace {
constexpr qint64 kChunk = 256 * 1024;

bool parse_request(QByteArray data, QByteArray* method, QByteArray* path,
                   QMap<QByteArray, QByteArray>* headers) {
    int eol = data.indexOf("\r\n");
    if (eol < 0) return false;
    QByteArray request_line = data.left(eol);
    QList<QByteArray> parts = request_line.split(' ');
    if (parts.size() < 2) return false;
    *method = parts[0].toUpper();
    *path = parts[1];
    int pos = eol + 2;
    while (pos < data.size()) {
        int nl = data.indexOf("\r\n", pos);
        if (nl < 0) break;
        QByteArray line = data.mid(pos, nl - pos);
        pos = nl + 2;
        if (line.isEmpty()) break;
        int colon = line.indexOf(':');
        if (colon > 0)
            (*headers)[line.left(colon).trimmed().toLower()] = line.mid(colon + 1).trimmed();
    }
    return true;
}
}  // namespace

class YoutubeProxy::Connection : public QObject {
public:
    Connection(YoutubeProxy* proxy, QTcpSocket* socket)
        : proxy_(proxy), socket_(socket) {
        connect(socket_, &QTcpSocket::readyRead, this, &Connection::on_ready);
        connect(socket_, &QTcpSocket::disconnected, this, &Connection::cleanup);
    }

private:
    void cleanup() {
        if (reply_) reply_->abort();
        if (file_ && file_->isOpen()) file_->close();
        socket_->deleteLater();
        deleteLater();
    }

    void on_ready() {
        if (handled_) return;
        buffer_.append(socket_->readAll());
        QByteArray method, path;
        QMap<QByteArray, QByteArray> headers;
        if (!parse_request(buffer_, &method, &path, &headers)) {
            if (buffer_.size() > 16 * 1024) {
                socket_->write("HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n");
                handled_ = true;
                socket_->flush();
                socket_->disconnectFromHost();
            }
            return;
        }
        handled_ = true;
        QString prefix = "/" + proxy_->token_ + "/";
        if (path != prefix.toUtf8() + "media") {
            send_error(404, "not found");
            return;
        }
        if (method != "GET" && method != "HEAD") {
            send_error(405, "method not allowed");
            return;
        }
        const bool head = method == "HEAD";
        QByteArray range = headers.value("range", "bytes=0-");
        if (proxy_->remote_) serve_remote(range, head);
        else serve_local(range, head);
    }

    void serve_local(const QByteArray& range_header, bool head) {
        QString err;
        if (!file_) {
            file_ = new QFile(proxy_->local_file_, this);
            if (!file_->open(QIODevice::ReadOnly)) {
                send_error(404, "media file missing");
                return;
            }
        }
        const qint64 size = file_->size();
        casu::network::range::ParsedRange r =
            casu::network::range::parse_bytes_range(range_header.constData(), size);
        if (!r.ok) {
            send_error(400, "invalid range");
            return;
        }
        if (r.unsatisfiable) {
            QByteArray body = "HTTP/1.1 416 Requested Range Not Satisfiable\r\n";
            body += "Content-Range: " + QByteArray(casu::network::range::unsatisfied_range_header(size).c_str()) + "\r\n";
            body += "Content-Length: 0\r\nConnection: close\r\n\r\n";
            socket_->write(body);
            socket_->flush();
            socket_->disconnectFromHost();
            return;
        }
        const qint64 len = r.end - r.start + 1;
        file_->seek(r.start);
        QByteArray head_b = "HTTP/1.1 ";
        if (r.start == 0 && r.end == size - 1) {
            head_b += "200 OK\r\n";
        } else {
            head_b += "206 Partial Content\r\n";
            head_b += "Content-Range: " + QByteArray(casu::network::range::content_range_header(r.start, r.end, size).c_str()) + "\r\n";
        }
        head_b += "Content-Length: " + QByteArray::number(len) + "\r\n";
        head_b += "Accept-Ranges: bytes\r\n";
        head_b += "Cache-Control: no-store\r\n";
        head_b += "Connection: close\r\n\r\n";
        socket_->write(head_b);
        if (head) {
            socket_->flush();
            socket_->disconnectFromHost();
            return;
        }
        remaining_ = len;
        pump_local();
    }

    void pump_local() {
        if (remaining_ <= 0) {
            socket_->flush();
            socket_->disconnectFromHost();
            return;
        }
        const qint64 n = qMin<qint64>(remaining_, kChunk);
        QByteArray chunk = file_->read(n);
        socket_->write(chunk);
        remaining_ -= chunk.size();
        if (socket_->bytesToWrite() > 2 * kChunk) {
            disconnect(socket_, &QTcpSocket::readyRead, this, nullptr);
            socket_->waitForBytesWritten(2000);
            connect(socket_, &QTcpSocket::readyRead, this, &Connection::on_ready);
        }
        pump_local();
    }

    void serve_remote(const QByteArray& range_header, bool head) {
        QUrl url(proxy_->upstream_url_);
        QNetworkRequest req(url);
        req.setHeader(QNetworkRequest::UserAgentHeader,
                      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0");
        req.setRawHeader("Accept", "*/*");
        req.setRawHeader("Accept-Language", "en-US,en;q=0.5");
        req.setRawHeader("Accept-Encoding", "identity");
        req.setRawHeader("Referrer-Policy", "no-referrer");
        if (!range_header.isEmpty())
            req.setRawHeader("Range", range_header);
        if (head)
            req.setRawHeader("X-Head", "1");
        reply_ = proxy_->nam_->get(req);
        connect(reply_, &QNetworkReply::readyRead, this, &Connection::pump_remote);
        connect(reply_, &QNetworkReply::finished, this, [this, range_header, head] {
            if (!reply_) return;
            const int status = reply_->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            if ((status == 403 || status == 410) && proxy_->refresh_ && !refreshed_) {
                refreshed_ = true;
                QString fresh;
                try {
                    fresh = proxy_->refresh_();
                } catch (...) {
                }
                if (!fresh.isEmpty() && fresh.startsWith("http")) {
                    proxy_->upstream_url_ = fresh;
                    reply_->abort();
                    reply_->deleteLater();
                    reply_ = nullptr;
                    headers_sent_ = false;
                    serve_remote(range_header, head);
                    return;
                }
            }
            // Send the response head as soon as we have one (readyRead may
            // already have pumped body bytes; only write the head once).
            if (!headers_sent_) {
                headers_sent_ = true;
                QByteArray hb = "HTTP/1.1 " + QByteArray::number(status) + " ";
                hb += reply_->attribute(QNetworkRequest::HttpReasonPhraseAttribute).toByteArray() + "\r\n";
                const qint64 cl = reply_->header(QNetworkRequest::ContentLengthHeader).toLongLong();
                if (cl > 0) hb += "Content-Length: " + QByteArray::number(cl) + "\r\n";
                const QByteArray cr = reply_->rawHeader("Content-Range");
                if (!cr.isEmpty()) hb += "Content-Range: " + cr + "\r\n";
                const QByteArray ct = reply_->rawHeader("Content-Type");
                if (!ct.isEmpty()) hb += "Content-Type: " + ct + "\r\n";
                hb += "Accept-Ranges: bytes\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
                socket_->write(hb);
                socket_->flush();
            }
            // Drain any buffered body, then finish the response.
            pump_body();
            socket_->flush();
            socket_->disconnectFromHost();
        });
    }

    void pump_remote() {
        if (!reply_) return;
        if (!headers_sent_) {
            // readyRead fired before finished: emit the head now, then body.
            const int status = reply_->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            headers_sent_ = true;
            QByteArray hb = "HTTP/1.1 " + QByteArray::number(status) + " ";
            hb += reply_->attribute(QNetworkRequest::HttpReasonPhraseAttribute).toByteArray() + "\r\n";
            const qint64 cl = reply_->header(QNetworkRequest::ContentLengthHeader).toLongLong();
            if (cl > 0) hb += "Content-Length: " + QByteArray::number(cl) + "\r\n";
            const QByteArray cr = reply_->rawHeader("Content-Range");
            if (!cr.isEmpty()) hb += "Content-Range: " + cr + "\r\n";
            const QByteArray ct = reply_->rawHeader("Content-Type");
            if (!ct.isEmpty()) hb += "Content-Type: " + ct + "\r\n";
            hb += "Accept-Ranges: bytes\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
            socket_->write(hb);
            socket_->flush();
        }
        pump_body();
    }

    void pump_body() {
        if (!reply_) return;
        QByteArray chunk = reply_->read(kChunk);
        while (!chunk.isEmpty()) {
            socket_->write(chunk);
            chunk = reply_->read(kChunk);
        }
    }

    void send_error(int code, const char* reason) {
        QByteArray body = "HTTP/1.1 " + QByteArray::number(code) + " " + reason + "\r\n";
        body += "Content-Length: 0\r\nConnection: close\r\n\r\n";
        socket_->write(body);
        socket_->flush();
        socket_->disconnectFromHost();
    }

    YoutubeProxy* proxy_;
    QTcpSocket* socket_;
    QByteArray buffer_;
    bool handled_ = false;
    bool headers_sent_ = false;
    bool refreshed_ = false;
    QFile* file_ = nullptr;
    qint64 remaining_ = 0;
    QNetworkReply* reply_ = nullptr;
};

YoutubeProxy::YoutubeProxy(QObject* parent)
    : QObject(parent), nam_(new QNetworkAccessManager(this)) {}

YoutubeProxy::~YoutubeProxy() {
    stop();
}

bool YoutubeProxy::start_local(const QString& file_path, QString* error) {
    stop();
    QFile f(file_path);
    if (!f.open(QIODevice::ReadOnly)) {
        f.close();
        if (error) *error = "media file is not readable";
        return false;
    }
    f.close();
    local_file_ = file_path;
    remote_ = false;
    return bind_and_prepare(error);
}

bool YoutubeProxy::start_remote(const QString& media_url,
                                std::function<QString()> refresh, QString* error) {
    if (!media_url.startsWith("http://") && !media_url.startsWith("https://")) {
        if (error) *error = "resolved media URL is not HTTP";
        return false;
    }
    stop();
    upstream_url_ = media_url;
    refresh_ = std::move(refresh);
    remote_ = true;
    return bind_and_prepare(error);
}

void YoutubeProxy::stop() {
    if (server_) {
        server_->close();
        delete server_;
        server_ = nullptr;
    }
    token_.clear();
    media_url_.clear();
    upstream_url_.clear();
    local_file_.clear();
    remote_ = false;
    refresh_ = nullptr;
}

int YoutubeProxy::port() const {
    return server_ ? server_->serverPort() : -1;
}

bool YoutubeProxy::bind_and_prepare(QString* error) {
    server_ = new QTcpServer(this);
    if (!server_->listen(QHostAddress::LocalHost, 0)) {
        if (error) *error = "could not bind loopback port";
        delete server_;
        server_ = nullptr;
        return false;
    }
    const QString alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    token_.clear();
    for (int i = 0; i < 18; ++i)
        token_.append(alphabet.at(QRandomGenerator::global()->bounded(alphabet.size())));
    media_url_ = QString("http://127.0.0.1:%1/%2/media").arg(server_->serverPort()).arg(token_);
    connect(server_, &QTcpServer::newConnection, this, [this] {
        while (QTcpSocket* socket = server_->nextPendingConnection())
            new Connection(this, socket);
    });
    return true;
}

}  // namespace mpcasu
