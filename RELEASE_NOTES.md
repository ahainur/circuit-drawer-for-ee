# Circuit Drawer 2.2.0

## 本版更新

1、Agent 候选图会说明哪个引脚缺线，以及当前栏宽能否放下电路。点 **手动修改候选** 打开草稿，补线后点 **保存并重新校验**，回原窗口比较、采用或撤销。也可比较同一候选在单栏和双栏的排版。[中文使用说明书](https://github.com/ahainur/circuit-drawer-for-ee/blob/v2.2.0/docs/USER_MANUAL.zh-CN.md)

2、标签压线、碰到元件或彼此重叠时，点 **问题** 可定位相应文字。黄色提醒允许按原样采用、预览和导出。需要增加留白时可单独拖动标签。导线断开、意外短接等电气错误会标出具体位置。

3、在波形出图窗口可插入并拖动矢量拓扑、系统图标、图片和图例，选择透明背景、无边框或圆角投影。横纵网格可分别使用实线或虚线，绘图区可设淡色底。将仪表绑定到曲线和时刻即可显示测量值。编辑器、SPICE 与波形面板增加悬停说明，窄窗可分区滚动。

## 贡献者

1、感谢 @YouthWell 报告「采用」按钮变灰，并提供 Boost PFC＋LLC 截图帮助定位标签排版造成的误报。[贡献者主页](https://github.com/YouthWell)

## 当前适用范围

1、自动模态适用于固定频率内部PWM。模态内部检查与结果页均匀采样检查可能出现判定差异，需要结合原始波形和取样区间判断。变频工况的周期重复性显示待确认。

2、动态PWM死区采用载波阈值近似，请核对实际门极时序。SPS按单组双桥绑定使用，共享原桥双输出调度仍在完善。控制模型、器件参数及适用条件见三语手册。

## 下载与升级

1、64位Windows 10／11用户下载 `CircuitDrawer-Setup-2.2.0.exe`。保存图纸并关闭正在运行的Circuit Drawer，运行安装程序完成升级，用户图纸与设置会保留。[下载Windows离线安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v2.2.0/CircuitDrawer-Setup-2.2.0.exe)

2、安装包包含Python、TinyTeX、Poppler、ngspice和AI依赖。绘图、出图及ngspice仿真支持离线使用，AI使用你配置的在线接口。安装目录采用ASCII字符，可含空格，图纸与导出路径支持中文。[安装与升级说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/WINDOWS_LAUNCHER.md)

3、安装包尚未数字签名。Windows提示未知发布者时，先核对官方下载来源与 `SHA256SUMS.txt`，再按安装说明继续。

4、Python 3.10+用户可安装通用wheel。另提供同版源码、可编辑示例试用包及三语离线手册，分别阅读 `START_HERE.md` 或打开 `index.html`。[查看全部附件](https://github.com/ahainur/circuit-drawer-for-ee/releases/tag/v2.2.0)
