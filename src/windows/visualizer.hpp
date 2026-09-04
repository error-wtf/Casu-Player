// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Visualizer widget — bounded waveform over decoded PCM:
// local files are decoded once via an ffmpeg s16le/44100 Hz mono pipe
// (decode_all_pcm), network streams feed a live ~40 Hz pipe into a ring
// buffer; the oscilloscope is downsampled to 64-128 visible points.
// CPU-throttle parity: ticking only while visible AND playing.
#pragma once
#include <QProcess>
#include <QTimer>
#include <QWidget>

#include <functional>
#include <vector>

class QPixmap;

namespace mpcasu {

class VisualizerWidget final : public QWidget {
public:
    explicit VisualizerWidget(QWidget* parent = nullptr);

    void set_playing(bool playing);
    void set_active(bool active);
    void set_mode(const QString& mode);
    void set_cover(const QPixmap* pixmap);  // borrowed; drawn centered when set

    // PCM sources (mutually exclusive). Both use a QProcess pipe drained
    // by the tick timer — NO background threads (a detached decode thread
    // kept the process alive on exit under Wine).
    void set_audio_file(const QString& path);   // full decode via pipe
    void set_stream_url(const QString& url);    // live s16le pipe (~40 Hz)
    void clear_audio();
    // Current playback position in seconds (playhead for waveform windows).
    void set_position_provider(std::function<double()> provider) {
        position_ = std::move(provider);
    }

protected:
    void paintEvent(QPaintEvent* event) override;
    bool event(QEvent* event) override;

private:
    void tick();
    void compute_frame();
    QVector<double> wave_samples(int points) const;
    const float* ring_tail(std::size_t* count) const;

    QTimer timer_;
    QProcess* pipe_ = nullptr;        // file-decode OR live stream pipe
    bool pipe_is_live_ = false;
    bool playing_ = false;
    bool active_ = true;
    bool visible_ = true;
    QString mode_ = "waveform";
    double phase_ = 0.0;
    QVector<double> current_wave_;
    const QPixmap* cover_ = nullptr;  // non-owning; owned by MainWindow

    // Decoded audio state.
    QString pcm_source_;
    std::vector<float> pcm_;          // mono samples in [-1,1] (file mode)
    std::vector<float> stream_ring_;  // live ring buffer (stream mode)
    std::size_t ring_write_pos_ = 0;
    int sample_rate_ = 44100;
    std::function<double()> position_;
    void start_pipe(const QStringList& args, bool live);
    void drain_pipe();
};

}  // namespace mpcasu
