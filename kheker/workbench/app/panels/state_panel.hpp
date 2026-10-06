#pragma once

// 状态（右侧停靠页）：详情、检查点与状态字段、采样选择与采样详情三个页签。
// 只读工作区状态渲染；采样序列切换经 sample_series_changed 交外壳请求详情，
// 选中帧仍由外壳持有。设计规则见设计文档 WB-19。

#include "panel_context.hpp"

#include <QWidget>

#include <cstdint>
#include <string>
#include <vector>

class QComboBox;
class QLabel;
class QTableWidget;
class QTabWidget;
class QTextBrowser;
class QTreeWidget;

namespace ascend::workbench {

class StatePanel : public QWidget {
    Q_OBJECT

public:
    explicit StatePanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在运行、装配或结果变更时调用。
    void render_state_fields();
    void render_sample_selectors(const std::vector<std::string>& labels);
    void render_sample_detail(const session::SampleDetailView& detail, std::int64_t frame);
    // 跨面板展示：选中项详情 HTML 由外壳转交到详情页。
    void show_details_html(const QString& html);
    // 身份切换：详情占位、采样标签提示并清空采样树（选中帧仍由外壳清理）。
    void clear_transient();
    // 采样下拉当前索引，供给外壳请求采样详情／最新采样。
    int selected_series() const;

Q_SIGNALS:
    void sample_series_changed();

private:
    const PanelContext& context_;
    QTabWidget* tabs_ = nullptr;
    QTextBrowser* details_view_ = nullptr;
    QLabel* checkpoint_label_ = nullptr;
    QTableWidget* state_table_ = nullptr;
    QComboBox* sample_series_combo_ = nullptr;
    QLabel* sample_label_ = nullptr;
    QTreeWidget* sample_tree_ = nullptr;
};

}  // namespace ascend::workbench
