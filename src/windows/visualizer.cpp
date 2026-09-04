// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Lightweight waveform-only visualizer (see header).
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define NOGDI
#include <windows.h>  // CREATE_NO_WINDOW: keep GUI child processes silent
#endif
#include "visualizer.hpp"

#include "casu/codec/tools.hpp"
#include "theme.hpp"

#include <QCoreApplication>
#include <QDir>
#include <QEvent>
#include <QFile>
#include <QPainter>
#include <QPainterPath>
#include <QPixmap>

#include <algorithm>
#include <cmath>
#include <fstream>

namespace mpcasu {

namespace {
constexpr int kWaveWindowSamples = 2048;
constexpr double kWaveWindowSeconds = 0.045;
}

VisualizerWidget::VisualizerWidget(QWidget* parent) : QWidget(parent) {
    setMinimumHeight(120);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    timer_.setInterval(33);  // ~30 fps; stream pipe reads here (~40 Hz class)
    connect(&timer_, &QTimer::timeout, this, &VisualizerWidget::tick);
    if (active_ && visible_) timer_.start();
}

void VisualizerWidget::set_playing(bool playing) {
    playing_ = playing;
    // CPU-throttle parity: freeze updates when paused.
    if (playing_ && active_ && visible_) timer_.start();
    else timer_.stop();
}

void VisualizerWidget::set_active(bool active) {
    active_ = active;
    if (!active_) timer_.stop();
    else if (visible_ && playing_) timer_.start();
    update();
}

bool VisualizerWidget::event(QEvent* event) {
    if (event->type() == QEvent::Show ||
        event->type() == QEvent::Hide) {
        visible_ = event->type() == QEvent::Show;
        // CPU-throttle parity: no ticking while hidden.
        if (visible_ && active_ && playing_) timer_.start();
        else if (!visible_) timer_.stop();
    }
    return QWidget::event(event);
}

void VisualizerWidget::set_mode(const QString& mode) {
    mode_ = mode;
    update();
}

void VisualizerWidget::set_cover(const QPixmap* pixmap) {
    cover_ = pixmap;
    update();
}

void VisualizerWidget::clear_audio() {
    if (pipe_) {
        if (pipe_->state() != QProcess::NotRunning) {
            pipe_->terminate();
            if (!pipe_->waitForFinished(2000)) pipe_->kill();
        }
        pipe_->deleteLater();
        pipe_ = nullptr;
    }
    pipe_is_live_ = false;
    pcm_.clear();
    stream_ring_.assign(stream_ring_.size(), 0.0f);
    ring_write_pos_ = 0;
    pcm_source_.clear();
    current_wave_.clear();
    update();
}

void VisualizerWidget::set_audio_file(const QString& path) {
    if (pcm_source_ == path && !pcm_.empty()) return;
    if (path.isEmpty() || !QFile::exists(path)) return;
    pcm_source_ = path;
    // decode_all_pcm parity: ffmpeg -> mono s16le @44100, drained by tick().
    start_pipe(QStringList{
        QStringLiteral("-nostdin"), QStringLiteral("-v"),
        QStringLiteral("error"), QStringLiteral("-i"), path,
        QStringLiteral("-map"), QStringLiteral("0:a:0"),
        QStringLiteral("-ac"), QStringLiteral("1"),
        QStringLiteral("-ar"), QStringLiteral("44100"),
        QStringLiteral("-f"), QStringLiteral("s16le"),
        QStringLiteral("-acodec"), QStringLiteral("pcm_s16le"),
        QStringLiteral("pipe:1")},
        false);
}

const float* VisualizerWidget::ring_tail(std::size_t* count) const {
    *count = stream_ring_.size();
    return stream_ring_.data();
}

void VisualizerWidget::set_stream_url(const QString& url) {
    if (url.isEmpty()) return;
    // Live pipe drained at ~40 Hz into the ring buffer.
    start_pipe(QStringList{
        QStringLiteral("-nostdin"), QStringLiteral("-v"),
        QStringLiteral("error"), QStringLiteral("-i"), url,
        QStringLiteral("-map"), QStringLiteral("0:a:0"),
        QStringLiteral("-ac"), QStringLiteral("1"),
        QStringLiteral("-ar"), QStringLiteral("44100"),
        QStringLiteral("-f"), QStringLiteral("s16le"),
        QStringLiteral("-acodec"), QStringLiteral("pcm_s16le"),
        QStringLiteral("pipe:1")},
        true);
}

void VisualizerWidget::tick() {
    phase_ += 0.06;
    drain_pipe();
    if (playing_) compute_frame();
    update();
}

void VisualizerWidget::compute_frame() {
    // Assemble the analysis tail: prefer decoded file PCM ending at playhead;
    // fall back to the live stream ring buffer's most recent samples.
    const double position = position_ ? position_() : 0.0;
    QVector<double> tail;
    if (!pcm_.empty()) {
        qint64 centre = static_cast<qint64>(position * sample_rate_);
        centre = std::clamp<qint64>(centre, 0,
                                    static_cast<qint64>(pcm_.size()) - 1);
        const qint64 start =
            std::max<qint64>(0, centre - static_cast<qint64>(kWaveWindowSamples));
        for (qint64 i = start; i <= centre &&
                                tail.size() < kWaveWindowSamples;
             ++i)
            tail.append(pcm_[static_cast<std::size_t>(i)]);
    } else if (!stream_ring_.empty()) {
        // Most recent samples up to write cursor (wrap-aware).
        const std::size_t n = stream_ring_.size();
        std::size_t count = std::min<std::size_t>(n, kWaveWindowSamples);
        tail.reserve(static_cast<int>(count));
        std::size_t idx =
            (ring_write_pos_ + n - count % n) % n;
        for (std::size_t i = 0; i < count; ++i) {
            tail.append(stream_ring_[(idx + i) % n]);
        }
    }
    if (tail.size() < 64) {
        current_wave_.clear();
        return;
    }

    // Wave only: most recent 45 ms, bounded by the visible widget width.
    if (mode_ != "off") {
        const int window_samples =
            std::max(64, static_cast<int>(sample_rate_ * kWaveWindowSeconds));
        QVector<double> wave_tail;
        const qint64 total = tail.size();
        const qint64 start =
            std::max<qint64>(0, total - window_samples);
        for (qint64 i = start; i < total; ++i)
            wave_tail.append(tail[int(i)]);
        const int points = qBound(64, std::max(1, width() / 6), 128);
        QVector<double> fresh;
        if (wave_tail.size() >= 32) {
            const double width = std::max(
                1.0, std::ceil(double(wave_tail.size()) / points));
            for (double i = 0; i < wave_tail.size(); i += width)
                fresh.append(wave_tail[int(i)]);
        }
        if (current_wave_.size() != fresh.size()) {
            current_wave_ = fresh;
        } else {
            for (int i = 0; i < fresh.size(); ++i)
                current_wave_[i] = 0.65 * current_wave_[i] + 0.35 * fresh[i];
        }
    }
}

QVector<double> VisualizerWidget::wave_samples(int points) const {
    Q_UNUSED(points);
    return current_wave_;
}


void VisualizerWidget::start_pipe(const QStringList& args, bool live) {
    clear_audio();
    const std::string exe = casu::codec::ffmpeg_path().empty()
                                ? std::string("ffmpeg")
                                : casu::codec::ffmpeg_path();
    pipe_ = new QProcess(this);
    pipe_->setProgram(QString::fromStdString(exe));
    pipe_->setArguments(args);
    pipe_->setProcessChannelMode(QProcess::SeparateChannels);
    pipe_is_live_ = live;
    if (live) {
        constexpr std::size_t kRingSeconds = 10;
        stream_ring_.assign(44100 * kRingSeconds, 0.0f);
        ring_write_pos_ = 0;
    }
#ifdef Q_OS_WIN
    // GUI app: never flash a console window for ffmpeg.
    pipe_->setCreateProcessArgumentsModifier(
        [](QProcess::CreateProcessArguments* a) { a->flags |= CREATE_NO_WINDOW; });
#endif
    pipe_->start();
}

// Read whatever the decoder produced since the last tick. File mode
// appends to pcm_ until EOF; live mode feeds the ring buffer.
void VisualizerWidget::drain_pipe() {
    if (!pipe_) return;
    const QByteArray chunk =
        pipe_->read(256 * 1024);
    if (chunk.isEmpty()) {
        if (!pipe_is_live_ &&
            pipe_->state() == QProcess::NotRunning &&
            pipe_->bytesAvailable() == 0) {
            pipe_->deleteLater();
            pipe_ = nullptr;
        }
        return;
    }
    const int16_t* samples =
        reinterpret_cast<const int16_t*>(chunk.constData());
    const qsizetype count = chunk.size() / 2;
    if (pipe_is_live_) {
        for (qsizetype i = 0; i < count; ++i) {
            stream_ring_[ring_write_pos_] =
                static_cast<float>(samples[i]) / 32768.0f;
            ring_write_pos_ = (ring_write_pos_ + 1) % stream_ring_.size();
        }
    } else {
        pcm_.reserve(pcm_.size() + static_cast<std::size_t>(count));
        for (qsizetype i = 0; i < count; ++i)
            pcm_.push_back(static_cast<float>(samples[i]) / 32768.0f);
    }
}

void VisualizerWidget::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    const Palette& P = mpcasu::palette();
    QPainter p(this);
    p.fillRect(rect(), QColor(P.stage));

