// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// RecordingController — full port of casu/recording.py MediaRecorder on top
// of an ffmpeg QProcess (argument arrays only):
//  - destination suffix whitelist {.mkv,.mp4,.mov,.ts,.m2ts,.webm,.ogg,
//    .mp3,.flac,.wav}, self-overwrite protection and source validation
//  - records to a hidden temporary file ".{stem}.recording-*" in the
//    destination directory and publishes ATOMICALLY after ffprobe
//    verification (audio/video stream required)
//  - maps metadata + chapters (-map_metadata 0 -map_chapters 0), stream copy
//  - SIGTERM-safe finish: ffmpeg's 255-on-term is not a failure; the probed
//    file is authoritative.
#pragma once
#include <QProcess>
#include <QString>

#include <functional>

namespace mpcasu {

class RecordingController final : public QObject {
public:
    explicit RecordingController(QObject* parent = nullptr);
    ~RecordingController() override;

    // Start recording `source` (path or URL) to `destination`. Returns false
    // + error when the source/format is invalid or ffmpeg could not start.
    bool start(const QString& source, const QString& destination,
               QString* error);
    void stop();  // graceful terminate -> verify -> atomic publish
    void kill();  // abort: kill process and DELETE the unverified temp file
    bool is_recording() const { return state_ == State::Recording; }
    QString output_path() const { return destination_; }

    enum class State { Idle, Starting, Recording, Stopping, Failed };
    State state() const { return state_; }

    std::function<void()> on_state_changed;
    // on_finished(published_path_or_empty, ok, detail)
    std::function<void(const QString&, bool, const QString&)> on_finished;

private:
    void handle_finished(int code, QProcess::ExitStatus status);

private:
    QString temporary_for(const QString& destination) const;
    void publish_async();
    void cleanup_temp();

    QProcess* proc_ = nullptr;
    State state_ = State::Idle;
    QString source_;
    QString destination_;
    QString temporary_;
};
}  // namespace mpcasu
