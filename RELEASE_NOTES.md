# Circuit Drawer 2.1.0

## 本版更新

1、从空白画布打开 Agent 提示词库，搜索 Boost，选择“只画图”可保留符号，选择“示例仿真”可先采用图纸、再确认工况并运行。另有单相全桥、三相 dq 并网、Boost APFC 和 PMSM FOC 示例。[中文使用说明书](https://github.com/ahainur/circuit-drawer-for-ee/blob/v2.1.0/docs/USER_MANUAL.zh-CN.md)

2、Agent 与 SPICE 仿真页读写同一份参数。打开仿真页即可查看当前工况、通道和待补项，点击提示可直接定位。搜索器件可手工改负载、驱动或初值，并复跑和导出。Agent 面板和器件列表均可拖动或用键盘调整尺寸。

3、美化出图支持 42 种系统图标、选区编辑、算法流程图与多子图组合。个人图标可用当前绘图模型生成简洁线稿，也可用专用图像模型生成设备插画。输入描述或参考图后可先预览，再收藏或应用到图纸。波形出图支持用数值 CSV 对比多个工况。

4、闭环控制支持采样状态和直接门极绑定，可用有功功率、功率因数及谐波指标查看输入电能质量。五个新增示例均可运行 ngspice 并导出 Simulink／PLECS，Boost 和单相全桥还可导出 PSIM 开环建模脚本。原生模型在对应软件中求解。

## 当前适用范围

1、自动模态适用于固定频率内部PWM。模态内部检查与结果页均匀采样检查可能出现判定差异，需要结合原始波形和取样区间判断。变频工况的周期重复性显示待确认。

2、动态PWM死区采用载波阈值近似，请核对实际门极时序。SPS按单组双桥绑定使用，共享原桥双输出调度仍在完善。控制模型、器件参数及适用条件见三语手册。

## 下载与升级

1、64位Windows 10／11用户下载 `CircuitDrawer-Setup-2.1.0.exe`。保存图纸并关闭正在运行的Circuit Drawer，运行安装程序完成升级，用户图纸与设置会保留。[下载Windows离线安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v2.1.0/CircuitDrawer-Setup-2.1.0.exe)

2、安装包包含Python、TinyTeX、Poppler、ngspice和AI依赖。绘图、出图及ngspice仿真支持离线使用，AI使用你配置的在线接口。安装目录采用ASCII字符，可含空格，图纸与导出路径支持中文。[安装与升级说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/WINDOWS_LAUNCHER.md)

3、安装包尚未数字签名。Windows提示未知发布者时，先核对官方下载来源与 `SHA256SUMS.txt`，再按安装说明继续。

4、Python 3.10+用户可安装通用wheel。另提供同版源码、可编辑示例试用包及三语离线手册，分别阅读 `START_HERE.md` 或打开 `index.html`。[查看全部附件](https://github.com/ahainur/circuit-drawer-for-ee/releases/tag/v2.1.0)
