"""面板状态基础层：日志、面板内容、参数控件读写。

2026-10-21 从 panel_state.py 原样搬出（只改结构，不动逻辑）。
panel_state 再导出，其他模块照旧经 gui_panel_state 取用。
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QMainWindow, QMdiSubWindow, QWidget)


def _log(window: QMainWindow, text: str) -> None:
    """统一反馈出口：写日志区（累积）+ 状态行（常驻"就绪"，消息
    替换它 5 秒后自动恢复——状态栏永远有内容，不会隐形）。"""
    window.log_text.appendPlainText(text)
    window.status_text.setText(text)
    if window._status_timer is None:
        window._status_timer = QTimer(window)
        window._status_timer.setSingleShot(True)
        window._status_timer.timeout.connect(
            lambda: window.status_text.setText("就绪"))
    window._status_timer.start(5000)   # 重新计时

def _param_box_set(window: QMainWindow, name: str, value) -> None:
    """给参数坞里的控件赋值（控件不在了就跳过）。

    2026-09-27：用户要求撤掉「显示 2θ 范围」那一行（"显示范围用户自己
    放大就行了"）。但视图范围这些键仍然活着——缩放/平移写回它们、[恢复
    默认] 复位它们；写回时**快照才是权威**，控件只是顺带刷新的只读展示，
    没有控件就静默跳过（2026-10-07 那对框在「热图显示」小节回来、
    2026-10-08 晚拆成对比/热图两对）。

    写控件时挂 `_param_box_sync` 旗标：接线到这些框的"即改即画"路径
    （_refresh_heat_view / _refresh_heatmap_view）靠它分辨程序同步和
    用户输入——缩放写回同样走本函数，不挂旗标的话每格滚轮都会触发一次
    整图重画。
    """
    box = window.params.get(name)
    if box is not None:
        prev = getattr(window, "_param_box_sync", False)
        window._param_box_sync = True
        try:
            box.setValue(value)
        finally:
            window._param_box_sync = prev

def _panel_param(window: QMainWindow, dock, name: str,
                 default):
    """面板自己的参数值：优先快照（这张图作图时用的值）。

    没有快照（或快照缺这个键）时退回参数坞控件当前值——防御后路：
    以后新增画图调用点若忘了拍快照，也不至于崩溃。画图一律走这里
    读参数，而不是直接读控件：控件是"编辑对象"的编辑界面，画别的
    面板时控件可能正显示另一张图的设置。
    """
    snap = getattr(dock, "params_snapshot", None)
    if snap and name in snap:
        return snap[name]
    w = window.params.get(name)
    if w is None:
        return default
    return _widget_value(w)

def _widget_value(w):
    """控件的值 → 快照里的表示（勾选框 bool / 下拉框 data / 其余 value()）。"""
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QComboBox):
        return w.currentData()
    return w.value()

def _set_widget_value(w, value) -> None:
    """快照里的表示写回控件（_widget_value 的逆；回放与"未应用"回填共用）。

    下拉框找不到这一项（data 不在列表里）就保持原样——与回放同一条口径。
    """
    if isinstance(w, QCheckBox):
        w.setChecked(bool(value))
    elif isinstance(w, QComboBox):
        idx = w.findData(value)
        if idx >= 0:
            w.setCurrentIndex(idx)
    else:
        w.setValue(value)

def _content(dock) -> QWidget:
    """面板容器 → 面板内容：子窗口用 widget()，弹出窗口用 content。"""
    if isinstance(dock, QMdiSubWindow):
        return dock.widget()
    return dock.content

