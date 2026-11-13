# PSIM 导出

1、打开已填写仿真参数的电路，选择 **仿真 → 导出到 PSIM…**，核对元件值、初值、参考地、测量通道和输出目录，再点击 **导出 PSIM**。文件菜单的导出子菜单也有入口。接线或参数检查失败时保留原图和已有输出；成功时按现有仿真窗口规则保存填写的参数。

2、导出生成 Python 建模脚本、模型清单和说明文件。生成这些文件无需安装 PSIM；执行建模脚本需要 **PSIM 2026 或以上、有效许可及官方 Python API**。本机验证的是 Windows 上的 PSIM 2026。以 Buck 为例：

```powershell
circuit-drawer psim examples/spice/buck.json -p double -o out/psim
python out/psim/build_cd_buck.py --psim-home "E:/Altair/Altair_PSIM_2026"
```

3、上面的脚本生成 `cd_buck.psimsch`，可在 PSIM 中打开、编辑和运行。给脚本加 `--run` 可直接调用原生求解器并写出 `cd_buck.csv`；加 `--out` 可另选生成目录。也可通过环境变量 `PSIM_HOME` 指定安装目录。已有同名模型或本次要写的 CSV 会阻止运行，确认需要替换时才加 `--overwrite`。临时文件放在所选结果目录下的 `psim-temp`。

4、当前支持开环的电阻、电感、电容、直流／正弦／分段线性源、线性化二极管、双向电阻开关、理想变压器及额外绕组、电压和电流测量。保留原模型展开后的寄生支路、初值、绕组极性和测量方向。闭环控制、折叠子系统、IGBT 模型、晶闸管和 PMSM 目前会明确拒绝；模块可在编辑器完整展开后导出。VCO 闭环请使用 PLECS 或 Simulink。

5、二极管沿用外部仿真导出的分段线性近似，参考电流会影响结果。LEVEL=1 MOSFET 需在本次导出中明确勾选 **按导通电阻近似 LEVEL=1 开关**，或在命令行加 `--approximate-mosfet`；原图的模型映射不变。开关在 PSIM 的数值导通／关断电阻外另加串并联电阻，使两态等效值与填写的 Ron/Roff 一致；要求 `1e-9 < Ron < Roff < 1e12` Ω。这些简化模型不能用于证明器件损耗、反向恢复或 ZVS。

6、器件按原图的位置和朝向投影，辅助器件就近放置；电气端口用 `CDN…` 网络标签连接，几何交叉不产生新连接。清单保留原引脚与网络对应，建模脚本包含标签映射。导出布局只存在于目标文件，源 JSON 的位置和导线保持。

7、内部步长不超过原设置的输出间隔和最大步长。PSIM 可能为开关事件进一步缩小内部步长；CSV 按原输出间隔对原生采样线性插值，列名、单位及顺序沿用原测量配置。伴随的 `.runtime.json` 记录请求步长、观测步长和采样数量。需要检查快速边沿时应明确减小输出间隔，并比较减半最大步长后的结果。

8、接口依据 PSIM 安装目录中的官方 Python API 与本地帮助；建模方式参见 [Altair 的 Python 建模教程](https://us.v-cdn.net/6038102/uploads/attachments/aca6b65a973c1e10da583b47f053afaf/tutorial%20-%20how%20to%20create%20psim%20schematics%20using%20python%20library.pdf)。其它目标和共有模型近似见 [用户手册](USER_MANUAL.zh-CN.md)。
