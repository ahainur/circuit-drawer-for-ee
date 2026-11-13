# 基础拓扑与理想参考波形

1、模板库有 31 个基础拓扑，另保留七个分析／数字控制变体。本目录为新增的 17 个拓扑配套可编辑坐标表、模态和 CSV。坐标表的 `waveform` 指向同目录下的 `waveforms-data`，移动时请一起复制。所有波形标有 **Ideal analytical reference**，由这里的 `reference_waveforms.py` 按下列条件计算，不是电路求解器输出或实测数据。

2、上述 17 个例子直接从模板开始时，得到母图及模态；打开本目录的同名 JSON，才能同时看到配套波形。网页点“文件 → 打开示例”可选所有模板，空白画布也有同一入口。载入后可撤销，保存前不会改写当前文件。双栏为这批图的默认格式；独立 JSON 请显式选择双栏。

```sh
circuit-drawer draw sepic -f pdf,svg -o out/sepic
circuit-drawer modes two_switch_forward -p double -f pdf,svg -o out/forward
circuit-drawer edit examples/power_topologies/cuk.json -p double
circuit-drawer waveforms examples/power_topologies/cuk.json -p double -o out/cuk
python examples/power_topologies/reference_waveforms.py out/fresh-analytical-examples
```

3、在波形窗口查看区间与模态，按需要改图或导出。CSV 为明确工况的参考：更改接线、器件参数、占空比或调制后，需重新计算波形和核对模态。导出不自动改动这些输入。再生成脚本要求目标目录尚不存在，避免覆盖已编辑的图纸。

4、两个三相整流例子另带可导出的演示仿真设置：100 V 相电压峰值、50 Hz、100 mH／10 Ω 串联负载。二极管桥使用六个普通二极管模型；晶闸管桥使用简化晶闸管模型与显式触发。这组有限电感负载参数及启动时间原点，与下表恒定 2 A 的理想解析参考不同，不应把配套 CSV 当作这些参数的仿真结果。

5、本目录另有 `mmc_three_phase.json`，是每臂两个半桥子模块的三相 MMC 拓扑母图。它没有模态、CSV、器件数值或控制策略。网页“文件 → 打开示例 → 三相 MMC（每臂两子模块）”或 `circuit-drawer edit my_mmc.json -t mmc` 可载入；选中子模块后按 Enter 查看内部，取消编辑返回上层。增删子模块后需自行接线、更新数量标注并检查连接。子模块 O 为半桥中点，N 为电容负端；方框边界不是导线，三相负载星点与直流母线分开。

| 模板名 | 接线和模态 | 波形条件及主要通道 |
| --- | --- | --- |
| `synchronous_buck` | 同步 Buck，两段 CCM | 12 V，D=0.4；门极、电感电压和电流 |
| `synchronous_boost` | 同步 Boost，两段 CCM | 12 V，D=0.4；门极、电感电压和电流 |
| `inverting_buck_boost` | 反相升降压，输出负极性 | 12 V，D=0.4；储能／传能 |
| `two_switch_buck_boost` | 两开关同时驱动，两个二极管 | 12 V，D=0.4；电感充放能 |
| `four_switch_buck_boost` | 四 MOSFET，升降压区同时充放能 | 12 V，D=0.4；未采用纯 Buck／Boost 分区调制 |
| `cuk` | Ćuk，两电感与传能电容，负输出 | 12 V，D=0.4；输入电感电压和电流 |
| `sepic` | SEPIC，两独立电感，正输出 | 12 V，D=0.4；输入电感电压和电流 |
| `zeta` | Zeta，两独立电感，正输出 | 12 V，D=0.4；第一电感电压和电流 |
| `interleaved_buck` | 两相交错 Buck，保留两次全关断段 | D=0.3，错开半周期；两门极、两电感电流 |
| `interleaved_boost` | 两相交错 Boost，保留两次全关断段 | D=0.3，错开半周期；两门极、两电感电流 |
| `two_switch_forward` | 双管正激，独立励磁支路、两只复位二极管 | 1:1，D=0.3；传能、复位、续流三段 |
| `phase_shift_full_bridge` | 移相全桥、中心抽头整流和输出电感 | 1:1:1，有效占空比 0.6；正向、低端零电压、反向、高端零电压 |
| `single_phase_full_bridge` | 单相全桥，纯电阻负载 | 12 V 直流、50 Hz 方波、6 Ω；门极、输出电压和电流 |
| `single_phase_half_bridge` | 单相半桥，平衡分裂母线 | 12 V 总母线、50 Hz 方波、6 Ω；输出 ±6 V |
| `single_phase_diode_bridge` | 单相二极管桥，无滤波电容 | 12 V 正弦峰值、50 Hz、6 Ω；源电压、全波输出、电流 |
| `three_phase_diode_bridge` | 六二极管，串联 L-R 负载，六区间 | 相电压峰值 100 V、50 Hz；直流电压、a 相电流、第一器件导通 |
| `three_phase_thyristor_bridge` | 六晶闸管，连续直流电流，六区间 | 同上，触发延迟 30°；导通标记不是窄门极触发脉冲 |

4、DC/DC 波形以 50 kHz、100 µH、忽略电容纹波的理想稳态为基础。单路电感平均电流设为 2 A，交错各路为 1.5 A。同步 MOSFET 的模态允许反向沟道电流；更换为 IGBT 符号后，需要重新核对反并联二极管与死区的导通模态。换符号保持几何连接，不自动改变工况。

5、双管正激的 Lm=1 mH，励磁电流从零上升并经两只复位二极管回到零；输出电感维持 CCM。移相全桥省略漏感、励磁电流及换流过渡，零电压段的理想原边电流为零，两个副边半绕组共同续流。因此其四段图不用于判断 ZVS。中心抽头标注 `1:1:1` 表示原边与两个半副边各为 1:1。

6、单相逆变器用纯电阻负载，不能直接套到感性负载或 SPWM。三相整流参考假定源电感为零、直流电流恒为 2 A，忽略换相重叠；两个电感负载母图没有预填仿真数值。解析波形便于核对基本关系，实际求解前应填写器件数值、驱动和初始条件。

7、连接关系参考：[TI 功率拓扑手册](https://www.ti.com/lit/eb/slyu036a/slyu036a.pdf)、[TI 双管正激](https://www.ti.com/document-viewer/lit/html/SLVAFW4)、[MPS 单相半桥／全桥逆变](https://www.monolithicpower.com/en/learning/mpscholar/power-electronics/dc-ac-converters/single-phase-inverters)、[Plexim 单相二极管整流](https://docs.plexim.com/plecs-demos/basic-topologies/diode-rectifier/)、[Plexim 三相二极管整流](https://www.plexim.com/academy/power-electronics/diode-rect-ind-load-3ph)、[Plexim 六脉波晶闸管变流器](https://docs.plexim.com/plecs-demos/power-supplies/thyristor-converter-6-pulse/)。图纸坐标、模态绑定及波形脚本均为本项目自己的实现。
