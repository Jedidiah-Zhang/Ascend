#include "waveform_widget.hpp"

#include <QMouseEvent>
#include <QPainter>
#include <QToolTip>
#include <QWheelEvent>

#include <algorithm>
#include <cmath>

namespace ascend::workbench {
namespace {

constexpr int kNameWidth = 116;
constexpr int kEventHeight = 18;
constexpr int kAxisHeight = 26;
constexpr int kRowMinHeight = 26;
constexpr int kRowMaxHeight = 150;

QString value_text(double value) {
    if (std::floor(value) == value && std::abs(value) < 9.0e15) {
        return QString::number(static_cast<qlonglong>(value));
    }
    return QString::number(value, 'g', 10);
}

}  // namespace

WaveformWidget::WaveformWidget(const UiTexts* texts, QWidget* parent) : QWidget(parent), texts_(texts) {
    setMinimumHeight(180);
    setMouseTracking(true);
}

void WaveformWidget::clear() {
    signals_.clear();
    events_.clear();
    view_valid_ = false;
    cursor_a_valid_ = false;
    cursor_b_valid_ = false;
    update();
}

void WaveformWidget::setSignals(QVector<Signal> rows) {
    signals_ = std::move(rows);
    if (view_auto_ || !view_valid_) {
        fit_view();
    } else {
        set_view(view_min_, view_max_);
    }
    const auto bounds = data_bounds();
    if (bounds.valid) {
        if (!cursor_a_valid_) cursor_a_ = bounds.min;
        if (cursor_b_auto_ || !cursor_b_valid_) cursor_b_ = bounds.max;
        cursor_a_valid_ = true;
        cursor_b_valid_ = true;
    }
    update();
    emit cursorsChanged();
}

void WaveformWidget::setEvents(QVector<Event> markers) {
    events_ = std::move(markers);
    update();
}

void WaveformWidget::setCheckpoint(std::optional<std::int64_t> frame, bool branch_zone) {
    checkpoint_ = frame;
    branch_zone_ = branch_zone;
    update();
}

QPoint WaveformWidget::framePosition(std::int64_t frame) const {
    const QRect wave = wave_rect();
    const double x = x_for_frame(static_cast<double>(frame), wave);
    return QPoint(static_cast<int>(std::lround(x)), wave.center().y());
}

QRect WaveformWidget::wave_rect() const {
    return QRect(kNameWidth, kEventHeight, std::max(1, width() - kNameWidth),
                 std::max(1, height() - kEventHeight - kAxisHeight));
}

WaveformWidget::Bounds WaveformWidget::data_bounds() const {
    Bounds bounds;
    const auto extend = [&bounds](std::int64_t frame) {
        if (!bounds.valid) {
            bounds.min = frame;
            bounds.max = frame;
            bounds.valid = true;
        } else {
            bounds.min = std::min(bounds.min, frame);
            bounds.max = std::max(bounds.max, frame);
        }
    };
    for (const auto& signal : signals_) {
        for (const auto& series : signal.series) {
            for (const auto& point : series.points) extend(point.frame);
        }
    }
    for (const auto& event : events_) extend(event.frame);
    return bounds;
}

void WaveformWidget::fit_view() {
    const auto bounds = data_bounds();
    if (!bounds.valid) {
        view_min_ = 0.0;
        view_max_ = 1.0;
        view_valid_ = false;
        return;
    }
    view_min_ = static_cast<double>(bounds.min);
    view_max_ = static_cast<double>(bounds.max);
    if (view_max_ - view_min_ < 1.0) view_max_ = view_min_ + 1.0;
    view_valid_ = true;
}

void WaveformWidget::set_view(double minimum, double maximum) {
    const auto bounds = data_bounds();
    if (!bounds.valid) return;
    const double data_min = static_cast<double>(bounds.min);
    const double data_max = static_cast<double>(bounds.max);
    double span = maximum - minimum;
    if (span < 1.0) span = 1.0;
    if (span >= data_max - data_min + 1.0) {
        minimum = data_min;
        maximum = minimum + std::max(1.0, data_max - data_min);
    } else {
        if (minimum < data_min) {
            maximum += data_min - minimum;
            minimum = data_min;
        }
        if (maximum > data_max) {
            minimum -= maximum - data_max;
            maximum = data_max;
        }
        minimum = std::max(minimum, data_min);
        maximum = std::min(maximum, data_max);
    }
    view_min_ = minimum;
    view_max_ = maximum;
    view_valid_ = true;
    update();
}

double WaveformWidget::x_for_frame(double frame, const QRect& wave) const {
    const double span = std::max(1e-9, view_max_ - view_min_);
    return wave.left() + (frame - view_min_) / span * wave.width();
}

double WaveformWidget::frame_for_x(double x, const QRect& wave) const {
    const double span = view_max_ - view_min_;
    return view_min_ + (x - wave.left()) / std::max(1.0, static_cast<double>(wave.width())) * span;
}

void WaveformWidget::paintEvent(QPaintEvent*) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing, true);
    const QRect wave = wave_rect();
    painter.fillRect(rect(), palette().window());
    painter.fillRect(wave, QColor(0xfb, 0xfb, 0xfb));
    if (branch_zone_ && checkpoint_.has_value()) {
        const auto bounds = data_bounds();
        if (bounds.valid) {
            const double left = x_for_frame(static_cast<double>(*checkpoint_), wave);
            const double right = x_for_frame(static_cast<double>(bounds.max), wave);
            const QRectF zone(left, wave.top(), std::max(0.0, right - left), wave.height());
            painter.fillRect(zone.intersected(QRectF(wave)), QColor(0x17, 0xa2, 0xb8, 14));
        }
    }

    if (signals_.isEmpty()) {
        painter.setPen(QColor(0x88, 0x88, 0x88));
        painter.drawText(wave, Qt::AlignCenter,
                         ui_text(*texts_, "workbench.waveform.no_samples", "No samples yet"));
        return;
    }

    // 时间网格与刻度
    const double span = view_max_ - view_min_;
    const double rough = span / 6.0;
    const double magnitude = std::pow(10.0, std::floor(std::log10(std::max(1.0, rough))));
    double step = magnitude;
    if (rough / magnitude > 5.0) {
        step = magnitude * 5.0;
    } else if (rough / magnitude > 2.0) {
        step = magnitude * 2.0;
    }
    const double first_tick = std::ceil(view_min_ / step) * step;
    painter.setPen(QColor(0xe4, 0xe4, 0xe4));
    for (double tick = first_tick; tick <= view_max_ + 1e-9; tick += step) {
        const double x = x_for_frame(tick, wave);
        painter.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()));
    }
    painter.setPen(QColor(0x99, 0x99, 0x99));
    painter.drawLine(wave.bottomLeft(), wave.bottomRight());
    for (double tick = first_tick; tick <= view_max_ + 1e-9; tick += step) {
        const double x = x_for_frame(tick, wave);
        painter.drawLine(QPointF(x, wave.bottom()), QPointF(x, wave.bottom() + 4));
        const double label_x = std::clamp(x, 30.0, static_cast<double>(width()) - 30.0);
        painter.drawText(QRectF(label_x - 30, wave.bottom() + 5, 60, kAxisHeight - 7),
                         Qt::AlignHCenter | Qt::AlignTop,
                         QString::number(static_cast<qlonglong>(std::llround(tick))));
    }

    // 信号行：阶梯线 + 端点标注
    const int rows = signals_.size();
    const int row_height = std::clamp(wave.height() / std::max(1, rows), kRowMinHeight, kRowMaxHeight);
    for (int row = 0; row < rows; ++row) {
        const int top = wave.top() + row * row_height;
        if (top >= wave.bottom()) break;
        const int bottom = std::min(top + row_height, wave.bottom());
        const auto& signal = signals_[row];
        double minimum = 0.0;
        double maximum = 1.0;
        bool has = false;
        for (const auto& series : signal.series) {
            for (const auto& point : series.points) {
                if (!point.numeric) continue;
                if (point.frame < view_min_ - 1.0 || point.frame > view_max_ + 1.0) continue;
                if (!has) {
                    minimum = maximum = point.value;
                    has = true;
                } else {
                    minimum = std::min(minimum, point.value);
                    maximum = std::max(maximum, point.value);
                }
            }
        }
        if (maximum - minimum < 1e-9) {
            minimum -= 1.0;
            maximum += 1.0;
        }
        const auto y_for = [&](double value) {
            const double ratio = (value - minimum) / (maximum - minimum);
            return bottom - ratio * static_cast<double>(bottom - top);
        };
        painter.save();
        painter.setClipRect(wave);
        for (const auto& series : signal.series) {
            painter.setPen(QPen(series.color, 1.4));
            const auto count = static_cast<std::size_t>(series.points.size());
            std::size_t begin = 0;
            while (begin < count && series.points[begin].frame < view_min_ - 1.0) ++begin;
            std::size_t end = begin;
            while (end < count && series.points[end].frame <= view_max_ + 1.0) ++end;
            if (end - begin > static_cast<std::size_t>(wave.width()) * 2) {
                // 密集区间按像素列聚合：绘制成本与像素数同阶，不随逻辑帧数增长。
                const auto columns = static_cast<std::size_t>(wave.width());
                std::vector<double> column_minimum(columns, 0.0);
                std::vector<double> column_maximum(columns, 0.0);
                std::vector<char> column_filled(columns, 0);
                for (std::size_t index = begin; index < end; ++index) {
                    const auto& point = series.points[index];
                    if (!point.numeric) continue;
                    const int column = std::clamp(
                        static_cast<int>(x_for_frame(static_cast<double>(point.frame), wave)) -
                            wave.left(),
                        0, wave.width() - 1);
                    const double y = y_for(point.value);
                    if (column_filled[static_cast<std::size_t>(column)] == 0) {
                        column_minimum[static_cast<std::size_t>(column)] = y;
                        column_maximum[static_cast<std::size_t>(column)] = y;
                        column_filled[static_cast<std::size_t>(column)] = 1;
                    } else {
                        column_minimum[static_cast<std::size_t>(column)] =
                            std::min(column_minimum[static_cast<std::size_t>(column)], y);
                        column_maximum[static_cast<std::size_t>(column)] =
                            std::max(column_maximum[static_cast<std::size_t>(column)], y);
                    }
                }
                for (std::size_t column = 0; column < columns; ++column) {
                    if (column_filled[column] == 0) continue;
                    painter.drawLine(
                        QPointF(wave.left() + static_cast<int>(column), column_minimum[column]),
                        QPointF(wave.left() + static_cast<int>(column), column_maximum[column]));
                }
            } else {
                double previous_x = 0.0;
                double previous_y = 0.0;
                bool started = false;
                for (std::size_t index = begin; index < end; ++index) {
                    const auto& point = series.points[index];
                    if (!point.numeric) continue;
                    const double x = x_for_frame(static_cast<double>(point.frame), wave);
                    const double y = y_for(point.value);
                    if (!started) {
                        painter.drawPoint(QPointF(x, y));
                        started = true;
                    } else {
                        painter.drawLine(QPointF(previous_x, previous_y), QPointF(x, previous_y));
                        painter.drawLine(QPointF(x, previous_y), QPointF(x, y));
                    }
                    previous_x = x;
                    previous_y = y;
                }
            }
        }
        painter.restore();
        painter.setPen(QColor(0xef, 0xef, 0xef));
        painter.drawLine(wave.left(), bottom, wave.right(), bottom);
        painter.setPen(QColor(0x33, 0x33, 0x33));
        painter.drawText(QRect(4, top + 3, kNameWidth - 10, 18), Qt::AlignLeft | Qt::AlignTop,
                         signal.name);
        painter.setPen(QColor(0x99, 0x99, 0x99));
        painter.drawText(QRect(4, top + 3, kNameWidth - 10, row_height - 6),
                         Qt::AlignLeft | Qt::AlignBottom,
                         QStringLiteral("%1 … %2").arg(value_text(minimum), value_text(maximum)));
    }

    // 事件轨
    painter.setPen(QColor(0xdd, 0xdd, 0xdd));
    painter.drawLine(0, kEventHeight - 1, width(), kEventHeight - 1);
    for (const auto& event : events_) {
        const double x = x_for_frame(static_cast<double>(event.frame), wave);
        if (x < wave.left() - 2 || x > wave.right() + 2) continue;
        painter.setBrush(event.color);
        painter.setPen(Qt::NoPen);
        switch (event.kind) {
            case EventKind::checkpoint: {
                // 旗杆 + 旗面
                painter.setPen(QPen(event.color, 1.2));
                painter.drawLine(QPointF(x, 3.0), QPointF(x, kEventHeight - 3.0));
                QPolygonF flag;
                flag << QPointF(x, 3.0) << QPointF(x + 8.0, 5.5) << QPointF(x, 8.0);
                painter.setPen(Qt::NoPen);
                painter.drawPolygon(flag);
                break;
            }
            case EventKind::branch:
                painter.drawEllipse(QPointF(x, 9.0), 3.5, 3.5);
                break;
            case EventKind::intervention: {
                QPolygonF diamond;
                diamond << QPointF(x, 4.5) << QPointF(x + 4.5, 9.0) << QPointF(x, 13.5)
                        << QPointF(x - 4.5, 9.0);
                painter.drawPolygon(diamond);
                break;
            }
            case EventKind::failure:
                painter.setPen(QPen(event.color, 1.6));
                painter.drawLine(QPointF(x - 3.5, 5.5), QPointF(x + 3.5, 12.5));
                painter.drawLine(QPointF(x - 3.5, 12.5), QPointF(x + 3.5, 5.5));
                break;
            case EventKind::stop:
                painter.drawRect(QRectF(x - 3.5, 5.5, 7.0, 7.0));
                break;
            case EventKind::other:
            default: {
                QPolygonF marker;
                marker << QPointF(x - 4.0, kEventHeight - 3.0) << QPointF(x + 4.0, kEventHeight - 3.0)
                       << QPointF(x, 3.0);
                painter.drawPolygon(marker);
                break;
            }
        }
    }
    painter.setBrush(Qt::NoBrush);

    // 检查点参考线（游标之下）
    if (checkpoint_.has_value()) {
        const double x = x_for_frame(static_cast<double>(*checkpoint_), wave);
        if (x >= wave.left() - 1 && x <= wave.right() + 1) {
            QPen pen(QColor(0x17, 0xa2, 0xb8), 1.2);
            pen.setStyle(Qt::DashLine);
            painter.setPen(pen);
            painter.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()));
            painter.drawText(QRectF(x - 30, wave.top() + 1, 60, 16), Qt::AlignCenter,
                             ui_text(*texts_, "workbench.waveform.event.checkpoint", "Checkpoint"));
        }
    }

    // 游标
    const auto draw_cursor = [&](std::int64_t frame, bool valid, const QColor& color, bool dashed,
                                 const QString& label) {
        if (!valid) return;
        const double x = x_for_frame(static_cast<double>(frame), wave);
        if (x < wave.left() - 1 || x > wave.right() + 1) return;
        QPen pen(color, 1.2);
        if (dashed) pen.setStyle(Qt::DashLine);
        painter.setPen(pen);
        painter.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()));
        painter.drawText(QRectF(x - 12, wave.top() + 1, 24, 16), Qt::AlignCenter, label);
    };
    draw_cursor(cursor_a_, cursor_a_valid_, QColor(0xe6, 0x7e, 0x22), false,
                ui_text(*texts_, "workbench.waveform.cursor_a", "A"));
    draw_cursor(cursor_b_, cursor_b_valid_, QColor(0x29, 0x80, 0xb9), true,
                ui_text(*texts_, "workbench.waveform.cursor_b", "B"));
}

