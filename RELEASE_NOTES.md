# Circuit Drawer 2.0.0

## 本版更新

1、两种编辑器新增Agent任务工作流。从空白画布或现有图纸描述需求，审阅电路、控制框图与仿真参数候选，采用后继续执行ngspice、指标、模态和图形导出。工具栏点击“Agent”即可开始。[Agent使用说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/AGENT_MCP.md)

2、提示词库支持常用拓扑参数填写与个人收藏。控制框图支持整理布线、重新排版、候选预览及单条撤销。任务进度、参数依据和结果抽屉采用更紧凑的布局。

3、控制框图可关联功率电路，一次生成完整反馈、数值参数和调制绑定。支持已有后端范围内的PWM、VCO和SPS闭环及PLECS／Simulink导出，并提供Python MCP接口。原生模型在对应仿真软件中打开并求解。

## 当前适用范围

1、自动模态适用于固定频率内部PWM。模态内部检查与结果页均匀采样检查可能出现判定差异，需要结合原始波形和取样区间判断。变频工况的周期重复性显示待确认。

2、动态PWM死区采用载波阈值近似，请核对实际门极时序。SPS按单组双桥绑定使用，共享原桥双输出调度仍在完善。控制模型、器件参数及适用条件见三语手册。

## 下载与升级

1、64位Windows 10／11用户下载 `CircuitDrawer-Setup-2.0.0.exe`。保存图纸并关闭正在运行的Circuit Drawer，运行安装程序完成升级，用户图纸与设置会保留。[下载Windows离线安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v2.0.0/CircuitDrawer-Setup-2.0.0.exe)

2、安装包包含Python、TinyTeX、Poppler、ngspice和AI依赖。绘图、出图及ngspice仿真支持离线使用，AI使用你配置的在线接口。安装目录采用ASCII字符，可含空格，图纸与导出路径支持中文。[安装与升级说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/WINDOWS_LAUNCHER.md)

3、安装包尚未数字签名。Windows提示未知发布者时，先核对官方下载来源与 `SHA256SUMS.txt`，再按安装说明继续。

4、Python 3.10+用户可安装通用wheel。另提供同版源码、可编辑示例试用包及三语离线手册，分别阅读 `START_HERE.md` 或打开 `index.html`。[查看全部附件](https://github.com/ahainur/circuit-drawer-for-ee/releases/tag/v2.0.0)
