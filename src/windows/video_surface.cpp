// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
#include "video_surface.hpp"

#include <QMouseEvent>
#include <QPainter>
#include <Qt>

namespace mpcasu {

VideoSurface::VideoSurface(QWidget* parent) : QWidget(parent) {
    setObjectName("VideoSurface");
    setAttribute(Qt::WA_NativeWindow, true);
    setAttribute(Qt::WA_OpaquePaintEvent, true);
    setAttribute(Qt::WA_NoSystemBackground, true);
    setAutoFillBackground(false);
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);
    setMinimumSize(320, 180);
}

void* VideoSurface::native_handle() {
    return reinterpret_cast<void*>(winId());
}

void VideoSurface::set_video_active(bool active) {
    if (video_active_ == active) return;
    video_active_ = active;
    update();
}

void VideoSurface::clear() {
    video_active_ = false;
    update();
}

void VideoSurface::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);
    if (video_active_) return;  // libVLC owns the surface; painting flickers
    QPainter painter(this);
    painter.fillRect(rect(), QColor("#000000"));
    painter.setPen(QColor("#2a2a32"));
    QFont font = painter.font();
    font.setPointSize(qBound(16, width() / 16, 46));
    font.setBold(true);
    painter.setFont(font);
    painter.drawText(rect(), Qt::AlignCenter, QStringLiteral("MPCASU"));
}

void VideoSurface::mouseDoubleClickEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        if (on_double_click) on_double_click();
        event->accept();
        return;
    }
    QWidget::mouseDoubleClickEvent(event);
}

void VideoSurface::wheelEvent(QWheelEvent* event) {
    if (on_wheel) {
        const int dy = event->angleDelta().y();
        if (dy != 0) on_wheel(dy > 0 ? 1 : -1);
        event->accept();
        return;
    }
    QWidget::wheelEvent(event);
}

void VideoSurface::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        if (on_click) on_click();
        event->accept();
        return;
    }
    QWidget::mousePressEvent(event);
}

}  // namespace mpcasu