void WaveformWidget::mousePressEvent(QMouseEvent* event) {
    if (event->button() != Qt::LeftButton) return;
    pressed_ = true;
    dragged_ = false;
    press_pos_ = event->pos();
    press_view_min_ = view_min_;
    press_view_max_ = view_max_;
}

void WaveformWidget::mouseMoveEvent(QMouseEvent* event) {
    if (pressed_) {
        const int dx = event->pos().x() - press_pos_.x();
        if (!dragged_ && std::abs(dx) > 3) dragged_ = true;
        if (dragged_) {
            const QRect wave = wave_rect();
            const double span = press_view_max_ - press_view_min_;
            const double shift = -static_cast<double>(dx) / std::max(1, wave.width()) * span;
            view_auto_ = false;
            set_view(press_view_min_ + shift, press_view_max_ + shift);
        }
        return;
    }
    update_tooltip(event->pos());
}

void WaveformWidget::mouseReleaseEvent(QMouseEvent* event) {
    if (event->button() != Qt::LeftButton || !pressed_) return;
    pressed_ = false;
    if (!dragged_) {
        const bool secondary = event->modifiers().testFlag(Qt::ShiftModifier);
        // 点击检查点参考线：把游标 A 定位到检查点。
        if (!secondary && checkpoint_.has_value()) {
            const QRect wave = wave_rect();
            const double x = x_for_frame(static_cast<double>(*checkpoint_), wave);
            if (std::abs(x - event->pos().x()) <= 5.0) {
                cursor_a_ = *checkpoint_;
                cursor_a_valid_ = true;
                update();
                emit cursorsChanged();
                emit frameSelected(cursor_a_);
                dragged_ = false;
                return;
            }
        }
        place_cursor(event->pos(), secondary);
    }
    dragged_ = false;
}

