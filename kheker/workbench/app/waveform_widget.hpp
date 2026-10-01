#pragma once

#include "ui_text.hpp"

#include <QColor>
#include <QPoint>
#include <QVector>
#include <QWidget>

#include <cstdint>
#include <optional>

namespace ascend::workbench {

// 时间轴波形：每个信号一行、序列叠加；离散时间按阶梯线呈现；
// 双游标给出精确读数，事件轨标出检查点、干预、分支起点与失败。
class WaveformWidget : public QWidget {
    Q_OBJECT

public:
    struct Point {
        std::int64_t frame = 0;
        bool numeric = false;
        double value = 0.0;
        bool exact = true;
    };
    struct Series {
        QString label;
        QColor color;
        QVector<Point> points;
    };
    struct Signal {
        QString name;
        QVector<Series> series;
    };
    enum class EventKind { other, checkpoint, branch, intervention, failure, stop };
    struct Event {
        std::int64_t frame = 0;
        QString text;
        QColor color;
        EventKind kind = EventKind::other;
    };

    explicit WaveformWidget(const UiTexts* texts, QWidget* parent = nullptr);

    void setSignals(QVector<Signal> rows);
    void setEvents(QVector<Event> markers);
    // 检查点参考线：frame 有效时画竖向虚线；branch_zone 为真时给检查点之后的区间加淡色底。
    void setCheckpoint(std::optional<std::int64_t> frame, bool branch_zone);
    void clear();

    std::int64_t cursorA() const noexcept { return cursor_a_; }
    std::int64_t cursorB() const noexcept { return cursor_b_; }
    std::optional<std::int64_t> checkpoint() const noexcept { return checkpoint_; }
    bool branchZone() const noexcept { return branch_zone_; }
    // 逻辑帧在控件内的像素位置（波形区中线），供定位与测试使用。
    QPoint framePosition(std::int64_t frame) const;

signals:
    void cursorsChanged();
    void frameSelected(std::int64_t frame);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;

private:
    struct Bounds {
        std::int64_t min = 0;
        std::int64_t max = 0;
        bool valid = false;
    };

    Bounds data_bounds() const;
    double x_for_frame(double frame, const QRect& wave) const;
    double frame_for_x(double x, const QRect& wave) const;
    QRect wave_rect() const;
    void set_view(double minimum, double maximum);
    void fit_view();
    void place_cursor(const QPoint& pos, bool secondary);
    void update_tooltip(const QPoint& pos);

    const UiTexts* texts_ = nullptr;
    QVector<Signal> signals_;
    QVector<Event> events_;
    double view_min_ = 0.0;
    double view_max_ = 1.0;
    bool view_valid_ = false;
    bool view_auto_ = true;  // 未手动缩放／平移前跟随数据范围
    std::int64_t cursor_a_ = 0;
    std::int64_t cursor_b_ = 0;
    bool cursor_a_valid_ = false;
    bool cursor_b_valid_ = false;
    bool cursor_b_auto_ = true;  // B 未手动放置前跟随数据末端
    std::optional<std::int64_t> checkpoint_;
    bool branch_zone_ = false;
    bool pressed_ = false;
    bool dragged_ = false;
    QPoint press_pos_;
    double press_view_min_ = 0.0;
    double press_view_max_ = 0.0;
};

}  // namespace ascend::workbench
