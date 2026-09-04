// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// VideoSurface — native window libVLC renders into. Qt never paints over the
// video (REQ-PLAYER-003): WA_NativeWindow, opaque paint event, no system
// background, auto-fill off. Overlays/captions must be hidden while video is
// active. No Q_OBJECT (cross build without host moc): input events are
// exposed as std::function callbacks.
#pragma once
#include <QWidget>

#include <functional>

namespace mpcasu {

class VideoSurface final : public QWidget {
public:
    explicit VideoSurface(QWidget* parent = nullptr);

    void set_video_active(bool active);
    bool is_video_active() const { return video_active_; }
    void clear();

    // HWND libVLC draws into; stable for the widget's lifetime.
    void* native_handle();

    std::function<void()> on_double_click;
    std::function<void()> on_click;
    std::function<void(int)> on_wheel;  // delta steps (+/-)

protected:
    void paintEvent(QPaintEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;

private:
    bool video_active_ = false;
};

}  // namespace mpcasu