void WaveformWidget::mouseDoubleClickEvent(QMouseEvent* event) {
    if (event->button() != Qt::LeftButton) return;
    view_auto_ = true;
    fit_view();
    update();
}

void WaveformWidget::wheelEvent(QWheelEvent* event) {
    const double factor = event->angleDelta().y() > 0 ? 0.8 : 1.25;
    const QRect wave = wave_rect();
    const double anchor = frame_for_x(event->position().x(), wave);
    view_auto_ = false;
    set_view(anchor - (anchor - view_min_) * factor, anchor + (view_max_ - anchor) * factor);
}

void WaveformWidget::place_cursor(const QPoint& pos, bool secondary) {
    const QRect wave = wave_rect();
    if (!wave.contains(pos)) return;
    const auto bounds = data_bounds();
    if (!bounds.valid) return;
    std::int64_t frame = static_cast<std::int64_t>(std::llround(frame_for_x(pos.x(), wave)));
    frame = std::clamp(frame, bounds.min, bounds.max);
    if (secondary) {
        cursor_b_ = frame;
        cursor_b_auto_ = false;
    } else {
        cursor_a_ = frame;
    }
    cursor_a_valid_ = true;
    cursor_b_valid_ = true;
    update();
    emit cursorsChanged();
    emit frameSelected(secondary ? cursor_b_ : cursor_a_);
}

void WaveformWidget::update_tooltip(const QPoint& pos) {
    const QRect wave = wave_rect();
    if (!wave.contains(pos)) {
        QToolTip::hideText();
        return;
    }
    QStringList lines;
    std::int64_t frame = 0;
    bool found = false;
    for (const auto& event : events_) {
        const double x = x_for_frame(static_cast<double>(event.frame), wave);
        if (std::abs(x - pos.x()) > 6) continue;
        if (!found) {
            frame = event.frame;
            found = true;
        }
        // 只列同一逻辑帧的事件；相邻帧不合并提示（WB-15）。
        if (event.frame != frame) continue;
        lines << event.text;
    }
    if (found) {
        QToolTip::showText(mapToGlobal(pos),
                           ui_text(*texts_, "workbench.waveform.event_tooltip", "Frame %1: %2")
                               .arg(frame)
                               .arg(lines.join(QStringLiteral("\n"))),
                           this);
        return;
    }
    QToolTip::hideText();
}

}  // namespace ascend::workbench