    if (!active_ || mode_ == "off") {
        p.setPen(QColor(P.muted));
        p.drawText(rect(), Qt::AlignCenter,
                   QStringLiteral("Visualizer disabled"));
        return;
    }

    QRadialGradient bg(rect().center(), qMax(width(), height()) * 0.75);
    bg.setColorAt(0.0, QColor(P.bg));
    bg.setColorAt(1.0, QColor(P.stage));
    p.fillRect(rect(), bg);

    if (cover_ && !cover_->isNull()) {
        const int size = qBound(40, int(qMin(width(), height()) * 0.44), 480);
        const QPixmap scaled = cover_->scaled(size, size, Qt::KeepAspectRatio,
                                              Qt::SmoothTransformation);
        const int px = (width() - scaled.width()) / 2;
        const int py = (height() - scaled.height()) / 2;
        QPainterPath clip;
        clip.addRoundedRect(QRectF(px, py, scaled.width(), scaled.height()),
                            10.0, 10.0);
        p.save();
        p.setClipPath(clip);
        p.drawPixmap(px, py, scaled);
        p.restore();
    }

    if (!current_wave_.isEmpty()) {
        const int samples = current_wave_.size();
        QPolygonF wave;
        for (int i = 0; i < samples; ++i) {
            const double x = double(i) * width() /
                             std::max(1, samples - 1);
            const double y = current_wave_[i] * 0.5 * height() +
                             0.75 * height();
            wave.append(QPointF(x, y));
        }
        QColor scope(P.red);
        scope.setAlpha(0x88);
        p.setPen(QPen(scope, 2.0));
        p.drawPolyline(wave);
    }

    p.setPen(QColor(P.muted));
    QFont f = p.font();
    f.setPointSize(9);
    p.setFont(f);
    p.drawText(rect().adjusted(8, 0, -8, -4), Qt::AlignBottom | Qt::AlignLeft,
               pcm_.empty() && pipe_ == nullptr
                   ? QStringLiteral(
                         "Visualizer: open media to enable the waveform")
                   : QStringLiteral("Waveform · 30 FPS · decoded PCM"));
}

}  // namespace mpcasu
