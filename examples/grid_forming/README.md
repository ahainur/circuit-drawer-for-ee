# 下垂、虚拟同步机与两机并联

## 简体中文

1、空白电路画布点“打开示例”，选择 `gfm_droop`、`gfm_vsm` 或 `gfm_parallel` 对应的案例，也可打开本目录的 JSON。进入“分析 → SPICE 仿真…”并点“导出并仿真”。三个案例均已填写元件、控制器和测量通道。并联案例的两台逆变器共接三相电阻负载。

2、额外电阻组在 0.5 s 投入、1.1 s 切除，开关边沿为 10 μs。默认仿真 1.2 s，输出间隔 100 μs、最大积分步长 20 μs。负载开关使用无体二极管的双向理想通道，表示交流负载开关。桥臂采用开关周期平均模型，电压内环采用理想跟踪，适用于平衡孤岛运行与连续控制动态的学习。限流、内环带宽、开关纹波和硬件保护需要相应模型。

3、运行下面的配套脚本，会在每个案例文件夹生成原始网表、波形 CSV、统计 JSON、电路 JSON 和独立控制框图 JSON。`P_W`、`Q_var`、`f_Hz`、`E_V` 直接读取同一次 ngspice 仿真的控制器信号。P/Q 为桥臂输出端的三相总功率，包含后续线路的损耗和无功。E 为内部相电压峰值，f 为振荡器频率。在“波形与模态”中导入 CSV，勾选这些通道并点“应用通道”，即可比较两机响应。统计窗口为 0.40～0.48 s 和 1.00～1.08 s。

```sh
python examples/grid_forming/run.py --case all --out out/grid-forming
circuit-drawer-control edit out/grid-forming/parallel/control.json
```

4、下垂控制使用 `τp·dPf/dt=P−Pf`、`Δω=−mp·Pf`。虚拟同步机使用 `M·dΔω/dt=−P−D·Δω`，频率为 `50+Δω/(2π)` Hz。两者均有 `τq·dQf/dt=Q−Qf`、`E=100−nq·Qf` 和相角积分，P/Q 给定为零。单机下垂的 `mp=0.002 rad/(s·W)`、`τp=0.03 s`，单机 VSM 的 `M=80 W·s²/rad`、`D=500 W·s/rad`。单机共用 `nq=0.02 V/var`、`τq=0.03 s`、300 V 直流电源、每相 5 mH/0.5 Ω 输出阻抗和每组每相 30 Ω 负载。一次调频会留下负载相关的频率静差。

