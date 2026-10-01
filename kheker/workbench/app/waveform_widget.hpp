#pragma once

#include "ui_text.hpp"

#include <QColor>
#include <QPoint>
#include <QVector>
#include <QWidget>

#include <cstdint>

namespace ascend::workbench {

// 时间轴波形：每个信号一行、序列叠加；离散时间按阶梯线呈现；
// 双游标给出精确读数，事件轨标出检查点、干预、分支起点与失败。
class WaveformWidget : public QWidget {
    Q_OBJECT

public:
    struct Point {
        std::int64_t boundary = 0;
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
    struct Event {
        std::int64_t boundary = 0;
        QString text;
        QColor color;
    };

    explicit WaveformWidget(const UiTexts* texts, QWidget* parent = nullptr);

    void setSignals(QVector<Signal> rows);
    void setEvents(QVector<Event> markers);
    void clear();

    std::int64_t cursorA() const noexcept { return cursor_a_; }
    std::int64_t cursorB() const noexcept { return cursor_b_; }

signals:
    void cursorsChanged();
    void boundarySelected(std::int64_t boundary);

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
    double x_for_boundary(double boundary, const QRect& wave) const;
    double boundary_for_x(double x, const QRect& wave) const;
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
    bool pressed_ = false;
    bool dragged_ = false;
    QPoint press_pos_;
    double press_view_min_ = 0.0;
    double press_view_max_ = 0.0;
};

}  // namespace ascend::workbench
