# FluentPy 接入改动

基于作者自研的 FluentPy 源码，保留原有控件绘制、圆角、渐变和动画。

- `ThemeManager.subscribe(owner, callback)`：用随控件销毁的 Qt 接收对象连接主题信号。替换此前持有控件的匿名回调，避免窗口或控件关闭后切换主题调用已销毁对象。
- `Toast.success`：自动关闭提示设置 `WA_DeleteOnClose`，及时释放不再显示的提示。
- `NavigationSidebar.set_expanded_width(width)`：提供展开宽度设置；库的默认宽度未变，新客户端使用 172 像素。

上述改动已同步回原库，并附在本程序源码内。客户端使用库公开的主题功能设置浅色与蓝灰色强调色；可以切换深色，没有重做一套控件皮肤。

测试覆盖主题切换时的已销毁窗口、文本框和卡片，以及导航展开和收起。原库的按钮和其他正常工作的组件行为保持原样。

## Fluent Melody 1.2.0 附带版本

为在关闭 Beta 模式时隐藏实验功能入口，附带的 `NavigationView` 增加两个公开方法：

- `set_route_visible(route_key, visible)`：隐藏或恢复指定导航项。隐藏当前页时切换到可见页；重新显示后保持原有排序，不销毁页面控件。
- `is_route_visible(route_key)`：查询导航项是否可见。

隐藏的入口不能通过普通导航切换进入，导航指示器也随当前可见页面更新。该改动只补充导航行为，不改变控件绘制与整体风格；本轮新增接口仅在程序附带版本中，尚未同步回独立的 FluentPy 原库。

FluentPy 是自研、独立实现的 Qt Python UI 库，采用 MIT 许可，并非其他同类 GPLv3 组件库的分支、封装或换皮。Qt / PySide6 和 Microsoft Fluent System Icons 属于第三方依赖，分别遵循各自许可，相关声明保留在发布包的 `licenses` 目录中。
