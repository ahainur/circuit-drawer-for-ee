# Circuit Drawer 1.8.0

## 本版更新

1、MOSFET／IGBT 支持可接线栅极、外置驱动、栅极电阻和电容，以及实际 Vgs／Vge 仿真。内部 PWM／闭环可绑定图中的驱动电压源。在元件属性中启用“可接线栅极 g”，再在仿真窗口配置驱动，或打开 `examples/spice/gate_driver_rc.json` 开始使用。

2、改善 AI 控制框图的多行文字、独立输出文字更新、输出走线和分区标题避让。适应窗口包含边缘文字，结构规划检查器件引脚，接口返回空正文时给出明确提示。

3、栅极电容采用线性近似。ngspice／Simulink 简化通道在阈值附近采用数值平滑，PLECS／PSIM 使用理想阈值。参数与适用范围见三语手册的电气栅极章节。

## 下载与升级

1、64 位 Windows 10／11 用户下载 `CircuitDrawer-Setup-1.8.0.exe`。保存图纸并关闭正在运行的 Circuit Drawer 后，运行新版安装程序完成升级，用户图纸与设置会保留。[下载 Windows 离线安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v1.8.0/CircuitDrawer-Setup-1.8.0.exe)

2、安装包包含 Python、TinyTeX、Poppler、ngspice 和 AI 依赖，绘图、出图及 ngspice 仿真支持离线使用。安装目录使用 ASCII 字符，可含空格。图纸、导出目录与 Windows 用户名支持中文。[安装、升级与卸载说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/WINDOWS_LAUNCHER.md)

3、安装包尚未数字签名。Windows 提示“未知发布者”时，核对官方下载来源及 `SHA256SUMS.txt` 中的校验值，再按安装说明继续。

4、Python 3.10+ 用户可安装通用 wheel。另提供同版源码、可编辑示例试用包和三语离线手册，下载后分别阅读 `START_HERE.md` 或打开 `index.html`。[查看全部附件](https://github.com/ahainur/circuit-drawer-for-ee/releases/tag/v1.8.0)