5、两机案例中，第一台沿用单机下垂参数。第二台使用 `M=160`、`D=1000`、`nq=0.01`、每相 2.5 mH/0.25 Ω，负载每组每相 10 Ω。相同标幺下垂与成比例的输出阻抗使稳态功率按约 1∶2 分配。负载投入后的展示窗口中，有功约为 921 W 和 1846 W，频率约为 49.707 Hz。VSM 的响应速度由 M/D 决定。本例取 0.16 s，便于与 0.03 s 的功率滤波下垂比较。固定功率给定时，取 `M=τp/mp`、`D=1/mp` 可得到相同的有功—频率动态。[模型推导](https://arxiv.org/abs/2311.17926)

6、控制框图模板 `gfm_sync` 可通过“从模板新建”载入，再打开环路分析，或运行下列命令。它描述固定电压幅值、相同稳态相角及准稳态 RL 网络下的两机相对相角。环路为 `Tθ=K·[mp/(1+τp·s)+1/(M2·s+D2)]/s`。令 `z1=R1+jω0L1`、`z2=R2+jω0L2`，`K=−1.5E²·Im[1/(z1+z2+z1z2/Rload)]`。模板中的 E=98 V、Rload=5 Ω 为负载投入后的近似工作点，可按实际工作点修改。极点与裕度适用于该降阶相角模型，完整系统还需计入电压/无功动态、线路电磁状态和公共相角模态。

```sh
circuit-drawer-control edit gfm_sync
circuit-drawer-control loop gfm_sync
circuit-drawer-control bode gfm_sync -o out/grid-forming/synchronization
```

7、可用 `--max-step 10u` 比较积分步长。SPICE 平均桥臂按控制和电路动态选取步长。Simulink/PLECS 当前将平均桥臂展开为开关模型，导出前需填写满足 PWM 分辨率的时间设置，本例 10 kHz 桥臂可取输出间隔和最大步长均为 2 μs。

## 繁體中文

1、空白電路畫布點「開啟示例」，選擇 `gfm_droop`、`gfm_vsm` 或 `gfm_parallel` 對應的案例，也可開啟本目錄的 JSON。進入「分析 → SPICE 模擬…」並點「匯出並模擬」。額外三相電阻組在 0.5 s 投入、1.1 s 切除，預設模擬 1.2 s。並聯案例的兩台逆變器共接三相電阻負載。

2、執行上方 `run.py --case all` 指令，在「波形與模態」中匯入生成的 `waveforms.csv`，勾選 `P_W`、`Q_var`、`f_Hz`、`E_V` 並套用通道。這些數值直接讀取同一次 ngspice 模擬的控制器訊號。P/Q 為橋臂輸出端的三相總功率，包含線路損耗與無功。E 為內部相電壓峰值，f 為振盪器頻率。`control.json` 可用控制框圖編輯器開啟。統計視窗為 0.40～0.48 s 與 1.00～1.08 s。

3、下垂控制為 `τp·dPf/dt=P−Pf`、`Δω=−mp·Pf`。VSM 為 `M·dΔω/dt=−P−D·Δω`，頻率為 `50+Δω/(2π)` Hz。兩者採用 `τq·dQf/dt=Q−Qf`、`E=100−nq·Qf`，P/Q 給定為零。單機下垂的 `mp=0.002`、`τp=0.03 s`，單機 VSM 的 `M=80`、`D=500`，共同使用 `nq=0.02 V/var`、`τq=0.03 s`、300 V 直流電源、每相 5 mH/0.5 Ω 輸出阻抗及每組每相 30 Ω 負載。

4、兩機案例第一台採下垂控制，第二台採 `M=160`、`D=1000`、`nq=0.01`、每相 2.5 mH/0.25 Ω 的 VSM，負載每組每相 10 Ω。負載投入後的展示視窗中，有功約為 921 W、1846 W，頻率約為 49.707 Hz。穩態功率按約 1∶2 分配，一次調頻留下負載相關的頻率靜差。固定功率給定下，`M=τp/mp`、`D=1/mp` 可使濾波下垂與 VSM 具有相同的有功—頻率動態。[模型推導](https://arxiv.org/abs/2311.17926)

5、控制框圖範本 `gfm_sync` 提供相對相角的小訊號環路。使用固定幅值、相同穩態相角與準穩態 RL 網路，E=98 V、Rload=5 Ω 為近似工作點。極點與裕度適用於此降階模型。完整系統需納入電壓/無功動態、線路電磁狀態及公共相角模態。電路案例採理想電壓內環、連續控制與平均橋臂，限流、開關紋波和硬體保護需相應模型。SPICE 預設最大步長 20 μs，可用 `--max-step 10u` 比較。Simulink/PLECS 會展開為開關模型，本例可將輸出間隔及最大步長均設為 2 μs。

## English

1. On an empty circuit canvas, choose **Open example** and select the droop, VSM or parallel case. You can also open a JSON file in this directory. Run it through **Analysis → SPICE simulation**. A balanced resistor bank connects at 0.5 s and disconnects at 1.1 s, with 10 μs switch edges. The simulation lasts 1.2 s. The load switches have bilateral ideal channels with body diodes disabled.

2. Run the companion command above with `--case all`. Import the generated `waveforms.csv` into the waveform editor and select `P_W`, `Q_var`, `f_Hz` and `E_V`. These are actual controller signals from the same ngspice solve. P/Q are total three-phase converter-terminal powers, including downstream line loss and reactive power. E is internal phase peak voltage and f is oscillator frequency. Each folder also contains the deck, log, summary, circuit table and an editable `control.json`. Summary windows are 0.40–0.48 s and 1.00–1.08 s.

3. Droop uses `τp·dPf/dt=P−Pf`, `Δω=−mp·Pf`. VSM uses `M·dΔω/dt=−P−D·Δω`, with frequency `50+Δω/(2π)` Hz. Both use `τq·dQf/dt=Q−Qf`, `E=100−nq·Qf`, and an angle integrator. P/Q references are zero. The single-source droop case has `mp=0.002 rad/(s·W)` and `τp=0.03 s`. The VSM has `M=80 W·s²/rad` and `D=500 W·s/rad`. Both have `nq=0.02 V/var`, `τq=0.03 s`, a 300 V DC link, 5 mH/0.5 Ω per-phase output impedance and 30 Ω per-phase resistor banks.

4. In the parallel case, source 1 retains the droop settings. Source 2 uses `M=160`, `D=1000`, `nq=0.01` and 2.5 mH/0.25 Ω per phase. Each load bank is 10 Ω per phase. Proportional impedances and droop coefficients give approximately 1:2 steady-state power sharing. Loaded-window power is about 921 W and 1846 W, with frequency near 49.707 Hz. Primary control leaves a load-dependent frequency offset. VSM has a 0.16 s M/D time constant here. For fixed power references, filtered droop and VSM share the same active-power/frequency dynamics when `M=τp/mp` and `D=1/mp`. [Model derivation](https://arxiv.org/abs/2311.17926)

5. The `gfm_sync` control template provides a reduced relative-angle model with fixed voltage magnitudes, equal equilibrium angles and a quasi-steady RL network. Its loop is `Tθ=K·[mp/(1+τp·s)+1/(M2·s+D2)]/s`, where `K=−1.5E²·Im[1/(z1+z2+z1z2/Rload)]` and `zi=Ri+jω0Li`. E=98 V and Rload=5 Ω approximate the loaded operating point. Edit them for the intended operating point. Its poles and margins cover this reduced model. Full-system analysis also requires voltage/reactive dynamics, electromagnetic line states and the common-angle mode.

6. The circuit examples use ideal inner voltage tracking, continuous control and switching-period-average bridge legs. Current limiting, inner-loop bandwidth, switching ripple and hardware protection require corresponding models. Default output sampling is 100 μs and maximum integration step is 20 μs. Use `--max-step 10u` for a step-size comparison. Simulink/PLECS currently expand average legs into switching models. For these 10 kHz cases, set both output sampling and maximum step to 2 μs before exporting.
