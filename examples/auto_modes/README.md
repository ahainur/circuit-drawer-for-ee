# 仿真识别工作模态

## 简体中文

1、打开 `buck_ccm.json`、`boost_ccm.json`、`buck_dcm.json` 或 `boost_dcm.json`，在“模态”页点击“从仿真识别模态…”。四份表含此前确认的主要模态及其仿真来源，可对照新的候选。原始手写示例位于 `examples/spice`。

2、输入 24 V，开关频率 50 kHz，占空比 0.5，电感 500 μH，输出电容 220 μF。CCM 负载分别为 10 Ω、30 Ω，DCM 为 200 Ω、1 kΩ。四份表运行 80 ms，最大积分步长 200 ns。DCM 电容初值分别为 14.8 V、67 V，帮助轻载工况在此时长内接近周期重复状态。

3、候选中完整保留短暂换流和近零电流区间。默认勾选的主要模态为 CCM 两个、DCM 三个，DCM 第三个为全部关断。核对路径与方向后，可追加或替换，整次应用可撤销。新名称应与已有模态区分。

4、LLC 三种频率工况与 DAB 使用同一识别入口，可选择各绕组电流区分模态。四份试用图包含 25 ms 仿真参数和二十个原手写模态，便于对照完整周期、死区与过零区间。工况说明随试用包提供。[LLC 低于谐振](../../trial/cases/llc_below.json)／[LLC 谐振](../../trial/cases/llc_resonance.json)／[LLC 高于谐振](../../trial/cases/llc_above.json)／[DAB](../../trial/cases/dab.json)

```console
circuit-drawer edit examples/auto_modes/buck_ccm.json
circuit-drawer modes-from-sim examples/auto_modes/buck_dcm.json -o output/buck-dcm-modes
```

5、`buck_bound.json` 已确认完整周期的三个候选并绑定同次仿真的波形。附带 CSV 取原运行末尾周期及相邻采样，保留 300 行原采样值，没有重新求解或改值；完整运行可用 `buck_ccm.json` 重新生成。打开后可直接查看波形、模态和分界，修改参数会提示过期。

## 繁體中文

1、開啟上述範例，在「模態」頁點擊「從模擬辨識模態…」。表內已保存確認的主要模態與來源，新的候選可另行追加或取代，套用後支援復原和儲存。

2、四份表均使用 24 V、50 kHz、0.5 占空比、500 μH、220 μF 和 80 ms 模擬。DCM 的負載為 200 Ω、1 kΩ，電容初值為 14.8 V、67 V。出現漂移提示時，延長模擬或核對初始條件後重新執行。

3、短暫換流和近零電流區間完整保留，可逐項勾選。主要模態為 CCM 兩個、DCM 三個，採用前核對導通路徑與電流方向。

4、LLC 三種頻率工況與 DAB 使用同一辨識入口，可選擇各繞組電流區分模態。四份試用圖包含 25 ms 模擬參數和二十個原手寫模態，可對照完整週期、死區與過零區間。[LLC 低於諧振](../../trial/cases/llc_below.json)／[LLC 諧振](../../trial/cases/llc_resonance.json)／[LLC 高於諧振](../../trial/cases/llc_above.json)／[DAB](../../trial/cases/dab.json)

5、`buck_bound.json` 已確認完整週期候選，並綁定同次模擬的波形。隨附 CSV 保留末尾週期附近的 300 行原採樣值；完整資料可用 `buck_ccm.json` 重新產生。修改參數後，頂部會持續提示來源過期。

## English

1、Open one of the four tables, then choose **Modes → Identify modes from simulation…**. The tables include previously confirmed primary modes and their simulation provenance. Compare new candidates, select append or replace, and apply the selection. Undo and saving follow the ordinary editor workflow.

2、All examples use 24 V, 50 kHz, 0.5 duty, 500 μH, 220 μF and an 80 ms run with a 200 ns maximum step. CCM loads are 10 Ω for Buck and 30 Ω for Boost. DCM loads are 200 Ω and 1 kΩ, with explicit capacitor initial voltages of 14.8 V and 67 V to shorten settling.

3、Short commutation and near-zero intervals remain individually selectable. The primary CCM and DCM candidates number two and three, respectively. Review conducting paths and current directions before applying.

4、The three LLC frequency conditions and DAB use the same recognition dialog with selectable winding currents. These trial files include 25 ms simulation settings and twenty original handwritten modes for comparing the complete period, dead time and current zero crossings. [LLC below resonance](../../trial/cases/llc_below.json) / [LLC at resonance](../../trial/cases/llc_resonance.json) / [LLC above resonance](../../trial/cases/llc_above.json) / [DAB](../../trial/cases/dab.json)


5、`buck_bound.json` includes confirmed modes and a waveform from the same run. Its compact CSV retains 300 original sample rows around the final period. Re-run `buck_ccm.json` for the complete trace. Parameter changes show a persistent stale-source notice.
