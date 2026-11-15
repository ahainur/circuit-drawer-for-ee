## Windows 离线安装包

1、64 位 Windows 10／11 用户双击安装包并按向导完成安装，从桌面或开始菜单打开 Circuit Drawer。安装包包含 Python、TinyTeX、Poppler、ngspice 和 AI 依赖，绘图、出图及 ngspice 仿真可离线完成。安装包约 68.06 MiB，安装后文件约占 184.23 MiB。[下载 Windows 安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v1.7.0/CircuitDrawer-Setup-1.7.0.exe)

2、安装界面提供简体中文和英文，支持当前用户安装、升级、卸载及第三方许可证查看。安装目录使用 ASCII 字符并支持空格，图纸与导出目录可使用中文。升级和卸载会提示先关闭运行中的程序，并保留用户图纸及设置。

3、新增程序图标，小尺寸使用闪电 L，较大尺寸使用完整 LEE。启动器、编辑器标签页、安装程序和快捷方式使用同一套定稿图标。

4、安装包尚未签名。Windows 提示“未知发布者”时，先核对下载来源与随附件提供的 `SHA256SUMS.txt`，再按安装说明继续。[安装、升级与卸载说明](https://github.com/ahainur/circuit-drawer-for-ee/blob/main/docs/WINDOWS_LAUNCHER.md)

## 本版绘图与仿真功能

1、绘图操作改进：模块展开自适应留空，元件、导线和模块可混合多选移动，普通元件与导线支持整组顺／逆时针旋转。重叠导线可合并，跨线标记随实际交点移动，支持撤销和保存重开。

2、增加整体缩放、模拟 IC 风格、多绕组变压器、系统图标、三相 MMC 及更多基础拓扑和模块。波形支持同轴多曲线、区间尺寸线、XY 轨迹和模态叠加。

3、VCO 变频闭环可导出到 PLECS／Simulink；PSIM 增加开环电路导出，通过已安装并许可的 PSIM 生成原生模型。EMI、阻抗、稳定性、参数范围搜索和环路分析集中到“实验功能”，原有数据与导出继续可用。

4、首次使用可下载 [试用包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v1.7.0/circuit-drawer-trial-1.7.0.zip)，解压后阅读 `START_HERE.md`。已有 Python 3.10+ 可下载 [通用安装包](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v1.7.0/circuit_drawer_for_ee-1.7.0-py3-none-any.whl)，用 `python -m pip install --upgrade circuit_drawer_for_ee-1.7.0-py3-none-any.whl` 安装。PDF／SVG／PNG 出图所需依赖见 [安装说明](https://github.com/ahainur/circuit-drawer-for-ee#installation)。

5、[三语离线手册](https://github.com/ahainur/circuit-drawer-for-ee/releases/download/v1.7.0/circuit-drawer-manuals-1.7.0.zip) 包含简体、繁體、English 和界面截图，解压后打开 `index.html`。另提供源码 ZIP 与 `SHA256SUMS.txt`；已有 JSON 可继续打开。
