# 1 MHz Buck 数字控制

## 一、打开示例

1、在电路编辑器载入 `buck_digital` 模板，或打开本目录的 `buck_gan_1mhz.json`。进入“分析 → SPICE 仿真…”，展开“闭环控制”中的“数字控制延迟与量化”，调整后保存或运行。设置随电路中的控制表保存。

2、独立控制框图为 `buck_gan_1mhz.control.json`，也可载入控制框图模板 `buck_digital`。在“环路分析与补偿器设计…”中展开数字设置，点“应用数字设置”查看新 Bode 图、裕度与 z 平面极点。两个 JSON 各自保存修改，需将选定控制表接入电路后再运行对应时域仿真。

```sh
circuit-drawer edit examples/digital_control/buck_gan_1mhz.json
circuit-drawer-control edit examples/digital_control/buck_gan_1mhz.control.json
circuit-drawer spice examples/digital_control/buck_gan_1mhz.json --run -o out/digital-buck
```

## 二、参数和比较方法

1、示例为 48 V 输入、12 V 目标输出、2.4 Ω 负载的同步 Buck。电感 4.7 μH、电容 22 μF，PWM 与采样均为 1 MHz，计算延迟一拍，ADC 为 12 位、0–24 V 量程，PWM 为 14 位，状态为有符号 24 位、18 位小数。参考值在 100 μs 内软启动，运行至 1 ms。

2、先比较计算延迟 0、1、3 拍时的裕度与闭环极点。再将 PWM 改成 8 位，观察末段输出电压、采样值和实际占空比。ADC 在本例中的一步为 5.859375 mV，14 位 PWM 经理想 Buck 直流增益折算为 2.9296875 mV，8 位则为 187.5 mV。窗口在后一种情况下提示分辨率风险。量化极限环与 ADC、PWM、控制增益及定点舍入共同相关。[Peterchev 与 Sanders 的原始论文](https://power.eecs.berkeley.edu/publications/peterch_03.pdf)

3、还可将状态改为 16 位、10 位小数，比较输出偏差与重复振荡。仿真输出 `circuit.csv` 连续电路波形、`circuit.digital.csv` 逐拍原始输入／量化输入／实际占空比，以及 `circuit.digital.json` 的周期候选。尾段重复至少四个周期，周期长度在 2–256 拍内时可给出“疑似量化极限环”。请结合启动是否结束、输入激励及提高分辨率后的波形判断原因。

4、示例采用面向 GaN 高频应用的理想开关近似，通道电阻 8 mΩ、边沿 2 ns、死区 10 ns。器件选型、效率与温升还需具体 GaN 器件及驱动模型。

## 三、模型含义

1、控制器从零状态启动，第一拍发生在 `Ts=1/code.fs`，此后每拍采样、计算并更新保持值。`delay_samples` 将算出的 PWM 指令再延后相应拍数，0 拍表示本次采样后立即更新。电路求解器在各拍间继续积分。每次瞬态最多 20000 拍。

2、ADC 步长为 `(max-min)/2^bits`，码值范围为 0 至 `2^bits-1`，最高输出为 `max-步长`。PWM 步长为 `2^-pwm_bits`，占空比在 0–1 内饱和。舍入采用最近值，恰好半步时向正无穷取整。

3、可选字长模型在每个方块输出和存储状态处舍入并饱和。乘加使用宽浮点累加器，系数按每方块／二阶节分别缩放并保留三位裕量。以 w 表示 `word_bits`、q 表示 `fraction_bits`，状态范围为 −2^(w−1−q) 至 2^(w−1−q)−2^−q。实际 MCU 的累加器、溢出规则与中断时序需按硬件匹配。现有 C 代码仍由 `code.arith/q` 配置，硬件 ADC、PWM 和调度需配套实现。

4、环路分析对模拟功率级采用零阶保持等效，计入整拍延迟和量化系数，按单位圆内极点判断稳定性。ADC、PWM 和状态舍入在线性分析中按单位增益处理，时域仿真保留这些非线性。扫频上限为采样率的 0.499 倍。功率级需直接连接控制器输出与采样输入，并满足现有零阶保持转换对互异非零极点的要求。

5、本阶段逐拍仿真支持标量控制器。含 `digital` 的电路通过 ngspice 运行，Simulink／PLECS 原生导出、连续 AC 阻抗和 k 因子自动设计会提示相应模型边界。数据字段详见[格式说明](../../docs/USER_MANUAL.zh-CN.md#16数字控制延迟与量化)。

## 四、繁體說明

1、電路編輯器載入 `buck_digital`，開啟“分析 → SPICE 模擬…”中的數位控制設定。控制框圖編輯器載入同名模板，在環路分析視窗套用取樣頻率、計算延遲、ADC／PWM 位數及定點字長。

2、先比較 0／1／3 拍延遲，再將 PWM 從 14 位調為 8 位，觀察末段波形和逐拍 CSV。ADC 量程使用輸入的物理數值。重複振盪只作量化極限環候選，須結合輸入激勵及解析度比較。

3、範例為 48 V／12 V、1 MHz 同步 Buck，採用理想開關與有限導通電阻的高頻近似。數位控制從零狀態開始，第一拍在 Ts，支援 8–24 位有符號狀態格式。此模型的 ngspice 逐拍模擬、線性環路與實際 MCU 時序各有對應設定，硬體實作須核對累加器、溢位及中斷規則。[完整繁體操作說明](../../docs/USER_MANUAL.zh-TW.md#16數位控制延遲與量化)

## Five. English guide

1. Load the `buck_digital` template in either editor. In the circuit editor, open **Analysis → SPICE simulation** and expand digital settings under closed-loop control. In the control editor, apply digital settings from the loop-analysis dialog.

2. Compare delays of 0, 1 and 3 samples, then reduce PWM resolution from 14 to 8 bits. Inspect the final output waveform and the per-sample CSV. The ADC range is expressed in physical input units. A repeating tail is reported as a possible quantization limit cycle, with its cause assessed against the excitation and higher-resolution runs.

3. The 48 V to 12 V, 1 MHz synchronous Buck uses ideal switches with finite on-resistance for a high-frequency GaN application example. Digital states start at zero and the first update occurs at Ts. The optional signed 8–24-bit model uses wide accumulation followed by state rounding and saturation. MCU accumulator width, overflow and interrupt timing require matching implementation. [English operating instructions](../../docs/USER_MANUAL.en.md#1716-digital-control-delay-and-quantization)
