# Circuit Drawer User Manual

[简体中文](USER_MANUAL.zh-CN.md) | [繁體中文](USER_MANUAL.zh-TW.md) | English

This manual covers Circuit Drawer for EE 1.7.0. Read chapters 1 and 2 to install the program, then use the rest as a reference. Commands and field names are written exactly as the program expects them.

Both editors support Simplified Chinese, Traditional Chinese and English. Choose **简体中文 / 繁體中文 / English** at the top right. This manual gives Chinese menu names with English equivalents so you can follow it in either interface.

## Contents

1、[Introduction](#1-introduction)

2、[Installation and startup](#2-installation-and-startup)

3、[Drawing from templates](#3-drawing-from-templates)

4、[Editor tour](#4-editor-tour)

5、[Parts library and parts](#5-parts-library-and-parts)

6、[Part properties](#6-part-properties)

7、[Wires, hops and junctions](#7-wires-hops-and-junctions)

8、[Selection and group editing](#8-selection-and-group-editing)

9、[Context menus](#9-context-menus)

10、[Undo, redo and arrow-key nudging](#10-undo-redo-and-arrow-key-nudging)

11、[The checker and the Issues tab](#11-the-checker-and-the-issues-tab)

12、[Regions, blocks, arrows, layers and step and pulse marks](#12-regions-blocks-arrows-layers-and-step-and-pulse-marks)

13、[Colouring parts and text](#13-colouring-parts-and-text)

14、[Modules](#14-modules)

15、[Operating-mode figures](#15-operating-mode-figures)

16、[Waveforms and modes](#16-waveforms-and-modes)

17、[SPICE simulation](#17-spice-simulation)

18、[Simulink and PLECS export, closed-loop control](#18-simulink-and-plecs-export-closed-loop-control)

19、[Control diagram editor](#19-control-diagram-editor)

20、[AI drawing assistant](#20-ai-drawing-assistant)

21、[Export formats, LaTeX and Word](#21-export-formats-latex-and-word)

22、[Command-line reference](#22-command-line-reference)

23、[Environment variables](#23-environment-variables)

24、[FAQ and troubleshooting](#24-faq-and-troubleshooting)

25、[Keyboard and mouse reference](#25-keyboard-and-mouse-reference)

26、[Appendix A. Coordinate table format](#appendix-a-coordinate-table-format)

27、[Appendix B. Drawing rules](#appendix-b-drawing-rules)

---

## 1. Introduction

### 1.1 What it does

Circuit Drawer draws circuit figures for power electronics papers and reports. You place parts and wires in a JSON coordinate table (or in the web editor). The program checks that the drawing is tidy and agrees with its netlist, then writes CircuiTikZ figures sized for journal columns. The same table can be exported to ngspice, Simulink and PLECS models, drawn as operating-mode figures, and lined up with waveforms. Control block diagrams have their own editor.

### 1.2 Three principles

1、**The coordinate table is the single source of truth.** Part positions, wires, the netlist, modes, regions, simulation parameters and waveform settings all live in one `.json` file. PDF, SVG, PNG, TeX and the simulation models are generated from it. To change a figure, change the JSON or edit it in the editor; do not edit the exported TeX.

2、**No automatic layout, and no figure until the checker passes.** The program never computes part positions and never patches a table silently. Errors reported by the checker must be fixed first; [chapter 11](#11-the-checker-and-the-issues-tab) explains how.

3、**Passing the checker does not mean the circuit works.** The checker covers connections, geometry and text placement. Bias, current direction and magnitude, device values, dead time and whether a mode really occurs are for you to confirm. All simulation models are simplified and are not meant for loss, efficiency or ZVS conclusions.

### 1.3 Coordinates and sizes

Coordinates are in "layout units", roughly 1 cm each. Dragging in the editor snaps to a 0.05 grid. When a figure is produced, the horizontal scale follows the column width: at most 8.79 cm for a single column and 17.99 cm for a double column; the vertical scale stays between 1.0 and 1.2. Symbols and text have absolute sizes (8 pt Times by default); fitting a column only stretches or compresses the spacing between parts, and small circuits keep their natural width instead of being stretched to the full column. A horizontal scale below 0.55 means the drawing does not fit the chosen column.

---

## 2. Installation and startup

### 2.1 Requirements

| Component | Purpose | Required? |
| --- | --- | --- |
| Python 3.10 or newer | Running the program | Yes |
| LaTeX (MiKTeX or TeX Live) with `circuitikz`, `standalone`, `mathptmx` | PDF output, the editor's true preview | For PDF, SVG and PNG |
| Poppler `pdftocairo` | Converting PDF to SVG and PNG | For SVG and PNG |
| LaTeX package `CJKutf8` | Chinese text in figures | Only when you write Chinese |
| ngspice | Running SPICE simulations, CSV output | Only for "export and simulate" |
| MATLAB, Simulink, Simscape | Running the exported Simulink build script | Only for Simulink |
| PLECS Standalone | Opening the exported `.plecs` model | Only for PLECS |
| Python package `anthropic` | The AI drawing assistant with an Anthropic-compatible interface | Only for that interface |

Editing, checking and exporting TeX sources or simulation model files need neither LaTeX, ngspice nor any commercial software.

### 2.2 Installing the program

Option 1: download the wheel `circuit_drawer_for_ee-1.7.0-py3-none-any.whl` from the [GitHub Releases](https://github.com/ahainur/circuit-drawer-for-ee/releases) page and run, in the folder that contains it:

```bash
python -m pip install circuit_drawer_for_ee-1.7.0-py3-none-any.whl
```

Option 2: install directly from the repository:

```bash
python -m pip install "git+https://github.com/ahainur/circuit-drawer-for-ee"
```

To use the AI drawing assistant with an Anthropic-compatible interface, install one more package (the OpenAI-compatible interface does not need it):

```bash
python -m pip install "anthropic>=2.3"
```

The installation provides three commands:

| Command | Purpose |
| --- | --- |
| `circuit-drawer` | Circuits: templates, editor, checking, modes, waveforms, simulation export, AI |
| `circuit-drawer-control` | Control diagrams: editor, figures, loop analysis, C code |
| `circuit-drawer-launcher` | Launcher window: open or create files, manage editors, enter API settings |

If your shell cannot find them (Python's Scripts folder is not on PATH), use `python -m circuit_drawer`, `python -m circuit_drawer.control` and `python -m circuit_drawer.launcher` with the same arguments.

### 2.3 Installing optional components

1、**LaTeX (Windows):** install [MiKTeX](https://miktex.org/download) and set "Install missing packages on-the-fly" to "Yes". The first figure downloads circuitikz and other packages, so it needs a network connection and takes a while. TeX Live works as well.

2、**Poppler (Windows):** run `conda install -c conda-forge poppler`, or download a Windows build of Poppler and add its `bin` folder to PATH.

3、**Linux (Debian, Ubuntu):**

```bash
sudo apt-get install texlive-pictures texlive-latex-extra texlive-fonts-recommended poppler-utils
sudo apt-get install latex-cjk-chinese     # if figures contain Chinese
```

4、**macOS:**

```bash
brew install --cask mactex-no-gui
brew install poppler
```

5、**ngspice:** download it from the [ngspice website](https://ngspice.sourceforge.io/download.html). On Windows, unpack it and use `Spice64\bin\ngspice_con.exe`; on Linux, use your distribution's package manager. The program looks for it in this order: the `--ngspice` command-line option, the environment variable `CIRCUIT_DRAWER_NGSPICE`, then `ngspice_con` or `ngspice` on PATH. The Windows launcher's API and simulation settings can also point to it.

6、**MATLAB and PLECS:** the Simulink export uses the Simscape Foundation Library and needs MATLAB, Simulink and Simscape. A PMSM component or a controller using Clarke/Park transforms also needs Simscape Electrical. The PLECS export is a native PLECS Standalone file, not a PLECS Blockset file. Neither program is needed to produce the export files.

7、**Checking the installation:** open a new terminal and run the commands below. If the first two run at all, LaTeX and Poppler are installed.

```bash
pdflatex --version
pdftocairo -v
circuit-drawer list
```

### 2.4 Getting the example files

The `examples/...` files referred to in this manual are in the repository's [`examples/`](../examples/) folder and are not included in the wheel. Download the source archive from the Releases page, or:

```bash
git clone https://github.com/ahainur/circuit-drawer-for-ee
cd circuit-drawer-for-ee
```

Run the example commands in this manual from that folder. The built-in templates are installed with the program and work without the example files.

### 2.5 Starting the circuit editor from the command line

```bash
circuit-drawer edit fb.json -t flyback       # create fb.json from the flyback template if missing, then open the browser
circuit-drawer edit fb.json                  # continue editing an existing table
circuit-drawer edit inv.json -p double       # large circuits: open at double-column width (the JSON does not store it)
circuit-drawer edit fb.json --port 8770      # use another port when 8765 is taken
```

1、The editor runs on your computer; the browser opens `http://127.0.0.1:8765`. `127.0.0.1` means this computer, and the editor listens only locally. Ordinary drawing needs no network connection.

2、If the file does not exist and no `-t` is given, the command stops with an error instead of creating a file. To start from an empty canvas, write a minimal table first, or use **新建电路…** (New circuit) in the launcher:

```json
{"name": "my_circuit", "elements": [], "wires": []}
```

3、Other options: `--no-browser` does not open a browser; `-o` sets the export folder (default: the JSON's folder); `--provider` and `--model` choose the interface type and model for the AI drawing assistant.

4、Press `Ctrl+C` in the terminal to stop the editor. Closing the browser tab does not stop it; open the same address again to return.

### 2.6 Windows launcher

The launcher is a small window for opening circuits, creating circuits and managing editors without a terminal. It uses the Python installation and this program; it does not bundle LaTeX, Poppler or other tools.

1、**Opening the launcher:** type `circuit-drawer-launcher` in the Run box or a terminal (on Windows it opens without a console window), or run `python -m circuit_drawer.launcher`. For a desktop icon, find Python's Scripts folder with the command below and create a desktop shortcut to `circuit-drawer-launcher.exe` in it:

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

2、**打开 / 继续编辑** (Open / continue editing): pick a `.json` under **图纸 / 最近文件** (Drawing / recent files) or with **浏览…** (Browse), then click the button. With **下次双击启动器时，自动打开上次使用的图纸** (Open the last drawing next time) checked, the next start goes straight back to the last figure.

3、**新建电路…** (New circuit): the default is **空白电路（自己绘制 / AI 生成）** (Blank circuit, draw by hand or with AI); you can also start from a template such as Buck or LLC, then choose where to save. A new circuit needs a file name that does not exist yet, so existing figures are never overwritten.

4、**新建控制框图…** (New control diagram): likewise starts from blank or from a template and opens the control diagram editor.

5、**API 与仿真设置…** (API and simulation settings): enter the interface type (`anthropic` for an Anthropic-compatible interface, `openai` for an OpenAI-compatible interface), model ID, base URL, API key, the two reasoning-effort levels and the ngspice path, then click **应用设置** (Apply settings). With **在当前 Windows 账户中加密记住 API Key** (Remember the API key, encrypted for this Windows account) checked, the key is encrypted with Windows DPAPI and stored in `%LOCALAPPDATA%\CircuitDrawer\launcher.json`, never in the project, the circuit JSON or Git; unchecked, it is used only for the current launcher session. Drawing, modes and local simulation need no API.

6、**正在运行的编辑器** (Running editors): lists the editors the launcher started. If you closed the browser, select the editor and click **重新打开浏览器** (Reopen browser); click **停止所选编辑器** (Stop selected editor) when you no longer need it. Quitting the launcher stops every editor it started, so save in the browser first.

7、The launcher reuses the previous port for each file where possible, so settings the browser remembers (such as the export folder) still apply; if the port is busy it picks a free one. It never stops editors you started yourself in a terminal.

8、Command-line options: `circuit-drawer-launcher my.json` opens a file directly; `--control` starts by creating a control diagram; `--settings` uses another settings file.

9、The launcher also runs on Linux and macOS (Python with Tk is required), without the encrypted key storage.

### 2.7 Starting the control diagram editor

```bash
circuit-drawer-control edit my_ctrl.json -t dual_loop   # create my_ctrl.json from the dual_loop example if missing
```

The control diagram editor uses port 8766 by default and does not interfere with the circuit editor. See [chapter 19](#19-control-diagram-editor).

---

## 3. Drawing from templates

### 3.1 Basic commands

```bash
circuit-drawer list                                     # all templates, options and Chinese keywords
circuit-drawer draw llc_fullbridge                      # default drawing, IEEE single column, four formats into out/
circuit-drawer draw llc 全桥整流 switch=igbt -p double  # only state what differs from the default
circuit-drawer draw dab output=source currents -o figs --name dab_fig -f pdf,png
```

1、**Options** are written as `key=value` or as plain keywords, in English or Chinese (for example `igbt`, `全桥整流` for a full-bridge rectifier, `电流` for current arrows, `二极管在下` for a low-side diode). Unknown words are reported with the list of valid ones; they are never ignored silently.

2、**Column width:** `-p single` (`ieee-single`) or `-p double` (`ieee-double`). When a drawing does not fit a single column, the checker reports `too-cramped` and suggests the double column.

3、**Output:** `-o` output folder (default `out/`); `-f` formats, default `pdf,svg,png,tex`; `--name` file name; `--grid` overlays the coordinate grid (for checking only, not for papers); `--mono` turns regions and arrows grey.

### 3.2 Templates

| Template (aliases) | Content | Options | Default width | Built-in modes |
| --- | --- | --- | --- | --- |
| `llc_fullbridge` (`llc`, `全桥llc`) | MOSFET full bridge, Cr–Lr, Lm, 1:n, centre-tapped rectifier, Co ∥ RL | `switch=mosfet/igbt`, `rectifier=center_tap/full_bridge`, `currents`, `lm=yes/no`, `fs=below/resonance/above` | Single | 6 for fs < fr, 4 for fs = fr, 6 for fs > fr |
| `dab` | Two full bridges, series inductor, 1:n, Co ∥ RL | `switch`, `output=load/source`, `currents`, `inductor=text` | Single | 4 per half period, single phase shift |
| `flyback` (`反激`) | Single switch, coupled inductor with opposite dots, output diode | `switch`, `currents`, `diode=top/bottom` | Single | 3 (the third is the DCM idle interval) |
| `zsource_inverter` (`z源`, `阻抗源逆变器`, ...) | Diode, X-shaped impedance network, three-phase bridge, star load | `switch`, `diode=yes/no`, `load=r/rl`, `x=diagonal/orthogonal` | Double | None |
| `vienna_rectifier` (`维也纳`, `vienna`, ...) | Textbook three-phase Vienna PFC | `switch` | Single | None |
| `buck` (`降压变换器`, ...) | Buck with freewheeling diode | `switch=drawn/mosfet/igbt` | Single | 2 CCM |
| `buck_emi` (`传导EMI`, `EMI Buck`) | Buck, two LISNs, chassis parasitics and filter | Edit electrical values in the SPICE window | Double | None |
| `buck_impedance` | Buck averaged output impedance | AC sweep, transient injection, Bode/Nyquist | Double | None |
| `grid_dq_impedance` | Grid-connected inverter dq impedance | Two-axis injection, full matrix, amplitude/step comparison | Double | None |
| `boost` (`升压变换器`, ...) | Boost with output diode | `switch=drawn/mosfet/igbt` | Single | 2 CCM |
| `llc_halfbridge` (`半桥llc`, ...) | Half bridge, Cr–Lr, Lm, centre-tapped rectifier | `switch=drawn/mosfet/igbt` | Single | None |
| `cllc` (`clllc`, ...) | Two full bridges, series LC on both sides, Lm | `switch=drawn/mosfet/igbt` | Double | None |
| `three_phase_inverter` (`三相逆变器`, ...) | Two-level six-switch bridge, DC-link capacitor, star load | `switch=drawn/mosfet/igbt` | Double | None |
| `npc_3level` (`npc`, ...) | Three-phase three-level diode-clamped inverter | `switch=drawn/mosfet/igbt` | Double | None |
| `dab_multi` (`多模块dab`, ...) | Two DABs, outputs in series, one coloured region per module | `switch=drawn/mosfet/igbt` | Double | None |
| `llc_ipos` (`多模块llc`, ...) | Two half-bridge LLCs, inputs in parallel, outputs in series, with regions | `switch=drawn/mosfet/igbt` | Double | None |

1、`switch=drawn` keeps the devices the template was drawn with. Switching to MOSFET or IGBT only changes the symbols; it does not prove that the circuit still works.

2、The output of `circuit-drawer list` is the authoritative list and includes templates you saved yourself.

3、For templates without built-in modes, define your own in the editor or in the JSON; see [chapter 15](#15-operating-mode-figures).

4、There are 31 basic topologies plus seven analysis/digital-control variants. The 17 additions cover synchronous Buck/Boost, inverting and two-/four-switch buck-boost, Ćuk/SEPIC/Zeta, interleaved Buck/Boost, two-switch forward, phase-shift full bridge, single-phase half-/full-bridge inverters, single-/three-phase diode bridges and a three-phase thyristor bridge. **File → Open example**, the empty-canvas button, the launcher and `circuit-drawer list` share the same registry. Loading a template is undoable and selects its default column width.

5、Each addition includes modes and defaults to double column. Open the matching JSON in the [companion examples](../examples/power_topologies/README.md) for key waveforms bound to those intervals; copy its `waveforms-data` folder too. Charts say **Ideal analytical reference** and use the stated ideal operating conditions. Recalculate after changing parameters, modulation or connections. The examples document reverse channel conduction, forward reset, full-bridge zero-voltage intervals and rectifier commutation assumptions.

6、Open **Three-phase MMC (two cells per arm)** for six arms, twelve half-bridge cells, six arm inductors and a floating star load. Select a cell and press Enter to edit its internal circuit; cancel to return to the parent. Definitions are stored in the drawing and ordinary wires remain editable. Use **View → Scale circuit…** to resize it. This is a topology drawing: component values, modulation, capacitor balancing and simulation settings must be provided separately.

![Editable three-phase MMC](images/manual/en/mmc-three-phase.png)

![Complete template list](images/manual/en/power-templates.png)

![Ćuk reference waveforms and mode intervals](images/manual/en/power-templates-waveforms.png)

### 3.3 Export the table, then edit it

```bash
circuit-drawer export llc -o llc.json       # write the template as JSON
circuit-drawer edit llc.json                # edit it in the editor
circuit-drawer check llc.json               # check only, print a JSON report; exit code 1 if it fails
circuit-drawer render llc.json -p single    # draw once the check passes
```

The exported JSON does not record the column width. Add `-p double` when reopening or drawing a large circuit.

### 3.4 Saving your own template

```bash
circuit-drawer save-template bb.json buck_boost --title "Four-switch buck-boost" --alias 四开关升降压
circuit-drawer draw 四开关升降压 switch=igbt
```

1、Template names may contain only lower-case letters, digits and underscores. Only tables that pass the checker are accepted, and the name may not clash with a built-in template; add `--force` to replace one of your own.

2、Templates are stored in `~/.circuit_drawer/templates/`, or in the folder named by the environment variable `CIRCUIT_DRAWER_TEMPLATES`.

3、**文件 → 存为模板…** (File > Save as template) in the editor does the same.

4、The save confirmation and `list` show the options supported by that template. `switch` requires switch symbols, and `currents` requires existing current reference arrows. Names are trimmed and converted to lowercase. After saving with `--dir`, set `CIRCUIT_DRAWER_TEMPLATES` to the same folder when using the template.

5、Run `circuit-drawer --version` or `circuit-drawer-control --version` to see the version being used.

---

## 4. Editor tour

![Circuit editor overview](images/manual/en/editor-overview.png)

Use the **文 / A** selector at the top right to change the interface language immediately. Unsaved edits, open forms and field values are preserved. The browser remembers the choice for the same address and port; another editor port has its own preference. All translations ship with the software and work offline. Drawing text, custom names, JSON and raw output from external tools remain unchanged. The command line and Windows launcher use their own existing text.

### 4.1 Layout

1、**Menu bar** (first row): 文件 (File), 编辑 (Edit), 视图 (View), 插入 (Insert), 仿真 (Simulation), 模态与波形 (Modes and waveforms), AI, 帮助 (Help), in the order of the work: draw, simulate, modes and waveforms, export. Shortcuts are shown on the right of menu items; an item with › opens a submenu.

2、**Toolbar** (second row), in six groups (file, edit, draw, view, simulation, AI): 保存 (Save), 导出 (Export), 撤销 (Undo), 重做 (Redo), 删除 (Delete), 导线 (Wire), 文字 (Text), 适应 (Fit), 预览 (Preview), **仿真 (Simulation)**, 识别模态 (Identify modes), 波形与模态 (Waveforms and modes), AI 修改 (AI edit). The highlighted Simulation button opens the SPICE simulation window; Identify modes opens **Identify modes from simulation**. Unavailable buttons are greyed out. While a module definition is being edited, **设置端口并保存…** (Set ports and save…) and **取消编辑定义** (Cancel definition editing) appear here.

3、**File tab:** shows the current file name; hover to see the full path. A dot before the name means there are unsaved changes.

4、**Parts library** (left): a categorised list that can be searched, clicked or dragged onto the canvas; see [chapter 5](#5-parts-library-and-parts).

5、**Canvas** (centre): the light dot grid matches the 0.05 snap step. Scroll to zoom; drag with the middle button, or hold Space and drag with the left button, to pan. **视图 → 坐标刻度** (View > Coordinates) shows coordinate ticks.

6、**Information panel** (right): four tabs.

| Tab | Content |
| --- | --- |
| 属性 (Properties) | Settings of the selected object; opens automatically when you select a part, wire, text, region, block, function mark, arrow or module. **图纸比例** (Drawing scale) below shows the current scale |
| 网表 (Netlist) | Net names, pins and branch numbers; expand **SPICE 网表** (SPICE netlist) to see the netlist that would be exported now |
| 问题 (Issues) | Checker results and fix buttons; the number on the tab is the issue count |
| 模态 (Modes) | Switching, editing and exporting mode figures |

7、**Status bar** (bottom): on the left, a hint for the current action; in the middle, the check status (**未检查** not checked, **校验通过** check passed, or **有 N 个问题** N issues) — click it to select and centre each problem in turn; on the right, the pointer coordinates, the zoom relative to "fit", and the numbers of parts and nodes.

8、**Empty canvas:** when there are no parts, the centre of the canvas offers **打开示例** (Open example) and **AI 描述** (Describe with AI).

9、Scroll the parts library and information panel to see additional entries and properties. Successful saves and errors are also shown briefly in a corner of the canvas.

10、**Three levels of hints**: a status line says only the current state or next step; hovering over a button or field shows a short explanation; rules, scope and modelling assumptions sit behind the **?** beside a heading, opened on demand. A wrong entry gets a red note right below the field saying what is wrong and how to fix it, for example `2M` in a SPICE value: “M means milli; write Meg for mega”. The mode panel, SPICE simulation, Identify modes from simulation, the analyses under Experimental (EMI, impedance, stability) and AI connection settings all follow this format.

### 4.2 Menus

| Menu | Items |
| --- | --- |
| 文件 (File) | 保存 (Save), 另存为… (Save As) — 存为模板… (Save as template), 存为模块… (Save as module) — 图纸栏宽 (Figure column width) — 导出 › (Export: circuit figure, all mode figures, ngspice netlist, Simulink model, PLECS model, PSIM model) |
| 编辑 (Edit) | 撤销 (Undo), 重做 (Redo) — 剪切 (Cut), 复制 (Copy), 粘贴 (Paste), 创建副本 (Duplicate), 删除所选 (Delete selection) — 顺时针旋转 90° (Rotate clockwise 90°), 逆时针旋转 90° (Rotate counterclockwise 90°), 镜像 / 反向 (Mirror / reverse), 增大符号 (Enlarge symbol), 缩小符号 (Shrink symbol), 同类对象图层 › (Layer among objects of the same kind: raise, lower, bring to front, send to back) — 按图更新网表 (Update netlist from drawing), Merge overlapping wires — with modules also 编辑选中模块定义 (Edit selected module definition) and 解散选中实例为普通元件 (Dissolve selected instance), and for old-style module tables 模块定义… (Module definitions) and 转换为端口连接 (Convert to port links) |
| 视图 (View) | 适应窗口 (Fit), 放大 (Zoom in), 缩小 (Zoom out), 模块显示 › (Module display) — 画布上显示 (Show on canvas): 节点/支路标号 (Node / branch labels), 坐标刻度 (Coordinates), 实时网表面板 (Live netlist panel) — 预览设置 (Preview options): 带坐标网 (With grid), 色块灰度 (Grey regions), then 真实预览 (Rendered preview) |
| 插入 (Insert) | 画导线 (Draw wire), 导线交叉 (Wire crossing mode) — 元件 › (Components, grouped as passive, semiconductors, sources, magnetics and machines, connections), 添加模块… (Add module) — 标注 › (Annotations: add text, draw region, draw block, draw arrow, draw signal arrow, draw function mark, step / pulse mark) |
| 仿真 (Simulation) | SPICE simulation…, Device models… — Export ngspice netlist…, Export to Simulink…, Export to PLECS…, Export to PSIM… |
| 模态与波形 (Modes and waveforms) | 从仿真识别模态… (Identify modes from simulation) — 上一个模态 / 下一个模态 (Previous / next mode), 在图上改模态 (Edit modes on canvas), 复制为新模态 (Duplicate as new mode), 模态真实预览 (Rendered mode preview) — 波形与模态… (Waveforms and modes), 导出全部模态图… (Export all mode figures) |
| AI | 描述新拓扑… (Describe a new topology), AI 修改当前图… (AI edit of the current figure) — AI 接口设置… (AI interface settings) |
| 实验功能 (Experimental) | Conducted EMI estimate…, Output impedance…, dq impedance matrix…, Source/load stability…, dq interconnection stability… — Parameter range search… |
| 帮助 (Help) | 用户指南 (User guide), 提示词说明 (Prompt guide), 操作与快捷键… (Actions and shortcuts) — 关于… (About) |

![Experimental tools in the circuit editor](images/manual/en/experimental-circuit.png)

In the table, “—” marks a separator. Exports to Simulink, PLECS, PSIM and ngspice share the SPICE simulation parameters, so these entries open the SPICE simulation window and point at the matching export button.

Menus can be operated with the arrow keys and Enter, and closed with Esc or by clicking the canvas. The user guide and prompt guide in the Help menu open the project's online documentation in a new browser tab and need a network connection.

### 4.3 Saving, previewing and exporting

1、**保存** (Save) writes the current JSON. The circuit editor has no save shortcut; use the toolbar button or **文件 → 保存** (File > Save).

   **文件 → 另存为…** (File > Save As) creates a file beside the original and switches to it; later saves write to the new file. It adds `.json`, replaces spaces and punctuation with underscores, and adds `_2`, `_3`, etc. if the name exists. Cancelling before submission writes nothing; closing after submission lets the save finish. Edits made while waiting stay unsaved. Finish or cancel module-definition editing before using Save As for the whole project.

2、**预览** (Preview, the true preview) runs LaTeX to show the final result. **视图 → 带坐标网** (View > With grid) decides whether the preview overlays the coordinate grid for reference.

3、**导出** (Export): see [chapter 21](#21-export-formats-latex-and-word). Exported figures never contain the grid, and export is only possible when the checker passes.

4、**Column width:** **文件 → 图纸栏宽** (File > Figure column width) switches between IEEE single and double column. The JSON does not store the width, so choose it again when you reopen a large circuit, or add `-p double` on the command line.

---

## 5. Parts library and parts

### 5.1 Using the parts library

1、**Adding parts:** click a card to place the part in the middle of the view, or drag the card onto the canvas to place it where you drop it. You can also use the parts list at the bottom of the **插入** (Insert) menu, or right-click an empty spot and choose **在这里插入 ›** (Insert here).

2、**Search:** type a name in Chinese or English, a part type or a category, for example "电感", "L", "igbt" or "电源" (sources). Matching groups open while searching and return to their previous state when the search is cleared.

3、**Groups:** 无源器件 (passive parts), 半导体 (semiconductors), 电源 (sources), 连接 (connections), 磁性器件 (magnetics), and at the bottom 模块（子系统） (modules, see [chapter 14](#14-modules)). Click a group title to collapse it.

### 5.2 List of parts

| Library name | Table `kind` | Pins | Notes |
| --- | --- | --- | --- |
| 电阻 (resistor) | `R` | `a`, `b` | Both pins share an x or a y |
| 电感 (inductor) | `L` | `a`, `b` | As above |
| 电容 (capacitor) | `C` | `a`, `b` | As above |
| 二极管 (diode) | `D` | `a` (anode), `b` (cathode) | Generic diode |
| 稳压管 (Zener diode) | `D` with `opts.zener: true` | As diode | Drawn as a Zener; simulation needs breakdown parameters |
| 晶闸管 (thyristor) | `scr` | `a` (anode), `b` (cathode) | Horizontal or vertical only; the gate lead is drawn on the left of a→b, `F` moves it to the right; the gate is driven by the simulation trigger pulse and is not a pin |
| MOSFET | `nmos` | `d`, `s` | Body diode by default (`opts.diode: false` removes it) |
| P-channel MOSFET | `pmos` | `d`, `s` | Source-up symbol with a gate dot; body diode d→s; logical PWM 1 = on. Set its parameters in Simulation → SPICE simulation. |
| IGBT | `igbt` | `c`, `e` | Anti-parallel diode by default |
| 电压源 (voltage source) | `V` | `a` (−), `b` (+) | DC voltage source |
| 交流电源 (AC source) | `vac` | `a` (reference), `b` | Sinusoidal source |
| 三相电源 (three-phase source) | three `vac` | `a`, `b` each | Places three AC sources in star with the neutral wire, labelled v_a, v_b, v_c |
| 电池 (battery) | `V` with `opts.battery: true` | As voltage source | The long plate is +, at pin b; simulated like a voltage source |
| 电流源 (current source) | `I` | `a`, `b` | Current flows from a to b |
| 端子 (terminal) | `port` | `p` | Hollow circle where one wire ends, such as inputs a/b/c or outputs P/O/N |
| 变压器 (transformer) | `transformer` | `p1`, `p2` (primary, top to bottom), `s1`, `s2` (secondary), optional `sct` (centre tap) | Ratio text and dots can be set; can be rotated by 90° |
| 耦合电感 (coupled inductor) | `transformer` with `opts.core: false` | As transformer | A transformer drawn without core lines; same pins and simulation fields |
| 永磁同步电机 (PMSM) | `pmsm` | `a`, `b`, `c` | Three-phase motor; no exposed star point. See chapter 17 for simulation. |

There is no separate card for a centre-tapped transformer: copy one from a template such as `llc_fullbridge`, or add an `sct` pin to the transformer in the table (see [Appendix A](#appendix-a-coordinate-table-format)).

Multi-winding transformer (QAB, AQAB and other multiport converters): select the transformer and, in the properties row "Multi-winding", click "Add on primary side" or "Add on secondary side". Another winding (w3–w6, at most 6 windings) appears on the same core, dotted at its a end; wire it to its bridge and enter its turns ratio `ratio_w3` etc. under "SPICE simulation…". "Remove winding w…" removes the highest numbered extra winding, including its pin declarations, measurements and turns ratio. Wires stay in place and the edit can be undone. First update any mode, feedback or reference-ground setting that uses this winding. Example: `examples/multiwinding/qab_multiwinding.json`.

![Multi-winding transformer and properties](images/manual/en/multiwinding.png)

System-diagram icons (battery / storage, PV panel, server rack, grid, square-wave source, DC capacitor, and the DC/DC, DC/AC converter boxes): draw a box with "Insert → Draw box" and pick one under "Icon" in the properties. With an icon, the box text is printed under the box as a caption. Icons are drawing only and take no part in connections or simulation. Example: `examples/system_icons/dc_bus_system.json`.

Icons move with the box and stay centered with their proportions preserved during resizing. Add standalone icons with "Insert → Function mark". A box referencing a control table shows the actual control diagram when expanded and its selected icon when collapsed.

![System diagram icons and properties](images/manual/en/system-icons.png)

---

## 6. Part properties

![Part properties](images/manual/en/element-properties.png)

Select a part and the **属性** (Properties) tab shows its settings. Different parts show different items.

### 6.1 Action buttons

**Rotate clockwise 90°**, **Rotate counterclockwise 90°**, **Reverse** (shown as **Mirror, gate to the other side** for MOSFETs and IGBTs, and absent for transformers) and **Duplicate** correspond to `R`, `Shift+R`, `F` and `Ctrl+D`.

1、`R` rotates clockwise by 90°; `Shift+R` rotates counterclockwise by 90°. Transformers can be rotated too; the ratio text stays upright.

![Clockwise and counterclockwise rotation commands](images/manual/rotation-directions.en.png)

2、`F` mirrors: switches and thyristors move the gate to the other side; other two-terminal parts are reversed. Transformers cannot be mirrored. Note that reversing a two-terminal part only changes the drawing (`opts.invert`) and does not swap its pins, so the simulation ignores it; to really reverse a part, swap the coordinates of its two pins.

### 6.2 Size

1、**长度倍率 / 宽度倍率** (Length factor / Width factor): symbol size along and across the pins, 0.5–2, following the part's rotation. Transformers show **绕组长度 / 绕组宽度** (Winding length / width).

2、**等比大小** (Uniform size) sets both axes to the same factor; **全部元件同此大小** (All parts this size) applies it to every part.

3、`+` / `-` (or **编辑 → 增大符号 / 缩小符号**, Edit > Enlarge / Shrink symbol) scale while keeping the current aspect ratio; **恢复默认尺寸** (Reset size) goes back to 1.

4、Size changes only the symbol. Pins, wires and the netlist stay the same; checking and export use the new size.

### 6.2a Symbol style

1、Select a resistor or MOSFET and choose Power electronics or Analog IC (Razavi) in its properties. To change the drawing, choose View → Symbol style preset → Apply to current drawing. One undo restores it; save to persist the change.

2、IC style uses zigzag resistors and simplified MOSFETs with an open PMOS gate circle. Physical body diodes remain in simulation and appear in conducting modes. This does not change device parameters or add a gate terminal.

3、The preset includes module internals and automatic external parts. Required library definitions are copied into this drawing; the shared library stays unchanged. New parts use the default style; apply the preset again if needed. Example: [analog_ic.json](../examples/styles/analog_ic.json).

![Symbol style preset](images/manual/en/analog-ic-style.png)

### 6.2b Scale a circuit

1、Select a circuit group by dragging a selection rectangle or Shift+clicking, then right-click → Scale circuit…. Alternatively choose View → Scale circuit… and select Entire circuit.

2、Enter a factor: `0.8` reduces it to 80% of its current size; `1.2` enlarges it to 120%. Component bodies, pins, wires and spacing change together. Scale font sizes too is selected by default; clear it to keep fonts unchanged. Line widths stay unchanged.

3、Choose Preview resize, inspect the actual rendered result, then Apply to current drawing. Unselected ordinary parts stay in place and external wires retain their connections. Modules keep their hierarchy and electrical parameters while their full and partial drawings and boxes resize. Scaling only some instances creates a separate project definition; the shared library stays unchanged.

4、One undo/redo covers the operation; save to persist it. The paper conversion stays fixed so automatic column fitting cannot cancel the resize. Restore automatic fitting changes the paper conversion, not the authored coordinates; use undo to restore those. Sizes that are too small, exceed the column width, collide or change connections cannot be applied. Adjust the factor or include more of the circuit. Example: [boost_scaled.json](../examples/styles/boost_scaled.json).

5、A frame referencing a control table continues to fit that table inside the frame; its internal font sizes do not follow circuit resizing. Collapse the control frame first if you need a uniformly scaled appearance. An expanded controller that no longer fits is rejected by the preview. The referenced table and control parameters stay unchanged.

![Scale a circuit](images/manual/en/circuit-uniform-scale.png)

### 6.3 Label, text size and position

1、**标签** (Label): plain text or LaTeX, for example `$L_r$`, `$V_{\mathrm{in}}$`, `$\phi_1$`. Maths on the canvas is an approximation; the preview shows the final result. Subscripts with several letters need braces; `$V_in$` produces a warning.

2、**标签字号** (Label size): 5–14 pt, default 8 pt; also applies to the part's current-arrow text. **全部文字同此字号** (All text this size) unifies every label.

3、**标签位置** (Label position): automatic, above, below, left, right. Switch labels are placed by on-screen direction.

4、Free text such as vo, +, − shows **字号** (Size) and **颜色** (Colour) when selected.

**Dragging labels:** drag a part label or transformer ratio independently of its part and wires. **恢复默认标签位置** (Reset label position) clears the offset. The change supports undo, redo and saving.

![Dragging a component label independently](images/manual/en/text-guides.png)

### 6.4 Colour

The **颜色** (Colour) list and colour picker colour the part; see [chapter 13](#13-colouring-parts-and-text).

### 6.5 Pin coordinates and intentional no-connects

1、**引脚 a / 引脚 b** (Pin a / Pin b; d/s or c/e for switches, p1/p2/s1/s2 for transformers) accept coordinates directly.

2、Each pin has a **故意悬空（NoConn）** (Intentionally unconnected) checkbox. Tick it only for terminals that really need no connection: the "pin not connected" error goes away and a small cross is drawn on the canvas and in exports. It does not hide any other wiring error.

### 6.6 Part-specific settings

| Part | Settings |
| --- | --- |
| Diode | 画成稳压管 (Draw as Zener) |
| Voltage source | 画成电池 (Draw as battery); 源旁波形 (Source waveform: rising step, falling step, pulse), 波形位置 (position), 波形大小 (size) |
| Current source | 源旁波形, 波形位置, 波形大小 |
| Transformer, coupled inductor | 变比 (Ratio text), 同名端 (Dots: same side, opposite, none) |

Resistors, inductors and capacitors also offer solid, dashed and dotted symbol lines. Line style changes the drawing only.

### 6.7 Current arrows

Select a two-terminal part, such as an inductor or resistor, and enter `$i_L$` in **Current arrow label**. View the arrow on its lead in the rendered preview or exported figure. Clear the field to remove it. The value is saved in `opts.current`. Independent arrows on wires use annotations with an `arrow` field, described in Appendix A.

### 6.8 Simulation parameters

Electrical values are not in the Properties tab but in **仿真 → SPICE 仿真…** (Simulation > SPICE simulation); see [chapter 17](#17-spice-simulation). Right-clicking a part and choosing **仿真参数…** (Simulation parameters) jumps straight to its row. Labels on the drawing are only text; the program never derives values from them.

---

## 7. Wires, hops and junctions

### 7.1 Drawing wires

1、Click **导线** (Wire) on the toolbar or **插入 → 画导线** (Insert > Draw wire), then click each corner on the canvas. Double-click or press `Enter` to finish, `Esc` to cancel.

2、Every segment must be horizontal or vertical. Start at a pin and end at a pin or on another wire.

3、Click a pin to start drawing. Move near a target pin or module port to show a green triangle, then click beside it to connect precisely and finish the wire. Click empty space to add intermediate bends. Hold `Alt` to suspend port snapping.

4、Physical wire contacts appear green. Connected endpoints also appear green when the wire is selected. The Issues tab reports shorts, open circuits and other drawing problems.

![Connection triangle near a target port](images/manual/en/module-port-snap.png)

![Green endpoints after connecting](images/manual/en/module-port-connected.png)

### 7.2 Editing existing wires

![Wire properties](images/manual/en/wire-properties.png)

1、**Dragging:** select a wire and drag a corner (small circle), or drag a horizontal or vertical segment directly. Dragging a segment keeps both ends in place and adds horizontal and vertical jogs near the ends if needed; one drag is one undo step.

2、**Adding a corner:** double-click a segment to add a point where you clicked; or click **添加折点（中点）** (Add corner at midpoint) in the Properties tab; or right-click the wire and choose **在此处加折点** (Add corner here).

3、**Deleting a corner:** click the small circle, or choose an inner point in the **选中点** (Selected point) list, then press `Delete` / `Backspace` or click **删除折点** (Delete corner). The neighbouring points are joined directly; if that makes a slanted segment, follow the checker's advice. End points cannot be deleted this way.

4、**Splitting and merging:** select an inner point and click **在此点拆成两条** (Split here), or right-click and choose **在此处拆成两条** (Split here; no corner needed first). Two wires that share exactly one end point can be merged with **合并 wire[n]** (Merge wire[n]) in the Properties tab, or by right-clicking and choosing **和首尾相接的导线合成一条** (Merge with the wire that continues it). A wire cannot be split at a hop.

5、**Deleting a whole wire:** **删除整条导线** (Delete whole wire) in the Properties tab, or right-click and choose **删除这条线** (Delete this wire).

6、**Overlapping wires:** clicking the same spot again cycles through overlapping wires. The selected wire is blue even if it has an error.

7、**Merge overlapping wires:** use the button beside an overlap issue, the Edit menu, the wire context menu or Properties. This removes repeated coverage from exactly collinear horizontal or vertical ordinary wires in the current drawing, including partial overlaps, while preserving junctions and insulated crossings. Undo in one step; Save keeps the result on reopening. Nearby parallel wires stay separate. Open a module definition to merge its internal wires. If connections would change or new conflicts would appear, the original drawing is kept. Free ends revealed by removing duplicates still require attention. The original button for deleting one exact duplicate remains available.

![One-click merge beside an overlap issue](images/manual/wire-merge.en.png)

8、Hovering over a point shows `wire[n] 点 m` (wire n, point m), counted from 0 in the same order as the JSON.

### 7.3 Crossings: hop or junction

1、Where two wires cross, draw either a hop (a semicircle, not connected) or a junction (a solid dot, connected). **插入 → 导线交叉** (Insert > Wire crossing) sets which one new wires use.

2、Click a semicircle or dot, or select a wire and use its Crossing rows in Properties to switch between a hop and a junction. The context menu also offers this action. An unmarked-crossing issue has direct repair buttons, including for ordinary external wires in drawings with modules. Open a module definition to edit its internal crossings.

3、When you drag a wire, nudge it with the arrow keys or edit its coordinates, existing hops follow the actual intersections during the edit. A hop is removed when its two wires no longer cross. Undo restores the previous state; Save keeps the result on reopening.

![Crossing repair actions in a modular drawing](images/manual/wire-crossing.en.png)

4、A T-joint (a wire end on another wire) counts as connected and gets a dot automatically; three or more segments meeting at a point also get a dot. A wire ending in the middle of a part's lead is an error.

### 7.4 Net names, node and branch labels

![Node labels and netlist](images/manual/en/netlist-panel.png)

1、**视图 → 节点/支路标号** (View > Node / branch labels) shows net names (N1, N2, ... or your own names) on a light background and grey branch numbers B1, B2, .... Turning it on the first time assigns fixed numbers; moving, adding or deleting other parts later does not renumber existing ones, and turning the display off does not erase them.

2、**Naming a net:** select a wire and type a name such as OP or SW under **网络名** (Net name); leave it empty to return to automatic numbering. Names are 1–41 ASCII letters, digits or underscores, start with a letter, and are unique across the drawing ignoring case.

3、**Netlist tab:** the **网表** (Netlist) tab (or **视图 → 实时网表面板**, View > Live netlist panel) lists the pins on each net and the branch numbers, refreshed after every edit; expand **SPICE 网表** to see the current netlist. If simulation parameters are still missing, it explains why.

4、When exporting a figure, the **包含节点标号** (Include node labels) checkbox decides whether these labels appear; it is unticked each time the dialog opens.

5、Once labels are enabled, the SPICE netlist uses the net names; parts keep their IDs (L1, S1, ...), and branch numbers appear only in comments. Older files that never enabled labels produce the same netlist as before.

### 7.5 Updating the netlist from the current connections

The table's `netlist` records which pins should be connected; the checker compares the connections found in the geometry with it pair by pair. If you changed the wiring on purpose (for example moved a diode to the return line), use **编辑 → 按图更新网表** (Edit > Update netlist from drawing) to make the current geometry the new netlist; otherwise the checker reports a netlist mismatch. Do not use it to hide a wrong connection.

---

## 8. Selection and group editing

![Mixed selection of parts, wires, text and a region](images/manual/en/box-selection.png)

### 8.1 Selecting

1、**Click** a part, wire, text, region, block, function mark, arrow or module. Click an empty spot to clear the selection.

2、**Box selection:** drag from an empty spot or an unselected region to select top-level parts, wires, free text, regions, blocks, function marks and arrows. All pins, wire vertices or annotation bounds must lie inside the rectangle. Ctrl+A selects these seven object types.

3、**Adding:** hold Shift while box-selecting to add; Shift-click toggles objects. Module instances can move together with components, wires and annotations. Their internals stay intact and external wires follow. A selection containing modules supports movement; use the individual actions to copy or delete modules. Standalone waveform marks are outside mixed selection.

4、While adding text or drawing wires, regions, blocks, function marks or arrows, or placing a module, the left button belongs to that tool and box selection is suspended; press `Esc` to leave the tool.

### 8.2 Panning and zooming

1、Scroll to zoom.

2、Drag with the middle button (wheel) to pan; on a touchpad, hold Space and drag with the left button. Space in a text field still types a space.

3、**视图 → 适应窗口** (View > Fit, or **适应** on the toolbar) fits the whole drawing; **放大** and **缩小** zoom in steps.

### 8.3 Group operations

With several parts or annotations selected:

1、Drag any one of them to move the whole group, snapping to the 0.05 grid. Wires inside the group move with it; wires to outside parts stretch by the same rules as when dragging a single end, and shared junctions stay connected. Press `Esc` during the drag to cancel.

2、`Delete` deletes the group, `Ctrl+C` copies, `Ctrl+X` cuts, `Ctrl+V` pastes, `Ctrl+D` duplicates; on macOS `Cmd` works in place of `Ctrl`. The same actions are in the **编辑** (Edit) menu and the context menu. Each group operation is one undo step.

3、Copies include the wires inside the group, the parts' simulation parameters and their no-connect marks; copies get new part IDs and separate nets. Wires leading outside the group are not copied. The first paste after a cut puts the parts back where they were and keeps their IDs where possible; later pastes are offset step by step.

4、Deleting or cutting parts also removes their simulation parameters, the probes that refer to them and their module port links.

5、Arrow keys nudge the group one grid step; Shift plus an arrow moves ten steps. Moving a group does not avoid other objects automatically; the checker reports any overlap. A fitted region needs all its members selected to move. Save as module requires selecting parts only, without explicit wires or annotations.

### 8.4 Smart guides

When you drag one part or free text, guide lines appear and snap, much as in presentation software. Group dragging follows the grid. Free text aligns with other text, part labels and part bodies, including equal spacing; labels can align exactly while part pins remain on the 0.05 grid.

1、First priority: a pin snaps to the horizontal or vertical line through another part's pin (so the wire can be straight). Next: alignment of body centres and body edges. Then: equal spacing with neighbouring parts in the same row or column.

2、Alignments are drawn as magenta dashed lines; equal spacing is marked by short ticks at both ends of each gap. The snap range is about 6 screen pixels regardless of zoom and takes precedence over the 0.05 grid.

3、Hold `Alt` while dragging to snap to the grid only.

---

## 9. Context menus

Context menus contain actions only; values such as colour, text size and symbol size stay in the Properties tab. Use the up and down keys to choose, `Enter` to run and `Esc` to close; the left and right keys enter and leave submenus. Hovering over a greyed-out item explains why it is unavailable. While drawing a wire or annotation, or placing a module, finish or press `Esc` before opening a context menu.

![Context menu of a part](images/manual/en/context-menu-element.png)

### 9.1 Parts

Right-clicking a part selects it first; right-clicking a group that is already selected keeps the whole group.

| Item | Action |
| --- | --- |
| 剪切, 复制, 粘贴, 创建副本, 删除 (Cut, Copy, Paste, Duplicate, Delete) | Same as the shortcuts, applied to the selection |
| Rotate clockwise / counterclockwise 90° | One part or selected parts and wires; R / Shift+R rotate the group about its centre while retaining external contacts. One undo step. Deselect modules and annotations first |
| 镜像 / 反向 (Mirror / reverse) | Single part only |
| 编辑标签… (Edit label) | Puts the cursor in the label field of the Properties tab |
| 仿真参数… (Simulation parameters) | Opens the SPICE window, scrolls to and highlights this part's row |
| 测两端电压, 测电流, 测驱动 (Measure voltage, current, drive) | Adds a supported measurement channel and opens the SPICE window for confirmation; unavailable types explain why |
| 存为模块… (Save as module) | Several parts; see [chapter 14](#14-modules) |

### 9.2 Wires

| Item | Action |
| --- | --- |
| 在此处加折点 (Add corner here) | Adds a corner at the pointer |
| 删除折点 (Delete corner) | Deletes an inner corner (not an end point) |
| 在此处拆成两条 (Split here) | Splits at the pointer in one undo step; not possible at a hop |
| 和首尾相接的导线合成一条 (Merge with the wire that continues it) | When several wires qualify, a submenu lists their numbers |
| Merge overlapping wires | Removes repeated collinear coverage from ordinary wires in this drawing, checks connections, and supports one-step undo |
| 删除这条线 (Delete this wire) | Deletes the whole wire |
| 测对地电压 (Measure voltage to ground) | Adds a channel for the connected node; set a reference ground first |

Right-click a pin to toggle its intentional no-connect mark. At a four-way crossing, switch between a junction and a hop; a T connection or a part pin cannot become a hop. Related issues reuse the Issues panel's existing actions.

If a whole wire is selected but no corner or segment of it, the arrow keys only show a hint and never pull the wire off its pins.

### 9.3 Empty canvas

![Context menu of the empty canvas](images/manual/en/context-menu-blank.png)

| Item | Action |
| --- | --- |
| 粘贴到这里 (Paste here) | Centres the copy on the pointer and snaps it to the grid; greyed out when the clipboard is empty |
| 在这里插入 › (Insert here) | Places a part or module at the pointer |
| 画区域, 画方框, 画箭头 (Draw region, block, arrow) | Starts the drawing tool |
| 全选顶层对象 (Select all top-level objects) | Selects parts, wires, text, regions, blocks, function marks and arrows |
| 适应窗口 (Fit) | Fits the drawing to the canvas |

**新建空子系统** (New empty subsystem) places an empty module that you can build up in its internal canvas.

### 9.4 Module instances

Right-clicking a module instance or a part inside it offers: 编辑模块定义 (Edit module definition), 实例参数… (Instance parameters), 展开 / 折叠 (Expand / fold), 解散为普通元件 (Dissolve into ordinary parts), 存进我的模块库… (Save to my module library), and 从库更新 (Update from library) when the library has a newer version. See [chapter 14](#14-modules).

### 9.5 Regions, blocks and arrows

| Item | Action |
| --- | --- |
| 上移一层, 下移一层, 置顶, 置底 (Raise, Lower, Bring to front, Send to back) | Changes the order among objects of the same kind; greyed out at the top or bottom |
| 复制, 删除 (Copy, Delete) | Paste the copy with `Ctrl+V` or **粘贴到这里** on the empty canvas |
| 编辑文字… (Edit text) | Jumps to the text field in the Properties tab |

A copy of a region that fits its members automatically still follows the original members; to place it freely, first fix its boundary with **色块贴合成员** (Fit region to members) in the Properties tab. Graphics inside modules are edited in the module definition.

---

## 10. Undo, redo and arrow-key nudging

### 10.1 Undo and redo

1、The toolbar and the **编辑** (Edit) menu both have **撤销** (Undo) and **重做** (Redo). `Ctrl+Z` undoes, `Ctrl+Y` or `Ctrl+Shift+Z` redoes; `Cmd` works on macOS.

2、The buttons are greyed out when there is nothing to undo or redo. Selecting, zooming or panning does not clear the redo history; only a real change to the drawing starts a new branch.

3、After undo or redo, ordinary objects are deselected; click again to continue nudging. A module instance that still exists stays selected. The shared module definition and the main drawing keep separate histories.

### 10.2 Nudging with the arrow keys

1、Select one part, free text, fixed-boundary region, block, function mark, arrow, step / pulse mark or module instance and press an arrow key to move it by one grid step (0.05 layout units); hold Shift to move 10 steps (0.5). Connected wires follow by the same rules as when dragging; modules keep their port links.

2、Wires: with a corner selected, the arrow keys move the corner; with a segment selected, only movement perpendicular to the segment is allowed and the ends stay put.

3、In text fields, editable text, menus, dialogs and the waveform panel, the arrow keys act on those controls and never move the drawing.

---

## 11. The checker and the Issues tab

![Issues tab](images/manual/en/issues-panel.png)

### 11.1 When checking happens

Every change is checked immediately in the background. Problem parts and positions turn red on the canvas; the **问题** (Issues) tab explains each problem in the selected interface language; the status bar shows **校验通过** (check passed) or **有 N 个问题** (N issues), and clicking it moves to each problem in turn.

### 11.2 Reading the Issues tab

1、The top line reads **✓ 校验通过，可以导出** (check passed, ready to export) or **✗ N 个问题需要修正** (N problems to fix).

2、Each problem is a card with a title, details (part IDs and coordinates) and buttons. **选中元件 X** (Select part X) jumps to the object; problems with an automatic fix have a fix button, for example **改成带花括号的写法** (Add braces), **加跨线** (Add hop) or **这是故意的……** (This is intentional).

3、Yellow cards are warnings (for example missing subscript braces) and do not block export; red cards are errors and must be fixed before export; blue-grey cards record something you allowed, and **撤回** (Withdraw) restores the check.

### 11.3 What it checks

| Category | Main rules | Example titles in the Issues tab |
| --- | --- | --- |
| Geometry | Wires and parts horizontal or vertical; enough distance between a part's pins for its symbol; transformer winding direction and order | 不是横平竖直 (not orthogonal); 元件两端太近，画不下 (pins too close for the symbol) |
| Overlap | Clearance between symbols, between wires and symbols, between parallel wires | 两个元件符号重叠 (symbols overlap); 导线穿过了元件符号 (wire crosses a symbol); 两条线叠在一起 (wires on top of each other); 两条平行线太近 (parallel wires too close) |
| Crossings and joints | Every crossing declared as a hop; no wire ending in the middle of a lead | 两线交叉但没标跨线 (unmarked crossing); 导线接在了元件引线中间 (wire touches a lead) |
| Connections | Every pin connected, no dangling wires; geometry agrees with the netlist pair by pair | 引脚没接上 (open pin); 导线一端悬空 (dangling wire); 该连的没连上 (missing connection); 不该连的连上了 (unwanted connection) |
| Text | Labels not on symbols, not on wires, not on each other; current arrows on wires | 文字压在元件上 (text on symbol); 文字压在线上 (text on wire); 文字互相重叠 (text overlap); 电流箭头不在导线上 (arrow off wire) |
| Column width | Horizontal scale at least 0.55 | 按当前栏宽放不下 (does not fit the column) |
| Regions, blocks, arrows | Region borders do not cut parts; arrows do not cover parts; zoomed details not wired to the main circuit; module members inside their region | 区域边框切过了元件 (region cuts a symbol); 箭头压在元件上 (arrow on symbol); 放大图被连到了主电路上 (detail wired to circuit); 模块成员不在区域框内 (member outside region) |
| Modules | Folded boxes do not overlap or cover parts; port links can be routed | 折叠框重叠或压到元件 (folded box overlap); 自动布线失败 (routing failed) |
| Fields | Valid options, colours and field names | 颜色名不认识 (unknown colour); 区域或箭头的设置不对 (bad region or arrow setting) |

[Appendix B](#appendix-b-drawing-rules) lists all rules.

### 11.4 What it does not check

Whether the bias is sensible, whether currents really flow in the drawn direction and with that magnitude, device values, dead time, and whether a mode occurs under your operating conditions. It only guarantees that the drawing is tidy and agrees with its declared netlist.

### 11.5 Fixing common problems

| Problem | Code | Fix |
| --- | --- | --- |
| Not horizontal or vertical | `not-orthogonal` | Align the two pins or corners to the same x or y; the smart guides help while dragging |
| Pins too close for the symbol | `too-short` | Drag the small blue square on a pin to lengthen the part, or reduce the length factor |
| Symbols overlap | `body-overlap` | Move one part by 0.1–0.2, or shrink the symbols |
| Wire crosses a symbol | `line-through-body` | Add corners so the wire goes around the part |
| Wires on top of each other / parallel wires too close | `collinear-overlap` / `too-close` | Use Merge overlapping wires for collinear overlaps; keep parallel wires at least 0.18 cm apart |
| Unmarked crossing | `unmarked-crossing` | Click **加跨线** (Add hop); if the wires should connect, change the crossing to a junction |
| Wire touches a lead | `touches-lead` | Move the wire end onto the pin |
| Open pin | `open-pin` | Connect a wire; for a terminal that really needs no connection, tick **故意悬空（NoConn）** |
| Dangling wire | `dangling-wire` | Connect the end to a pin or another wire, or delete the segment |
| Missing / unwanted connection | `netlist-open` / `netlist-short` | First decide whether it is a mistake; if you changed the wiring on purpose, use **编辑 → 按图更新网表** |
| Text on symbol / on wire / overlapping | `text-on-symbol` / `text-on-line` / `text-overlap` | Change the label position, move the part by 0.1–0.2, or reduce the text size |
| Subscript without braces (warning) | `subscript-braces` | Click **改成带花括号的写法**; `$V_in$` becomes `$V_{in}$` |
| Does not fit the column | `too-cramped` | Switch to double column, or tighten the spacing and shrink symbols |
| Region border cuts a part | `region-cuts-symbol` | Drag the region's corners so the part is entirely inside or outside |
| Zoomed detail wired to the circuit | `detail-wired` | Remove the wire running into the detail; if the region is real circuitry, untick the arrow's **指向放大图** (Points at a zoomed detail) |
| Folded box overlaps parts | `module-box-overlap` | Shrink the box or move the instance |
| No room for a net label | `electrical-label-space` | Move nearby parts or text, or shorten the net name |
| Wrong field or value | `bad-option`, `bad-color`, `bad-region`, ... | Use one of the values listed in the message |

### 11.6 When overlap is intentional: allow it per object

The checker blocks overlaps by default because most of them are mistakes. When one is intentional, allow it for that object only; there is no global switch:

1、**A mark on a wire:** to draw a standalone step / pulse mark on a signal line, click **这是故意的：符号压在导线上** (Intentional: symbol on wire) in the issue card, or tick **压在导线上** (On wire) in the mark's properties. The figure covers that short piece of wire with the background colour; the connection is unchanged.

2、**An arrow that only points at a region:** if the region an arrow points at is real circuitry with a background colour, not a zoomed detail, click **这是故意的：箭头只是指向，不是放大图** (Intentional: the arrow only points, it is not a detail), or untick **指向放大图** in the arrow's properties.

3、**A block arrow covering parts or text:** tick **允许这支箭头覆盖元件／文字** (Allow this arrow to cover parts / text) in the block arrow's properties, or click **这是故意的** in the issue card.

4、After allowing, the Issues tab shows a blue-grey note; click **撤回** (Withdraw) to restore the check. These settings affect only checking and drawing; the netlist and the SPICE, Simulink and PLECS exports are unaffected.

### 11.7 Checking from the command line

```bash
circuit-drawer check my.json             # JSON report; exit code 1 if it fails
circuit-drawer check my.json -p double
```

The report lists part IDs, coordinates and reasons, for example `Lr: pins (3.35, 2.5) and (4.5, 2.7) are not on one horizontal or vertical line`.

---

## 12. Regions, blocks, arrows, layers and step and pulse marks

All of these are drawing aids. They take no part in connections or the netlist.

![Regions, stacked panels, blocks and signal lines](images/manual/en/regions-blocks.png)

### 12.1 Regions (coloured backgrounds and dashed frames)

1、Choose **Insert → Annotations → Draw region** and drag a rectangle. Select it to move or resize it. New regions remember the previous fill, border style and border colour. **White fill, black border** applies that preset. The browser remembers these settings.

2、**Properties:** fill (green, blue, orange, red, purple, yellow, cyan-green, grey, a custom `#rrggbb`, or none), border (none, solid, dashed, dotted), border colour, label and its position (below, above, left, right, inside at the top) and label size. Custom colours that are too dark are lightened automatically so black lines and text stay legible.

3、**背后底板数** (Panels behind): 1–6 adds that many panels behind the region (offset 0.25 up and to the right by default), often used for "draw phase a, let panels stand for phases b and c". 0 means none. It does not copy any circuitry and is not a layer position.

4、**叠面板用途** (Purpose of stacked panels): drawings that will be simulated must declare whether the panels are decoration only or stand for omitted circuitry (listing the instance IDs, including the front panel). In the second case, if the real circuits are not defined, the SPICE export lists the missing instances and stops instead of exporting only the drawn phase.

5、**以框内元件设为成员** (Use parts inside as members) records the parts entirely inside the frame as members. From then on the checker verifies that members stay inside and warns about unlisted parts inside the frame.

6、**色块贴合成员** (Fit region to members): a region with `"fit": "members"` in the table takes its boundary from its members' symbols and labels (their union plus 0.3 layout units, rounded outwards to the 0.05 grid). The button fixes the current boundary as an ordinary rectangle that you can then adjust; `Ctrl+Z` restores automatic fitting. Example: `examples/regions/dab_multi_fit.json`.

![Stacked panels and a zoomed detail](images/manual/example-regions-mmc.png)

### 12.2 Blocks (control part)

1、**插入 → 标注 → 画方框** (Insert > Annotations > Draw block), then drag a rectangle. On the right you can set the text (LaTeX allowed, for example `$f_r$ calculation`), fill (white, none, a palette colour), border (solid, dashed, dotted), colour, text size, and the lower-left and upper-right corners.

2、Draw signal lines between blocks with **画箭头** (Draw arrow), using the thin arrow style in black.

3、Checks: blocks may not cover parts, wires may not cross blocks, blocks may not overlap each other, the text must fit (0.06 cm clearance on all sides), and thin arrows must end at a block's edge.

4、Complete example: `examples/control_blocks/llc_cells_dsp.json` (multi-module LLC with DSP control).

![Exported control blocks](images/manual/example-control-blocks.png)

### 12.3 Arrows

![Block arrow properties](images/manual/en/arrow-properties.png)

1、Choose **Insert → Annotations → Draw arrow** and drag from the start to the tip. Available styles are block, thin line / arc / polyline, and wave. New arrows remember the previous style, with block as the initial style.

1a、Choose **Insert → Annotations → Draw signal arrow** for a thin line. Click the start and each corner, then click the endpoint again or press Enter to finish. Shift constrains the next segment horizontally or vertically. Esc cancels. Dragging from start to end creates a straight line.

1b、While drawing or dragging endpoints and corners, labelled guides snap near horizontal, vertical, parallel or perpendicular alignment with existing arrow or wire segments. Hold Alt to bypass guide snapping. Choose **Sharp / Rounded** under **Corners** in Properties. New arrows remember the choice. Rounded corners shrink for short segments and appear in saved drawings, previews and exports.

2、**Thin arrows:** drag the middle handle to bend the arrow (or type −120 to 120 in **弯曲（度）**, Bend in degrees); **恢复直线** (Straighten) removes the bend. Corners can be added, dragged and deleted to route long arrows. Line style (solid, dashed, dotted), arrowhead position (end, start, both, none) and an isolation mark (two short bars, with adjustable position along the line) are available.

3、**Wavy arrows:** amplitude and number of waves (1–8) can be set; bending and corners cannot be combined with them. They are annotations, not simulated waveforms.

4、**Labels:** position above, below, left or right; thin arrows can also use **inline** (`inline`), which places the text on the path and breaks the line on both sides. The position along the path is a number between 0 and 1.

5、**Block arrow appearance:** fill colour, border colour (including **无边框**, no border, and **默认（随填充）**, default following the fill), border width (0.1–3 pt), fill opacity (0 transparent, 1 solid; fill only).

6、**画在电路上面** (Draw above the circuit): block arrows are below the circuit by default; when ticked they are drawn above the circuit and other annotations. If the arrow really must cover parts or text, also tick **允许这支箭头覆盖元件／文字**. The two do different jobs: one controls what is on top, the other records that the covering is intentional. PDF keeps transparency; converting to EPS or some print workflows flatten it, so check again.

7、**Zoomed details:** when a block arrow starts outside a region and points into it, the region is treated as a zoomed detail and no wire may run into it from outside; otherwise the checker reports "detail wired to circuit". If the region is just a background, untick **指向放大图**.

Examples: `examples/arrows/paper_arrows.json`, `examples/regions/buck_arrow_styles.json`.

![Arc, wavy and polyline arrows](images/manual/example-arrows.png)

### 12.4 Layers

![Layer menu of a region](images/manual/en/layer-context-menu.png)

1、Right-click a region, block or arrow and choose **上移一层** (Raise), **下移一层** (Lower), **置顶** (Bring to front) or **置底** (Send to back); or select it and use **编辑 → 同类对象图层** (Edit > Layer among objects of the same kind). Shortcuts: `Ctrl+]` raise, `Ctrl+[` lower, with Shift for front and back.

2、Order changes only among objects of the same kind. The fixed order between kinds is: regions → ordinary block arrows → blocks → wires → parts → text and standalone marks → function marks → thin and wavy arrows; block arrows with "draw above the circuit" ticked are on top.

3、**置顶** does not turn an ordinary block arrow into a foreground arrow. Changes can be undone and are kept when saved. Example: `examples/regions/layer_order.json`.

### 12.5 Step and pulse marks

1、**Source waveforms:** select a voltage or current source and choose a rising step, falling step or pulse under **源旁波形** (Source waveform). Position (opposite the label, left, right, above, below) and size (0.5–2) can be changed. The source keeps its polarity or current arrow.

2、**Standalone marks:** **插入 → 标注 → 阶跃 / 脉冲标记** (Insert > Annotations > Step / pulse mark) places a figure symbol without electrical pins. It can be dragged, recoloured, resized, deleted and undone.

3、These are graphics only. The real excitation values and times are entered in **仿真 → SPICE 仿真…**; changing the drawing never changes the simulation parameters, and vice versa. Example: `examples/source_waves.json`.

![Source waveforms and standalone marks](images/manual/example-source-waves.png)

### 12.6 Black-and-white printing

**视图 → 色块灰度** (View > Grey regions; `--mono` on the command line) replaces region and arrow colours by distinguishable greys. Part colours print black in black-and-white output.

### 12.7 Free text, function marks and terminals

1、Choose **Insert → Annotations → Add text**, click a position and enter text such as `$i_x$`, `$v_{mv,dc}$`, `O'` or `$\vdots$`. Select the text to change its content, font size, colour or position. Undo and save/reopen are supported.

2、Choose **Insert → Annotations → Draw function mark** and drag its bounds inside an existing box. Select AC/DC, DC/DC, DC/AC, MOSFET, IGBT or diode in **Icon**. These marks are saved in `glyphs` and support moving, resizing, copying and deleting.

3、Select a terminal and tick **Hide terminal circle (at a box edge)** to set `opts.bare`.

4、Region border colours accept ink names such as `black`. With `fill: none` on the front region, unspecified back panels use white. This default also applies to saved drawings. Use `stack.fills` to specify individual panel colours.

![Insert text and function marks](images/manual/en/drawing-tools.png)

Open [drawing_marks.json](../examples/editor/drawing_marks.json) for an example.

---

## 13. Colouring parts and text

Papers often mark a device and its currents and voltages in magenta or blue.

1、**In the editor:** select a part or free text and pick a colour under **颜色** (Colour), or use the colour picker for a custom `#rrggbb`; choose **黑（默认）** (black, default) to reset.

2、**Effect:** a coloured part draws its symbol, label and current arrow in that colour; coloured free text colours the text and its current arrow. Wires and junction dots are always black.

3、**Available colours:** `magenta`, `blue`, `red`, `green`, `orange`, `purple`, `teal`, `gray`, `black`, or `#rrggbb`; Chinese names such as 品红, 洋红, 蓝 and 红 are also understood. These are dark inks for white paper, separate from the light palette used for regions.

4、**Interaction with other features:** `--mono` prints everything black; mode figures colour parts by conducting / idle state and ignore part colours, while text colours are kept.

5、**JSON:** parts use `"opts": {"color": "magenta"}`, free text uses `"color": "blue"`.

---

## 14. Modules

The SST examples include an input-series, output-parallel two-stage circuit and a single-stage circuit with SM cells and a resonant IDC. Double-click blocks to inspect their components. The vertical SM chain uses side ports. PDF/PNG figures, details generated from the same definitions and a separate DAB startup example are included. Whole-system simulation requires power-stage values and control parameters. Drag external capacitors, sources or loads to adjust their position relative to module ports. Each expanded or collapsed view applies the saved offset to its own port positions. Edit horizontal and vertical offsets or labels in Properties. Arrow keys, undo, redo and save/reopen are supported. [SST example instructions](../examples/sst/README.md#english)

Modules let you define a repeated converter cell once and place it several times. All instances share one definition; per-instance parameter differences are stored separately. Folding changes only the picture; simulation always expands every real part.

![Module instance, module library and context menu](images/manual/en/module-context-menu.png)

### 14.1 Module library

The 模块（子系统） (Modules) group at the bottom of the parts library has three sections:

1、**Built-in**: 13 modules, including Buck, Boost, DAB, LLC, MOSFET/IGBT half bridges, a MOSFET full bridge, NPC leg, LLC tank, diode full-bridge/center-tapped rectifiers, and half-/full-bridge submodules. Hover over an entry for its ports: a half bridge exposes P/N/O, NPC exposes P/M/N/O, and center-tapped rectifier ports C and ON share the same net.

2、**我的模块库** (My module library): modules you saved, usable from any circuit file. They are stored in `~/.circuit_drawer/templates/modules/` (or the `modules` folder under `CIRCUIT_DRAWER_TEMPLATES`), one JSON per module. Right-click an entry to delete it.

3、**本文件** (This file): module definitions already in the current file.

**插入 → 添加模块…** (Insert > Add module) lists the same three sections.

4、Run `circuit-drawer blocks` for library names, ports and one-to-one drawing sizes including labels. Specify device models and gate timing before simulation; the new tank and submodule capacitor parameters are demonstration values. Open the [two-LLC IPOS example](../examples/modules/llc_ipos_blocks.json) to switch between collapsed, expanded and partial views. Its top-level file contains only library references and external port connections.

![Module building-block library](images/manual/en/module-building-blocks.png)

1. Drag with the left mouse button on blank canvas, or Shift-click to select at least three module instances. Right-click **Omit middle instances** to keep the first and last along the main arrangement direction and show ⋮ or ⋯ between them. Package ordinary components as modules first, and deselect ordinary components, wires and shapes from a mixed selection.

2. Right-click either end instance or the dots and choose **Restore all instances**. Omission and restoration each undo in one step. Hidden instances remain in the complete checks and simulation; external taps keep their actual instance terminals. Example: [four-cell bridge arm](../examples/modules/mmc_arm_ellipsis.json).

3. The module JSON dialog and crowded-drawing advice show a **Size budget**, including labels and regions in the expanded drawing and estimated horizontal counts for single and double columns. Actual wiring still needs validation. Column width does not set a page-height limit, so vertical counts have no fixed maximum.

![Right-click selected instances to omit their middle](images/manual/en/module-ellipsis-select.png)

![Omitted instances and restore action](images/manual/en/module-ellipsis-omitted.png)

### 14.2 Placing and connecting

1、Drag a library module onto the canvas and release to place it. Clicking a library entry instead shows a dashed placement preview; click the canvas to place it.

2、Click a port to begin a wire, then click the green triangle near another port to connect. The **Wire** toolbar button also supports point-by-point drawing. You may draw wires first, place the module first, or add components later.

3、While dragging a module, its body, labels, pins and connected wires move together. Press Esc to cancel the drag; connections are checked on release. You can move the corners or segments of ordinary canvas wires, split them, or delete them. Connected endpoints follow ports when a module moves, expands, folds, or changes box size or title. Other corners are retained where possible. When a port lead collides with a nearby wire during movement, the editor tries adjusting its bends into available gaps while preserving existing branch junctions and wire hops.

4、Unconnected pins remain listed in **Issues**. After completing new wiring, review the connections and use **Record new pins from current connections** when prompted. For an intentional change to existing connections, use **Update netlist from drawing** after reviewing it. A wire hop remains insulated.

5、Placing a library module copies its definition into the drawing. Repeated instances reuse that definition, and the saved file carries the actual circuit to another computer.

6、Select legacy `modules.links` wires on the canvas to drag their routes or delete them. The connection properties include **Restore automatic routing**. Saving preserves their named references. Draw new connections with the canvas wire tool. See [the ordinary module-wiring example](../examples/editor/module_wiring.json).

### 14.3 Expanding, folding and appearance

1、**Double-click** an instance, or right-click and choose **展开 / 折叠** (Expand / fold), to cycle through "expanded → folded into a box → partly folded (where supported)". The **视图 → 模块显示** (View > Module display) submenu selects instances and expands or folds all of them.

1a、Expansion measures symbols, labels and wires and makes space when needed, moving surrounding objects and stretching external wires while preserving definitions, parameters and the complete electrical connections. Properties, Expand All and double-click use the same operation. Undo restores the previous layout in one step; only Save writes the file. Folding retains the added space. If space cannot be made without new conflicts, the original drawing is kept with an explanation. If only the fixed column width is exceeded, adjust circuit scaling or restore automatic paper scaling before export.

![Space around the expanded module, bus and inductor](images/manual/module-expand-clearance.en.png)

2、**Partly folded:** currently only the LLC half bridge supports it; the switching half bridge and the rectifier bridge each shrink into a small box with a device symbol, while the resonant tank and the transformer are still drawn.

3、**Box size:** select a folded box and drag the small square at its lower-right corner; on release it snaps to the grid and is checked. If the new size cannot be routed, the reason is shown and the old size is kept. **视图 → 模块显示 → 方框尺寸… / 恢复默认方框尺寸** (Box size / Reset box size) do the same from the menu.

4、**Symbol in the box:** IEC 60617 converter symbols `dcdc`, `acdc`, `dcac`, device symbols `mosfet`, `igbt`, `diode`, or just the name, `text`.

5、Select an instance to set its folded title, fill, border style and colour. Clear **Title text** to hide the title, or drag the title to position it independently. Its offset follows the module and supports undo, saving and reopening.

6、**Group display:** tick other instances of the same definition to draw only this instance, with the others shown as offset panels behind it; the simulation still includes them.

7、Fully expanded, fully folded and partly folded drawings export line-for-line identical SPICE netlists.

![Exported folded modules](images/manual/example-modules-folded.png)

### 14.4 Editing the shared definition and instance parameters

1、Select a module and press `Enter`, or choose **Edit module definition** from its context menu. Edit parts, wires and labels on the internal canvas, then choose **Set ports and save…**. Click the nodes to expose, enter port names and sides, and choose **Save definition and update all instances**. Finally use **Save** to write the file. **Cancel definition editing** discards this definition edit.

2、You do not need to place port components in advance; existing ones are reused. Two ports chosen on the same electrical node remain connected. New ports start unconnected; existing ports used by instances cannot be deleted or renamed directly.

3、The definition name can be changed at the top of the dialog. Start with an English letter and use letters, digits, underscores or hyphens. All referencing instances update together, preserving connections and parameter overrides.

4、**Instance parameters…** changes only this instance. Untick an override to restore the shared default. Only parameters declared editable in the definition can differ per instance.

5、**Dissolve into ordinary parts** returns the internal components to the current canvas. You can finish configuring newly added components after dissolving. Fill in their required values in the simulation dialog before running or exporting a simulation model.

### 14.5 Saving existing circuitry as a module

![Save as module](images/manual/en/save-module-dialog.png)

1、Box-select or Shift-click the parts to package, right-click and choose **存为模块…** (Save as module), or use **文件 → 存为模块…**.

2、Enter a module name, for example `Buck` or `LLC_HB`.

3、The dialog shows the same drawing. Red circles are nodes still connected to the outside: click one to make it a port, type a port name (such as P, N, OP, ON) and choose left or right; drag port rows or use the up and down arrows to reorder them. The module cannot be saved while an outside connection is unmarked.

4、**元件参数** (Part parameters) lists the simulation parameters the inner parts support; tick those that may differ per instance. Physical values that were never set are not guessed.

5、To reuse the module in other files, tick **同时存进我的模块库（其他电路文件也能用）** (Also save to my module library).

6、Click **保存并原地替换** (Save and replace in place). The selection becomes one instance; part IDs and probes stay the same, and the SPICE netlist before and after is line-for-line identical. Any electrical change makes the operation fail.

7、Encapsulation and dissolving update the device references in current modes, which stay editable. Modes archived by older versions are restored after the last instance is dissolved only when there are no current modes.

### 14.6 Library versions, dissolving and limits

1、With an instance selected, **存进我的模块库…** saves it to your library; an existing entry with the same name is overwritten only after confirmation.

2、When a library module changes, files that use it are not changed silently. The Properties tab shows **模块库里有新版本** (newer version in the library); only **更新本文件的定义** (Update this file's definition), or **从库更新** in the context menu, replaces it, updating all instances together; you are warned first if the file's copy was also edited. A new version with renamed ports cannot be applied directly.

3、**解散为普通元件** (Dissolve into ordinary parts) expands this instance into ordinary parts; other instances and the library entry remain.

4、Modes can cross module ports; reference internal devices as M/S1 or M/T:p. Modules still have one level. Edit internal part positions in the definition, or drag the whole instance.

5、Examples: `examples/modules/library_boost_buck.json` (built-in Boost and Buck in cascade), `examples/modules/boost_parallel_authored.json` (two Boosts in parallel), `examples/modules/llc_ipos.json`, `examples/modules/dab_ipos.json`, `examples/modules/llc_partial.json`.



1、**Module modes:** open the [parallel Boost example](../examples/auto_modes/module_boost_bound.json) and select a mode in the Modes sidebar. Expanded instances show their conducting parts and wires. A highlighted folded box means an internal part participates in the mode; external wires follow the actual loop. Group frames summarize hidden members while visible devices retain their own instance states.

2、Expand an instance, enable **Edit modes on canvas**, and click a switch or diode to change its state, or an arrow to reverse its loop. Partial blocks map hidden devices through their port networks; expand the instance when that mapping is ambiguous. Canvas, compiled preview and exports use the same result. Identify again when the simulation source is out of date. Wires follow the pointer during dragging; mode highlighting returns when the new position is accepted.

![Mode across module ports](images/manual/en/module-modes.png)

### 14.7 Starting with an empty subsystem

1、Right-click empty canvas, choose **New empty subsystem**, and place its box. Select it and press `Enter` to open the internal canvas.

2、Add components and wires. Register newly connected pins when the Issues tab requests it, then use **Set ports and save…** to define external ports and parameters.

3、Return to the parent drawing and connect ordinary wires. An empty subsystem can be saved and reopened, but remains a draft until its actual circuit is complete. Only one subsystem level is supported.

---

## 15. Operating-mode figures

One base drawing, one figure per operating mode: conducting parts and the wires between them turn red and bold, the rest turns grey, and arrows show the current direction. Only the style changes, never the coordinates, and each figure is checked as usual.

![Modes tab and editing modes on the drawing](images/manual/en/modes-panel.png)

### 15.1 In the editor

1、Open the **模态** (Modes) tab and switch modes with the list or ◀ ▶ (keys `[` `]`); the canvas shows the highlight and arrows directly. The first list entry, **（不显示模态，正常编辑）** (no mode, normal editing), returns to ordinary editing.

2、**在图上改模态** (Edit mode on the drawing): click a switch to cycle "off → channel on → body diode on → off", click a diode to toggle it. If a newly conducting device is not in any loop, the shortest closed loop through it (in its conducting direction, with parallel branches merged) is added; loops through devices that stop conducting are removed. Click an arrow to reverse its whole loop. Dragging is paused while editing modes.

3、**复制为新模态** (Duplicate as new mode) inserts a copy of the current mode after it, so you only change what differs. Expanding **编辑当前模态** (Edit current mode) also offers **新增空模态** (New empty mode) and **删除当前模态** (Delete current mode), and lets you edit the JSON (`on`, `loops`, `caption`, `condition`) directly; click **应用** (Apply).

4、**模态真实预览** (True preview of mode) shows the LaTeX result; **导出全部模态** (Export all modes) writes every mode and an overview sheet in one go. Tick **黑白打印样式（黑色粗线 + 灰色虚线）** (black-and-white style: bold black lines and grey dashed lines) for a monochrome version.

5、Modes refer to part IDs, so they follow the base drawing when you move parts or reroute wires. If a deleted part or changed wiring breaks a mode, the Modes tab lists **模态 N 已失效** (mode N is invalid) with the reason; click it to jump there.

6、The **?** beside the 模态图 (Operating modes) heading holds what a mode consists of, what is checked, the rules for editing on the drawing and the JSON syntax; click it to read. The small tag before the operating condition shows where the mode came from: 仿真 (Simulation) for **从仿真识别模态** (Identify modes from simulation), 手写 (Manual) for modes written by hand or edited on the drawing. Only loop connectivity and device conduction direction are checked, not whether bias and operating conditions allow the mode.

### 15.2 Command line

```bash
circuit-drawer modes llc -o figs                 # full-bridge LLC (fs < fr): 6 modes and an overview
circuit-drawer modes llc fs=above                # fs > fr; fs=resonance at resonance
circuit-drawer modes dab --mono                  # black and white: bold black lines, grey dashed rest
circuit-drawer modes flyback 二极管在下 --only 1,2 -f pdf
circuit-drawer modes llc --list                  # print the mode definitions only
circuit-drawer modes fig.json --modes my_modes.json   # your table, your modes
```

1、Each mode is written to `<name>_mode1.pdf`, `_mode2.pdf`, ...; `<name>_modes` puts all modes on one page, each with (a), (b), ... and its caption, two single-column figures per row, which is exactly double-column width. `--no-sheet` skips the overview.

2、`--color` changes the highlight colour (default `#c8102e`; xcolor names such as `blue!70!black` also work); `--off dashed` draws idle wires dashed; `--arrows N` sets the number of automatic arrows per loop (default 3, 0 for none).

3、Highlighted lines are twice as wide as normal lines and grey parts are 35 % black, so colour figures remain readable when printed in black and white. Part labels and texts such as vo, +, − stay black.

### 15.3 Built-in sequences

| Template | Modes |
| --- | --- |
| `llc_fullbridge` | `fs=below` (default, 6), `fs=resonance` (4), `fs=above` (6), for both centre-tapped and full-bridge rectifiers |
| `dab` | Single phase shift, primary leading, continuous current, 4 per half period |
| `flyback` | S on, magnetising; S off, energy released through D; DCM idle interval (DCM only) |
| `buck`, `boost` | 2 ideal CCM modes each |

When a switch carries reverse current, a MOSFET is drawn with its channel conducting (synchronous rectification) and an IGBT with its anti-parallel diode conducting. The built-in sequences are textbook drawings for common operating conditions and may not match your paper.

![DAB mode sheet](images/manual/example-dab-modes.png)

### 15.4 Writing your own modes

The table's `modes` list gives the conducting devices and the loops of each mode:

```json
{"name": "Mode 1", "caption": "$[t_0, t_1]$: $S_1$, $S_4$ on",
 "on": ["S1", "S4", "D1"],
 "loops": [["Vin", "S1", "Cr", "Lr", "T:p|Lm", "S4"],
           ["T:s1", "D1", "Co|RL"]]}
```

1、A loop lists its branches in the direction of the current; the last one connects back to the first. The program checks that neighbouring branches share a node and that the loop closes, then follows the real wiring to find the wire pieces between the pins to highlight.

2、Transformer windings are `T:p` and `T:s` (`T:s1`, `T:s2` for a centre tap); parallel branches are `Co|RL`; a switch conducting through its body diode is `S2:diode`.

3、For a loop of just two parallel parts, the direction is from the first part's first pin to its second pin. For example `["RL", "Co"]` means Co discharging into RL.

4、You may give `on` without `loops`: the program highlights every device on a closed path, but that is only a topological path, so no arrows are drawn and a hint asks you to confirm.

5、If you do not like the automatic arrows, place them yourself with `"arrows": [{"at": [1.6, 2.5], "dir": "right"}]`.

6、`condition` states the operating condition, such as `"$f_s < f_r$"`, shown by `--list` and in the editor.

### 15.5 What is checked

The program checks loop connectivity (names exist, neighbouring branches share a node, the loop closes, switch conduction is consistent) and warns when a diode or an IGBT channel is used in reverse. It does not check device bias, current magnitude, or whether the mode really occurs under your conditions.

---

### 15.5 Identifying modes from simulation

1、Configure device values, PWM and transient settings in **Simulation → SPICE simulation…**. Open the **Modes** sidebar and choose **Identify modes from simulation…**. Supported ordinary and modular circuits use fixed-frequency open-loop ideal MOS switches, forward diodes, RLC components and ideal transformers. Examples cover Buck/Boost CCM and DCM, three LLC frequency conditions and DAB. Phase soft-start must finish before the analysis window.

2、Click **Simulate and identify**. Recognition checks phase-matched inductor, winding and internal magnetizing currents, capacitor voltages, state order and boundaries over the final eight complete cycles. A drift message calls for a longer run or a review of initial conditions. The current threshold is the greater of the absolute floor and the configured fraction of peak current. Selected inductor and winding currents distinguish positive, negative and near-zero states. Winding p runs p1→p2, s runs s1→s2, and center-tap halves s1 and s2 run s1→sct and sct→s2.

3、Each candidate shows intervals, duration, switch states, branch currents and comparisons with existing modes. **Preview conducting paths** compiles the actual mode figure. Short commutation and threshold intervals remain available for individual selection. An all-devices-off DCM candidate can still show the capacitor supplying the load. If an inductor or winding current opposes the drawn loop direction, the candidate hides direction arrows and identifies the conflicting branches. Review the measured currents before adjusting the mode manually.

4、Select candidates, edit names and captions, choose **Append** or **Replace existing modes**, then click **Apply selected modes**. Names must be unique. Select **Apply this run's waveform and interval bindings too** to use the same run's CSV, complete period boundaries and confirmed mode names. **Preview waveform and boundaries** shows the result before applying. Intervals for unchecked candidates remain unbound. Modes and waveform settings are applied and undone together; **Save** writes the file. An existing waveform is kept by default. Explicitly enabling replacement replaces its settings and annotations. Without replacing the waveform, append modes to preserve its existing references.

4a、Changes to the circuit, device models or simulation settings leave a persistent notice at the top, and the mode source reads **Simulation (out of date)**. Previous results remain available for comparison. **Identify again…** restores the original recognition settings; run again, review and confirm. Undoing parameter changes restores the corresponding source status. Bind every interval before exporting the combined overview, and regenerate outdated waveforms or bound simulation modes first.

5、The four Buck/Boost examples in `examples/auto_modes` run for 80 ms. DCM examples specify capacitor initial voltages of 14.8 V for Buck and 67 V for Boost. Run `circuit-drawer modes-from-sim examples/auto_modes/buck_ccm.json -o output/modes` to export candidate JSON, the original user-probe CSV, a hidden-probe window, netlist and simulation log in a separate run subfolder.

6、The four files in `trial/cases` retain the twenty handwritten modes and include 25 ms simulations with a 100 ns maximum step. LLC uses 80, 100 and 130 kHz with a 100 kHz tank resonance and a 12 Ω load. DAB uses 50 kHz, a 30° phase shift, a 7.2 Ω load and a 47 Ω / 2.2 nF series RC across each switch. All four cases use 48 V input, 200 ns dead time and 1 nF numerical node capacitance. Review the complete period, current zero crossings and short commutation intervals against the handwritten conducting sets and directions. If a candidate reports current outside the drawn closed loops, check the measured currents and the model capacitance or snubber branches.

![Simulation-derived mode candidates](images/manual/en/auto-modes.png)

7、Crowded boundary labels use ranges such as t0–t8. Every boundary and interval remains intact. Open `examples/auto_modes/buck_bound.json` for a confirmed, bound example.

![Waveform and boundaries](images/manual/en/auto-mode-waveform.png)

![Simulation source notice](images/manual/en/simulation-stale.png)

## 16. Waveforms and modes

This feature lines up simulated or measured waveforms with mode figures: t0, t1, ... mark the waveform, and each interval is bound to one mode.

![Waveforms and modes panel](images/manual/en/waveforms-panel.png)

### 16.1 Opening the example

```bash
circuit-drawer edit examples/waveforms/boost.json
```

Click **波形与模态** (Waveforms and modes) on the toolbar, or **模态与波形 → 波形与模态…**. The example already has two intervals bound, and no simulator is needed. Click `t0→t1` and the base drawing above switches to the inductor-charging mode; click `t1→t2` for the energy-transfer mode.

### 16.2 Importing a CSV file

1、Click **导入 CSV…** (Import CSV), choose a local file or type a path, and select the time column and its unit.

2、Format: the first row holds column names, followed by numbers; UTF-8 (a BOM is allowed), separated by commas, semicolons or tabs; at most 64 MiB, one million rows and 32 columns; strictly increasing time. Missing columns, non-numbers, NaN, infinity and repeated times are reported.

3、Under **通道、标签和单位** (Channels, labels and units), choose 1–6 curves to show and set their labels and units. Use English maths labels such as `i_L` or `v_out`; subscripts are typeset automatically.

4、Imported CSV files are copied into a `waveforms-data/` folder next to the JSON, with a content hash in the file name. Share the JSON together with that folder. The JSON stores only the path, channels and intervals, not the samples.

4a、Set matching **Y-axis group** values for channels with the same units, then apply. Curves share their actual value range and a wrapping colour legend; **Separate Y axis** restores separate rows. Units must match exactly; no automatic conversion or normalization is applied. Grouping supports undo and survives saving and reopening.

### 16.3 Choosing the time window and boundaries

1、Type the start and end times and click **应用范围** (Apply range), box-zoom on the curves, or use **放大 / 缩小 / 全范围** (Zoom in / out / Full range). The display unit can be s, ms or us; by default the time axis starts at zero at t0, while range and boundary inputs always use the CSV's absolute times.

2、Choose a PWM channel and click **末尾一个 PWM 周期** (Last PWM period) to get boundaries suggested from the last two rising edges and the falling edge between them. PWM only helps choose the window; it does not identify modes.

3、Drag the boundary lines, or expand **精确设置分界时间** (Exact boundary times) and type values; **拆分所选区间** (Split selected interval) adds a boundary. Boundaries may not coincide or cross.

4、Choose a mode for each interval in its list. Clicking an interval shows the corresponding current path on the base drawing. Click **保存对应关系** (Save bindings) to write them to the JSON.

### 16.4 Annotations

1、The **标注** (Annotate) buttons above the panel are: **文字** (Text) places text with one click; **箭头** (Arrow) is dragged from its tail to the point it marks; **关键点** (Key point) places a small hollow circle, optionally with text, at peaks, zero crossings and so on.

2、Text may contain English and `$maths$` only (for example `$i_L$ peak`, `$I_{L,\max}$`), no Chinese.

3、Click an annotation to drag it, drag either end of an arrow, or drag its text alone; double-click to edit the text; **删除所选** (Delete selected) or `Delete` removes it; **撤销** (Undo) restores. The t0, t1, ... and `Mode k` labels can also be dragged aside without moving the boundaries.

4、**标注属性** (Annotation properties) sets colour, text size (5–20 pt), line width (0.1–2 pt), circle radius (0.02–0.3 cm) and text offset; click **应用属性** (Apply). With an annotation selected, the arrow keys move it by 0.01 cm, Shift plus arrow keys by 0.1 cm.

5、The panel keeps the paper aspect ratio. **纸面尺寸** (Paper size) switches between **总览中的波形** (waveform in the overview) and **独立波形图** (standalone waveform figure); **查看比例** (View scale) only changes the on-screen magnification. Use **真实出图预览** (True output preview) to check the final maths rendering.

6、Click **AI annotations…** and enter “Mark the i_L peak with $I_{L,\max}$ and add an arrow on the rising ramp of the first interval labeled $V_{in}/L$.” The program calculates candidate points from the current CSV, selected channels and time intervals, then uses your current AI settings to generate annotations. Review the preview and click **Apply annotations** to append them. One undo removes the whole batch. **Save mode mapping** writes the result to the file.

7、The AI receives a candidate-point summary containing interval boundaries, extrema, zero crossings and the midpoint and slope of the longest continuous rising/falling ramp. Each interval supplies up to eight zero-crossing candidates. The total annotation limit is 40. Regenerate after changing channels, intervals or annotations. Labels use English or mathematical notation and can be adjusted in **Annotation properties**.

![Waveform AI annotation preview](images/manual/en/waveform-ai.png)

8、**Interval dimension** draws a horizontal span: drag from its start to its end and enter a label such as `$D T_s$`. Drag either endpoint to change the span, or move the entire dimension without changing its duration. Short spans place the arrows outside. Text, line width, offsets, deletion and undo use the existing annotation controls. AI dimensions take both times from real candidates on the same channel.

9、Open `examples/waveforms/interleaved_axes.json` for overlaid inductor currents and an interval dimension. Its CSV is an ideal analytical reference, not a new switching simulation or measurement.

![Shared axes and interval dimensions](images/manual/en/waveform-overlays.png)

### 16.4a XY / state plane

1、Click **XY / state plane…** and select the X- and Y-axis channels. Each point pairs values at the same time, using the waveform panel's window and boundaries. Arrows show increasing time; filled and open circles mark the start and end. Unbound intervals are grey. Direction arrows and mode colouring are optional; values and units are unchanged.

2、Previewing does not change the drawing. **Apply to current drawing** supports undo; **Save mode mapping** writes the settings to the file. Export adds an `_xy` figure, or choose **Export XY plot only**. **Remove XY plot** keeps time waveforms and CSV data. XY always uses the standalone paper width; time-domain annotations are not projected onto it.

3、`examples/waveforms/boost_state_plane.json` reuses the existing Boost simulation CSV. The plot does not infer stability, force the trajectory to close or normalize the values.

![XY and state plane](images/manual/en/waveform-state-plane.png)

### 16.5 Export

1、Click **导出波形与模态…** (Export waveforms and modes), choose the output folder and formats (only SVG by default; SVG or PNG for Word, PDF for LaTeX; the TeX source is a separate option).

2、For `boost`, the output is:

| File | Content |
| --- | --- |
| `boost_waveforms.<format>` | Standalone waveform figure with the t0, t1, ... boundaries |
| `boost_mode1.<format>`, ... | One figure per mode used |
| `boost_waveform_modes.<format>` | Waveforms and modes overview |

3、**波形按模态切片** (Shade waveforms by mode, on by default) shades alternate intervals light grey and labels them `Mode 1`, `Mode 2`, ... (numbered in mode-list order, matching the `_mode1` files). Untick it to draw only dashed boundaries and t0, t1, ....

4、**总览排版** (Overview layout): stacked (default, single or double column), side by side (waveforms left, modes right, double column only), or two columns (waveforms on top, mode figures two per row below). The last two draw each mode once, with a caption listing all its intervals.

5、Untick **包含对应模态与总览** (Include modes and overview) to export only the waveforms. Files are copied to the target folder only after everything compiles; on failure the previous output is kept.

![Waveforms and modes overview](images/manual/example-waveform-modes.png)

### 16.6 Command line

```bash
circuit-drawer waveforms examples/waveforms/boost.json -o out/boost-waveforms -f svg,pdf
circuit-drawer waveforms examples/waveforms/boost.json -o out/boost-waveforms -f png --wave-only
circuit-drawer waveforms examples/waveforms/boost_side.json -p double -o out/boost-side -f png
```

### 16.7 After re-simulating

1、After importing a new CSV, channel and interval bindings that still apply are kept; if you used **末尾一个 PWM 周期**, the boundaries are recomputed.

2、After changing only labels, positions or symbol sizes of the base drawing, you can export again directly. If connections, part kinds or simulation parameters change, the program asks you to import data for the new conditions; a modified CSV snapshot also blocks export.

3、For display and export, large files are reduced per bucket to the first, minimum, maximum and last points, so peaks are never smoothed away; the raw data is untouched.

---

## 17. SPICE simulation

![SPICE parameters and export window](images/manual/en/spice-dialog.png)

### 17.1 Preparation

1、Writing a `.cir` file does not need ngspice. Running a simulation needs ngspice installed (see [chapter 2](#2-installation-and-startup)), with its path set before the editor is started.

2、Parameters are stored in the `simulation` field of the current table. The program never derives values from text labels, and every part needs parameters.

### 17.2 The window

**仿真 → SPICE 仿真…** opens the **SPICE 参数与导出** (SPICE parameters and export) window:

1、**载入 Buck 示例参数（含多相交错）/ 载入 Boost 示例参数** (Load Buck example parameters, including interleaved / Load Boost example parameters): checks the topology and fills in example values. For an interleaved Buck, each phase runs at 50 kHz with duty 0.5, and phase k starts k/N periods later. Only the form changes; **保存参数** (Save parameters) writes the circuit file.

2、**推荐仿真参数…** (Recommend simulation parameters): see section 17.6.

3、**参考地（引脚）** (Reference ground, a pin): choose a real pin; its net becomes SPICE ground.

4、**一、元件与驱动参数** (Part and drive parameters): one group per part; see section 17.3.

5、**二、瞬态设置** (Transient settings): output sampling interval, stop time, maximum integration step, and numerical stabilising capacitance (from every net to simulation ground; default 0, off; it is not a device parasitic). The rules are 0 < maximum step ≤ sampling interval < stop time, at least 50 samples per PWM period, and at least 10 integration steps in each on and off interval. Simulation starts at time 0 from the inductor and capacitor initial conditions, without a DC operating point.

6、**三、测量通道（可修改 JSON）** (Probes, editable as JSON): voltage probes name a positive and a negative pin; current probes name a part (a→b for D, L and scr; b→a for V and vac; c→e total terminal current for igbt; transformers also need a winding); drive probes name a switch and output the normalised 0/1 PWM. Probe names use English letters, digits and underscores, start with a letter, and there may be up to 32.

7、**四、闭环控制（可选）** (Closed-loop control, optional): see [chapter 18](#18-simulink-and-plecs-export-closed-loop-control).

8、**五、输出文件夹** (Output folder): browse or type; relative paths start from the folder of the circuit file.

9、**保存参数** (Save parameters) only saves; **导出 SPICE** (Export SPICE) writes the `.cir` file; **导出并仿真** (Export and simulate) writes the `.cir`, a `.csv` with units in the column names, and the run log. The drawing is checked before export as usual.

10、After a successful run, the waveform panel opens with the new CSV loaded, ready for [chapter 16](#16-waveforms-and-modes).

![Waveforms loaded after a simulation](images/manual/en/spice-result.png)

### 17.3 Parts and parameters

| Part | Model | Main parameters |
| --- | --- | --- |
| R, L, C | Linear | Value; L and C need an initial value (L current a→b, C voltage a−b); optional series resistance for L |
| V, vac | Voltage source | DC value (b−a); optional internal resistance and inductance (in series at the + terminal b); transient excitation, see 17.4 |
| I | Current source | DC value (a→b); transient excitation |
| D | `generic_diode` | Is, N, Rs; Zener diodes also need breakdown voltage Vz at Izt, test current Izt and dynamic resistance Zzt |
| nmos, pmos | `ideal_switch` | Ron, Roff, PWM frequency, duty, start delay, rise and fall times; optional RC snubber across the switch, extra phase shift and its ramp time, linear output capacitance Coss, body diode parameters |
| igbt | `simplified_igbt` | Vce0, Ron, Roff and PWM; forward channel c→e, reverse current through the anti-parallel diode |
| scr | `simplified_thyristor` | Vf, Ron, Roff; the trigger pulse uses the PWM fields (`delay` is the first firing time); optional holding current, latching current and turn-off time |
| transformer | `ideal_transformer` | Full secondary turns ratio Ns/Np (each half of a centre tap is half of it), dot polarity, reference resistance (at least 1 GΩ); optional magnetising and leakage inductance referred to the primary |

1、Values are in SI units and may use suffixes such as `500u`, `50k`, `20m`, `1Meg`. Following SPICE, both `m` and `M` mean milli; mega is `Meg`.

2、The PWM duty is measured between the 50 % points of the rising and falling edges. PWM in the CSV is a normalised 0/1 control signal, not a physical gate voltage.

3、Three-phase sources: the window's **三相统一设置** (Three-phase settings) fills in the sine parameters of all three sources from the line-to-line RMS voltage, frequency, phase-A angle and phase sequence (peak = line voltage × √2 / √3); **保存参数** writes them.

### 17.4 Excitation

Voltage and current sources can be DC, step (rising or falling), repeated pulse or sine; `vac` must be sine.

1、Step: initial value, final value, delay and transition time (greater than zero); exported as `PWL`.

2、Pulse: additionally return time, plateau width and period; exported as `PULSE`.

3、Sine: offset, peak amplitude (not RMS), frequency, delay and phase (degrees; 0, −120 and 120 for three phases). Before the delay the output holds "offset + amplitude × sin(initial phase)". This is a time-domain sine, not a small-signal AC sweep.

### 17.5 Output files and command line

```bash
circuit-drawer spice examples/spice/buck.json -o out/spice/buck            # .cir only
circuit-drawer spice examples/spice/buck.json -o out/spice/buck --run      # run and write CSV
circuit-drawer spice-init my_boost.json --preset boost -o my_boost_sim.json
```

1、The first CSV column is `time_s`; the other column names carry units, such as `v_out_V`, `i_L_A`, `pwm_1`. The time axis is resampled to the chosen uniform interval.

2、The file set is written only when the simulation succeeds. If the engine fails, times out (120 s at most) or produces incomplete waveforms, earlier results are kept and the error is reported.

3、`spice-init` checks the basic structure against the real netlist (one each of V, L, C, R, D and nmos) and writes Buck or Boost example parameters into a new JSON, leaving the input untouched; add `--force` if the output already exists.

4、Without `-p`, the command first checks at single-column width; if that fails but double column passes, it checks at double column and says so. With an explicit `-p` it checks strictly at that width. The width never changes the exported model.

5、Runnable examples are in `examples/spice/`: `buck.json`, `boost.json`, `llc_fullbridge.json`, `dab.json`, `igbt_ac.json`, `three_phase_ac.json`, `zener_regulator.json`, `thyristor_rectifier.json`, `thyristor_bridge_3ph.json`, `battery_internal_resistance.json` and more.

### 17.6 Recommended simulation parameters

Expand **推荐仿真参数…** in the SPICE window:

1、Without requirements, the basic and interleaved Buck use 24 V in, 12 V out, 24 W, 50 kHz; the Boost uses 24 V in, 48 V out, 24 W, 50 kHz; inductor ripple is 30 % of the average current per phase and output ripple is 1 % of the output voltage. With only the input voltage given, the Buck assumes Vout = Vin/2 and the Boost Vout = 2 × Vin.

2、You can also enter input and output voltages, power, switching frequency and ripple, or describe the requirements in a sentence (text requirements use the AI drawing assistant's interface settings). For other expanded open-loop circuits, the AI proposes the parameters.

3、Click **生成推荐并检查** (Generate and check): the result lists the old value, proposed value, reason and assumption for each item, and runs a short ngspice check of at most 10 ms in a temporary folder.

4、After reviewing, click **采用到参数表** (Apply to the form), then **保存参数** or export to write the file; this can be undone. Recommendations use the current form values. Nothing is applied without ngspice, after a failed check, when the drawing or form values have changed since generation, or after cancelling.

5、These are first drafts from ideal CCM formulas: voltage drops are not compensated and device ratings are not chosen; a short simulation completing does not mean the targets are met.

6、The program also tunes module instances and closed-loop controllers from an existing runnable configuration, using the same AI settings. Only allowed instance values change; shared defaults and other instances remain intact. Controller tuning preserves connections, formulas and measurement bindings. A changed external controller becomes an inline copy in this circuit; the original file is untouched and later edits to it no longer update the copy.

7、Try `examples/modules/boost_parallel_authored.json` with “Change only module M's L1 to 600u; keep all other parameters.” For `examples/closed_loop/buck_vmode.json`, try “Change only the Type III fi to 14000 Hz.” Review the proposal and short simulation, then apply and save. Existing loop-model analysis is shown for controllers; verify that its plant model matches the power stage. Short checks do not prove steady state or discrete-time stability.

### 17.6.1 Parameter range search

![Parameter ranges and Pareto comparison](images/manual/en/parameter-search.png)

1、Choose **Experimental → Parameter range search…**, or expand the same section while editing SPICE parameters, and load the adjustable parameters. Enter minimum, maximum, point count and linear or logarithmic spacing. Ctrl-select multiple parameters in one row to change synchronized switch frequencies together. Separate rows form all combinations, up to 32 cases. Modules only change editable instance values.

2、Choose an output voltage channel and a nonzero DC target. Set the output error limit and optional ripple and startup overshoot limits. Each case runs the full transient and checks repeatability over the final eight complete cycles. Ripple uses the final complete cycle; overshoot covers the entire run. Percentages use the absolute target voltage.

3、Without loss data, compare ripple and overshoot. With validated powerdesignkit device, magnetic and auxiliary loss data, compare estimated efficiency and magnetic envelope volume. Blue points are Pareto candidates; the table retains failures and constraint violations. Volume covers only the magnetic components. Efficiency is a separate datasheet-model estimate, not a hardware measurement.

4、Select a point, then **Apply selected parameters to dialog**. Review before saving or exporting; applying alone does not write the drawing. Changes to the drawing, dialog, search settings or data files require a new search. Closing the dialog requests cancellation. Start with `examples/parameter_search/buck.json`; all device, magnetic and loss data in this example are synthetic demonstrations. The optional powerdesignkit installation must include magnetic evaluation and device snapshots. The [format and CLI reference](PARAMETER_SEARCH.md) describes data requirements and limits.

### 17.7 Device models for the whole drawing

**仿真 → 全图器件模型…** (Simulation > Device models for the whole drawing) chooses simulation models for all parts at once; the drawing does not change:

| Category | Models | Parameters |
| --- | --- | --- |
| Switches | ideal switch, MOSFET (Level 1), IGBT (piecewise linear) | MOSFET needs vto, kp, gate_voltage; IGBT needs vce0 |
| Resistors | ideal, with parasitics | series inductance, parallel capacitance |
| Inductors | ideal, with parasitics | series resistance, parallel capacitance |
| Capacitors | ideal, with parasitics | series resistance, series inductance |

Fill in the parameters and click **应用到全图** (Apply to the whole drawing); **沿用各器件设置** (Keep per-part settings) restores the previous behaviour. If an inductor already has its own non-zero series resistance, choosing the parasitic inductor model reports a conflict; choose one of the two explicitly.

### 17.8 Limits

All models are simplified: no reverse recovery, magnetic saturation, temperature effects, tail current or vendor subcircuits. The `.cir` file uses ngspice control syntax and is not guaranteed to work with other SPICE programs. Standalone SPICE waveforms do not include datasheet loss models and cannot establish hardware loss, efficiency or ZVS. Parameter search estimates require separate, explicit loss and magnetic data.

### 17.9 Conducted EMI estimate

![Conducted EMI settings](images/manual/en/emi-settings.png)

Start from the `buck_emi` template or [the Buck LISN example](../examples/emi/buck_lisn.json). The drawing must explicitly include both supply-line LISNs, their 50 Ω receiver ports and the chassis return. In **Simulation → SPICE simulation…**, set the supply and sampling parameters; then open **Experimental → Conducted EMI estimate…**.

Select the positive and negative LISN voltage channels and an analysis window. **Run current parameters** runs ngspice and displays each line and its common-mode / differential-mode spectrum. The parasitic-capacitance section supports a geometry estimate or measured / Q3D CSV input. **Estimate filter and rerun comparison** changes the selected filter components in a candidate and reruns ngspice; review the comparison before explicitly applying it. Applying can be undone.

These are engineering estimates using a Hann window and sinusoidal RMS amplitudes, not quasi-peak or average receiver readings. CISPR 32 curves are references for the specified AC mains port and do not establish DC-port compliance. Results depend on switching edges, parasitics, wiring, chassis paths, window drift and sampling step. Compare smaller steps and measurements; the supplied example's candidate remains above the reference curve.

![EMI spectra before and after filtering](images/manual/en/emi-result.png)

Command line: `circuit-drawer emi examples/emi/buck_lisn.json -p double --compare -o out/emi`. It writes spectra, numerical results and simulation data. After applying parameters in the editor, save the drawing.

### 17.10 PMSM and FOC

![PMSM component and properties](images/manual/en/pmsm-editor.png)

1、Add a permanent-magnet synchronous motor from **Motors** and connect a, b and c to the three phase outputs. Press `R` to rotate; drag its label independently. The internal star point is not exposed.

2、In the SPICE window, enter per-phase stator resistance, d/q inductances, permanent-magnet flux linkage, pole pairs and total inertia. Friction, constant load torque and initial conditions are optional. Pole pairs are half the pole count; speed is mechanical rad/s and angle is electrical radians.

3、Open **Measurement channels**, select the motor and quantity, then choose **Add motor measurement**. Available quantities include phase and dq currents, mechanical speed, electrical angle and torque. Bind FOC inputs to these channels: the speed loop uses mechanical speed and Park transforms use electrical angle.

![PMSM measurement channels](images/manual/en/pmsm-measurements.png)

4、See [the three-phase motor example](../examples/spice/pmsm.json) and [closed-loop FOC](../examples/closed_loop/pmsm_foc.json). If you change the FOC example's rated DC bus voltage, update the controller's fixed `vdc` input too.

5、This is a constant-parameter sinusoidal PMSM model without saturation, iron loss or heating. Its Simulink model requires Simscape Electrical; its PLECS model targets Standalone.

---

### 17.11 Buck output impedance

![Buck Bode / Nyquist](images/manual/en/impedance.png)

1、Load `buck_impedance` or open `examples/impedance/buck_output.json`. The example uses a 12 V input, a 3.3 V output, a synchronous averaged bridge and an embedded PI controller. Select `v_out` under **Experimental → Output impedance…**.

2、Defaults are 10 Hz–10 kHz, a 10 mA peak current and 200 ms settling time. **Sweep and compare time-domain injection** measures sinusoidal-current responses at up to three frequencies. Increase settling time or check the controller parameters if the operating point drifts. **Consistent** means complex-impedance error ≤2% and adjacent whole-cycle window drift ≤1%. Repeat with a smaller injection amplitude to check small-signal behaviour.

3、The definition is `Zout = −ΔV/ΔI`, with current flowing from the positive to the negative measurement terminal. The drawn load participates in the calculation. Bode magnitude uses dBΩ, and Nyquist shows the positive-frequency complex impedance locus. Source/load stability assessment additionally requires the appropriate impedance ratio and open-loop pole information.

4、Use DC RLC circuits, explicit `average` bridge pairs and frequencies at or below one tenth of the lowest switching frequency. Sources use fixed `dc` values. Controller references use the final value of startup ramps. Each run saves settings, CSV, SVG, JSON, the source snapshot and original simulation files in a separate directory. Download the plot and data from the dialog.

```sh
circuit-drawer impedance examples/impedance/buck_output.json --probe v_out --compare -o out/impedance
```

### 17.12 Grid-connected inverter dq impedance matrix

![dq impedance matrix](images/manual/en/dq-impedance.png)

1、Load `grid_dq_impedance` or open `examples/impedance/grid_dq.json`, then open **Experimental → dq impedance matrix…**. Select `Va`, `Vb`, `Vc` in A, B, C order. The example contains a 300 V DC link, a 50 Hz three-phase grid, a PLL and dq current PI controllers with references id=10 A and iq=0 A.

2、Each frequency runs a baseline and independent d-axis and q-axis voltage perturbations. Baseline-subtracted phasors determine the full 2×2 admittance matrix, whose inverse gives impedance. Frequencies are perturbation frequencies in the dq frame. The measurement frame rotates at the fixed grid frequency, with its d axis defined by the phase-A source reference. The original controller's PLL dynamics participate in the calculation. Current flows from the converter to the grid, with `ΔVdq = −Zdq·ΔIdq`.

3、Defaults are 10 Hz–1 kHz with 3 points per decade, giving 7 frequencies, a 2 V peak injection, 300 ms settling time and a maximum step of 20 μs. Gear second-order integration uses a step additionally bounded by the grid and perturbation frequencies. More frequencies require more simulations. Reopen the dialog to view a completed result after closing it.

4、**Sweep and compare half amplitude and step** repeats the first, middle and last frequencies. **Consistent** requires every matrix entry to differ by at most 2% and adjacent whole-cycle windows to drift by at most 1%. Near-zero entries use 0.1% of the matrix norm as the denominator floor. Reduce amplitude and step further to check convergence. Increase settling time or check the PLL and current loops if the operating point fluctuates.

5、Use balanced ABC positive-sequence sine sources with equal frequency and amplitude, a shared negative neutral, and zero `offset` and `delay`. The circuit uses RLC filters and `average` bridge legs. Other independent sources use fixed `dc` values. The maximum analysis frequency is one tenth of the lowest switching frequency. Peak injection voltage is limited to 5% of the grid phase-voltage peak.

6、**Matrix entry** selects `Zdd`, `Zdq`, `Zqd` or `Zqq`, with voltage axes in rows and current axes in columns. CSV contains real and imaginary parts of all four impedance and admittance entries. Four SVG files show Bode plots and positive-frequency Nyquist loci. JSON records settings, operating points, window drift and comparisons. Each result directory also contains the drawing and controller snapshot, netlists and original waveforms. Stability assessment additionally requires the interconnected system's impedance-ratio matrix and open-loop pole information.

```sh
circuit-drawer dq-impedance examples/impedance/grid_dq.json --sources Va Vb Vc --compare -o out/dq-impedance
```

### 17.13 DC source/load stability

![Source/load stability](images/manual/en/impedance-ratio.png)

1、Open **Experimental → Source/load stability…**, select a 48 V bus example, click **Load example**, then **Calculate impedance ratio and poles**. Use **Open model JSON** to load a separate port-model file. Edit its parameters and calculate again. This file supplies source impedance `Zs` and load admittance `Yl`. The canvas remains available for drawing.

2、Current enters the load. Define `Δv = −Zs·Δi` at the source and `Δi = Yl·Δv` at the load. Both models must use the same operating point and port boundary. Assign each capacitor and control loop to its corresponding subsystem once. The impedance ratio is `Tm = Zs·Yl = Zs/Zl`, with characteristic polynomial `Ds·Dl + Ns·Nl`. Source impedance uses Ω, load admittance uses S, and the ratio is dimensionless.

| Field | Input |
| --- | --- |
| `params` | Optional numbers or formulas, referencing previously defined parameters in insertion order |
| `source_impedance` | Source `num` and `den` coefficient arrays, in descending powers of s |
| `load_admittance` | Load `num` and `den` coefficient arrays, in descending powers of s |
| `freq` | Positive increasing display limits in Hz, default `[1, 10000]`, at most six decades |
| `points` | Points per decade, default 80, integer 5–200 |

3、Each port accepts a real-coefficient, continuous-time proper rational model of up to order eight. Numerator order is at most denominator order. Coefficients can use formulas such as `"L*C"` and `"-P/V^2"`. Supplied common polynomial factors are retained, including their internal modes in the pole count. Stability conclusions cover dynamics expressed in these models. Pure delays, discrete control and dq matrix interconnections require their corresponding model methods.

4、The dialog shows open-loop right-half-plane poles `P`, clockwise encirclements `N` and closed-loop right-half-plane poles `Z`, checking `Z=P+N`. A complete right-shifted contour is bounded using the model roots and progressively subdivided. Its winding is independent of the display band. Solid and dashed curves show positive and negative frequencies in that band, with a red cross at −1. **Sampled minimum |1+Tm| in the band** covers the displayed sample points only. Inspect the pole table for marginal or numerical-boundary results. Repeated roots with insufficient pole precision, failed numerical solutions and inconsistent winding checks return an error.

5、The example uses `Zs=(R+sL)/(LCs²+RCs+1)` and `Yl=−P/V²`, with 48 V, 0.2 Ω, 2 mH and 470 μF. V is the bus operating voltage, with the ideal source set to `E=V+RP/V`. Closed-loop poles are approximately `−22.2961 ± j1028.4901 rad/s` at 60 W and `+42.3463 ± j1021.5514 rad/s` at 200 W. Both editable files are available as the [60 W example](../examples/impedance/source_load_stable.json) and [200 W example](../examples/impedance/source_load_unstable.json).

6、Download `model.json` for the input, `result.json` for evaluated coefficients, poles, winding and frequency responses, CSV for complex data and SVG for plots. Each CLI run writes a separate directory. Existing Buck and dq sweeps can help check port models. A complete stability assessment also depends on the modeled internal dynamics and open-loop pole information.

```sh
circuit-drawer impedance-ratio examples/impedance/source_load_stable.json -o out/impedance-ratio
```

### 17.14 dq interconnection stability

![dq interconnection stability](images/manual/en/dq-stability.png)

1、Open **Experimental → dq interconnection stability…**, load the 1500 W or 6000 W example, then click **Calculate impedance ratio and poles**. **Open model JSON** accepts separate source impedance `Zs` and load admittance `Yl` models at one operating point in one fixed dq frame. Voltage and current components follow d, q order. Matrix rows specify output axes and columns specify input axes.

2、Each port uses continuous-time state equations `dx/dt=A·x+B·u`, `y=C·x+D·u`, giving `C·(sI−A)⁻¹·B+D`. At the source, `u=Δi` and `y=−Δv`. At the load, `u=Δv` and `y=Δi`. Positive current enters the load. Both models must share axis directions, angular reference, nominal frequency, units and power normalization. Assign each controller and line to its corresponding port once. Include PLL, control-loop and frequency-variation effects in the supplied state models.

| Field | Input |
| --- | --- |
| `params` | Optional numbers or formulas, referencing previously defined parameters in insertion order |
| `source_impedance`, `load_admittance` | Each contains real matrices `A`, `B`, `C`, `D` for its port |
| `A`, `B`, `C`, `D` | Each port has n=0–8 states, with dimensions n×n, n×2, 2×n, 2×2 respectively. Entries can reference params |
| Static port | `A=[]`, `B=[]`, `C=[[],[]]`, with its response supplied by the 2×2 D matrix |
| `freq`, `points` | Positive increasing display limits in Hz, default `[1,10000]`, at most six decades. Points per decade default to 80, integer 5–200 |

3、The complete impedance-ratio matrix is `T=Zs·Yl`. Generalized Nyquist counts clockwise encirclements N of the origin by `det(I+T)`, retains every internal state of both ports, and checks `Z=P+N` using open-loop RHP pole count P and closed-loop RHP pole count Z. Winding uses a complete right-shifted contour bounded by the model poles. Result JSON contains the closed-loop state matrix and both pole lists, including internal modes hidden in the port response.

4、**Plot** selects `det(I+T)`, `Tdd`, `Tdq`, `Tqd` or `Tqq`. The determinant plot marks the origin with a red cross. Solid and dashed curves show positive and negative frequencies in the display band. Each T entry shows its positive-frequency response. **Sampled minimum |det(I+T)| in the band** describes the sampled points only. Closed-loop poles on or near the imaginary axis give a marginal result. Nearly singular `I+Ds·Dl`, insufficient pole precision, or inconsistent winding and internal pole counts produce a calculation error.

5、The example combines a synchronous-frame RLC source and an ideal constant-power load at 50 Hz, with `R=0.2 Ω`, `L=2 mH`, `Cbus=470 μF`. It uses an amplitude-invariant dq transform and operating point `vd=100 V`, `vq=0`. The 100 V value is phase-voltage peak, P is total three-phase active power, and Q=0. Load admittance is `diag(−G,+G)`, where `G=2P/(3V²)`. The 1500 W model is asymptotically stable. The 6000 W model has two RHP poles. Editable models are available as the [1500 W example](../examples/impedance/dq_source_load_stable.json) and [6000 W example](../examples/impedance/dq_source_load_unstable.json).

6、Download `model.json` for input, `result.json` for evaluated matrices, internal poles, contour and complex responses, CSV for all four entries of Zs, Yl, T and the determinant, and five SVG files for the determinant and T entries. Existing dq sweep data can help compare model frequency responses. This analysis requires a complete state-space model supplied separately.

```sh
circuit-drawer dq-stability examples/impedance/dq_source_load_stable.json -o out/dq-stability
```

### 17.15 Droop, virtual synchronous machine and parallel operation

![Parallel-source power and frequency response](images/manual/en/grid-forming.png)

1. On an empty canvas, click **Open example** and choose the droop, VSM or parallel case. You can also open `examples/grid_forming/gfm_droop.json`, `gfm_vsm.json` or `gfm_parallel.json`. Run through **Simulation → SPICE simulation**. An additional balanced resistor bank connects at 0.5 s and disconnects at 1.1 s.

2. Run the companion commands below, import the generated CSV in the waveform editor, and select `u1_P_W`, `u2_P_W`, `u1_f_Hz` and `u2_f_Hz` in the channel settings. P/Q are converter-terminal three-phase powers, f is controller frequency, and E is phase peak voltage. The script also saves an editable `control.json`. In the loaded display window, power is about 921 W and 1846 W, with frequency near 49.707 Hz.

```sh
python examples/grid_forming/run.py --case parallel --out out/grid-forming
circuit-drawer-control edit out/grid-forming/parallel/control.json
```

3. The cases use continuous primary frequency control, Q/V droop, ideal inner voltage tracking and average bridge legs. Proportional output impedances and droop settings give approximately 1:2 sharing with a load-dependent frequency offset. Droop power filtering has a 0.03 s time constant. VSM M/D is 0.16 s. Current limiting, inner-loop bandwidth, switching ripple and hardware protection require corresponding models.

4. The `gfm_sync` control template provides relative-angle analysis with fixed voltage magnitudes, equal equilibrium angles and a quasi-steady RL network. Load it and open loop analysis for the Bode plot and poles. Default operating-point parameters are E=98 V and Rload=5 Ω. Poles and margins cover this reduced model. Full-system analysis also requires voltage/reactive dynamics, electromagnetic line states and the common-angle mode. [Parameters, equations and three-language case guide](../examples/grid_forming/README.md)

### 17.16 Digital control delay and quantization

![Digital sampling and quantization settings](images/manual/en/digital-control.png)

1. Load `buck_digital` and expand digital settings under closed-loop control in **Simulation → SPICE simulation**. The control editor has the same settings in loop analysis. Click **Apply digital settings** to update the Bode plot and poles. Changes support undo and save/reopen.

2. Enter ADC ranges in physical input units. PWM bits determine duty resolution. Computation delay uses whole samples. Specify word length and fractional bits together, or leave both empty for double-precision states. States start at zero, with the first update at Ts.

3. The example uses 1 MHz sampling and PWM, a 12-bit ADC, 14-bit PWM and 24-bit states. Compare delays of 0, 1 and 3 samples, then 8-bit PWM. The transient export includes per-sample CSV and reports possible quantization limit cycles when the tail repeats.

4. Linear analysis uses a zero-order-held plant and quantized coefficients, with poles tested against the unit circle. Sampled simulation supports scalar controllers. Native Simulink/PLECS quantization models remain a future integration. The GaN application example uses ideal switches with finite on-resistance, with device losses requiring a device-specific model. [Parameters, rounding rules and commands](../examples/digital_control/README.md#five-english-guide)

## 18. Simulink and PLECS export, closed-loop control

The same circuit and simulation parameters can be exported as Simulink and PLECS models. The export carries the real wiring, part values, PWM and probes, and lays the model out like the original drawing.

![Simulink and PLECS export settings](images/manual/en/simulation-export-options.png)

### 18.1 Exporting from the editor

1、Open **仿真 → SPICE 仿真…**, check the parameters, reference ground and probes, and choose the output folder.

2、Expand **外部仿真导出说明与近似设置** (External simulation export notes and approximations): diode linearisation reference current (default 1 A), series resistance of PLECS numerical stabilising capacitors (default 1 mΩ), and closed-loop controller mode (continuous or discrete). These affect only this export and are not stored in the circuit's simulation parameters.

3、Click **导出 Simulink** (Export Simulink) or **导出 PLECS 独立版** (Export PLECS Standalone). The buttons save the window's simulation parameters first; if checking fails, nothing is saved or written.

4、Open and run the model in the target program. "Exported" in the editor only means the files were written; solving is done by the target program, which the editor never starts.

### 18.2 Simulink

```bash
circuit-drawer simulink examples/spice/buck.json -o out/simulink
```

1、The output is `build_cd_buck.m`, the manifest `cd_buck_simulink.manifest.json` and a notes file.

2、In MATLAB, change to the output folder and run:

```matlab
model = build_cd_buck;
result = sim(model);
```

3、The build script connects and compiles the model, then saves `cd_buck.slx`. Probes go to a Scope and to output ports in the original order; results are in `result.yout`, with units and order in the manifest's `columns`. The saved SLX no longer needs Python or the original JSON.

4、The script does not rebuild a model that is already open, and by default does not overwrite an existing SLX. To regenerate, close the model and run `build_cd_buck(true)`.

5、Requires MATLAB, Simulink and Simscape (Foundation Library). A PMSM component or a controller using Clarke/Park transforms also needs Simscape Electrical.

### 18.3 PLECS Standalone

```bash
circuit-drawer plecs examples/spice/buck.json -o out/plecs
```

1、The output is the native model `cd_buck.plecs`, a manifest and a notes file. Open it in PLECS Standalone, run it and look at the Scope; probes are also connected to top-level output ports.

2、The model uses the Radau solver with extended matrix precision and keeps the original maximum step.

3、Model names start with `cd_`; file names with spaces, Chinese characters or excessive length are converted to safe names.

### 18.3a PSIM

1、Choose **Simulation → Export to PSIM…**, check the parameters and click **Export PSIM**. The equivalent command is `circuit-drawer psim examples/spice/buck.json -o out/psim`. This produces a Python builder, a manifest and notes. Running the builder requires PSIM 2026 and its official Python API.

2、Run `python out/psim/build_cd_buck.py --psim-home "PSIM installation folder"` to create `.psimsch`; add `--run` to simulate natively and save measured CSV. Existing models or results block replacement unless you explicitly pass `--overwrite`. Temporary files stay under the output folder.

3、Supports open-loop RLC, diodes, resistive switches, sources and ideal multi-winding transformers. Closed-loop control, collapsed subsystems, IGBT models, thyristors and PMSM are rejected with a reason, preserving the original drawing. Net labels retain the checked connections; CSV uses the requested output interval. See [PSIM export](PSIM_EXPORT.md) for approximations and limits.

### 18.4 Differences from ngspice

1、**Diodes:** both targets use piecewise-linear diodes, taking the tangent at 27 °C and a reference current I0 (default 1 A; `--diode-current 2` on the command line uses 2 A). This is not identical to ngspice's exponential model. The reverse branch of a Zener diode uses the device's own test current Izt.

2、**Numerical stabilising capacitors:** PLECS puts a resistor in series with each one (default 1 mΩ, changed with `--numerical-resistance 2m`) to remove the conflict between ideal voltage sources and capacitor states.

3、**An input capacitor directly across an ideal voltage source:** PLECS cannot treat it as an independent state, and the export reports why. In **仿真 → 全图器件模型…**, set capacitors to "with parasitics" and enter a real ESR, then export again. Note that this applies to every capacitor in the drawing and also changes the SPICE results.

4、A global Level 1 MOSFET model requires explicit consent to a resistive-switch approximation. In the export settings, select **Approximate LEVEL=1 switches using on-resistance**, or pass `--approximate-mosfet` on the command line. The exported model uses each switch's Ron/Roff and logical PWM. Confirm again when reopening the window; the original SPICE model is preserved.

5、Each drive signal may have at most 200,000 time points; shorten the simulation or reduce the frequency if exceeded.

### 18.5 Closed-loop control

![Closed-loop settings](images/manual/en/closed-loop-settings.png)

A control diagram table can be connected to the circuit: ngspice then runs the closed loop directly, and the Simulink and PLECS exports turn the controller into native control blocks.

1、In the SPICE window, expand **四、闭环控制（可选）**, tick **启用闭环控制** (Enable closed-loop control), enter the path of the control table (relative to the circuit file) and click **读取控制表** (Read control table).

2、Choose a source for each controller input: a probe, or a constant (with an optional soft-start time; 0 means a step).

3、Select the switches and modulation for each output. PWM uses duty from 0 to 1; VCO uses Hz and requires minimum and maximum frequency. Edit the complementary group’s dead time in seconds. Each switch can be driven by one output only.

3a、Open [llc_vco.json](../examples/closed_loop/llc_vco.json) with its control table in the same folder. Complementary groups share continuous phase and the same delay; their nominal frequency/duty fields are unused. Run ngspice or export to Simulink/PLECS with a continuous or discrete controller: phase remains continuous and dead time stays in seconds. Native models use ideal edges. Tables with digital delay or quantization still require ngspice; disable PWM quantization for VCO. The example draws capacitor series resistor Rc explicitly and specifies numerical node capacitance; compare waveforms again after changing these values or the step size.

![LLC frequency settings](images/manual/en/vco-settings.png)

3b、To show the same controller in the circuit drawing, draw a frame using **Insert → Annotations → Draw block**, select it, and choose **Reference closed-loop controller** in Properties. **Controller display** switches between a label and the expanded diagram. Drag the corners to make room before expanding; the diagram keeps the printed font size. Moving, folding and unlinking the frame preserve the controller and its simulation bindings.

3c、Open [buck_control_view.json](../examples/closed_loop/buck_control_view.json) with `buck_vmode.control.json` beside it. Both simulation and drawing read that file. After editing the control table, check again or reload the page; keep both files together when copying the circuit. Expanded previews use the local LaTeX renderer. A missing file or a diagram that does not fit blocks figure export and preserves existing output files.

![Referenced controller properties](images/manual/en/control-reference.png)

4、The corresponding JSON:

```json
"closed_loop": {
  "control": "buck_vmode.control.json",
  "inputs":  {"ref":   {"value": 1.65, "ramp": 5e-4},
              "plant": {"probe": "v_out"}},
  "outputs": {"pwm":   {"switches": ["S1"]}}
}
```

5、A bridge leg is written as `{"switches": ["S1"], "complement": ["S2"], "dead_time": "1u", "model": "average"}`: `complement` is the lower switch, `dead_time` the dead time, and `model` is `average` (switching-period average, the default) or `switching` (individual switching). Probe inputs may add `filter` (first-order filter time constant in seconds). Outputs of function blocks are written `block.output`, for example `sv.da`.

6. ngspice defaults to a continuous controller, with anti-windup for transfer functions having an integrator and output limits. Digital settings enable sampled updates, whole-sample delay and quantization as described in section 17.16. A single switch uses trailing-edge PWM.

7、Exports offer two controller modes: `continuous` (default, comparable with ngspice) and `discrete` (using the control table's `code.fs` and discretisation method, matching the generated C code). Choose it in the export settings or with `--control discrete`. `average` bridge legs are exported to Simulink / PLECS as individually switched legs.

```bash
circuit-drawer spice examples/closed_loop/buck_vmode.json -o out/closed-loop --run
circuit-drawer plecs examples/closed_loop/buck_vmode.json -o out/plecs/buck_closed
circuit-drawer simulink examples/closed_loop/grid_inverter_dq.json -o out/simulink/grid --control discrete
```

8、Examples: `examples/closed_loop/buck_vmode.json` (voltage-mode Buck with Type III compensation) and `examples/closed_loop/grid_inverter_dq.json` (three-phase grid-connected inverter with PLL and dq current control).

### 18.6 Folded modules as subsystems

Module instances shown folded are exported to Simulink / PLECS as one subsystem block, positioned and sized like the folded box; expanded and partly folded instances are flattened as before. The electrical terminals follow the ports on the folded box (left side first, then right, each from top to bottom), and the subsystem contains the expanded circuit. If the two ends of a voltage probe lie inside two different folded instances, the export refuses and names the probe; show one of the instances expanded instead.

```bash
circuit-drawer simulink examples/modules/llc_single.json -o out/simulink/llc_single
circuit-drawer plecs examples/modules/dab_ipos.json -o out/plecs/dab_ipos
```

---

## 19. Control diagram editor

![Control diagram editor](images/manual/en/control-editor.png)

### 19.1 Starting and examples

```bash
circuit-drawer-control list                          # built-in examples and saved templates
circuit-drawer-control edit my.json -t dual_loop     # create my.json from an example if missing
```

| Example | Content |
| --- | --- |
| `single_loop` | Single voltage loop: reference, summing point, PI, limiter, PWM, feedback through an LPF |
| `dual_loop` | Outer voltage loop, inner current loop |
| `buck_charger` | Battery charger Buck controller: CV/CC-CP, multiply and divide points, A/B switch |
| `foc_pmsm` | Field-oriented control of a PMSM |
| `buck_vmode` | Voltage-mode Buck loop with complete loop parameters, ready for loop analysis |
| `mpc_fcs` | Finite-control-set model predictive current control |

The editor uses port 8766 and draws at IEEE double-column width by default.

### 19.2 Interface

1、Menu bar: 文件 (File: Save, Export, New from selected template, Save whole diagram as template, Save selected blocks as snippet), 编辑 (Edit: Undo, Redo, Cut, Copy, Paste, Delete selection, Add L-shaped corner to link), 视图 (View: Fit, Snap step, LaTeX preview), 插入 (Insert: block, limiter, reference tag, measurement tag, summing point, multiply point, divide point, A/B switch, text, background panel, blue dashed frame, link, insert selected snippet), AI (AI interface settings, Describe a new diagram, AI edit of the current diagram), 实验功能 (Experimental: Loop analysis), 帮助 (Help).

2、Toolbar: 保存 (Save), 撤销 (Undo), 重做 (Redo), ＋ 添加… (Add), 连线 (Link), 删除 (Delete), 适应窗口 (Fit), LaTeX 预览 (LaTeX preview), AI 修改… (AI edit), 导出… (Export).

3、Right panel: check result, properties of the selection (appearance, text and formula, JSON), templates and snippets, and the JSON of the whole table.

### 19.3 Common operations

| Operation | How |
| --- | --- |
| Move blocks and text | Drag; snaps to the snap step (0.05, 0.1, 0.25); arrow keys nudge, Shift plus arrow keys move 5 times as far |
| Multiple selection | Shift-click, or drag a selection box on an empty spot; drag to move the group |
| Links | Click **连线** (Link, or press `L`), click the start port, then the end port; ending on an empty spot gives an open end; starting on a line branches from it (a dot is added) |
| Corners | Select a link to drag its corners; double-click a link to add a corner; select a corner and press `Delete` to remove it; use **给连线加 L 形拐点** for slanted links |
| Copy, cut, paste | `Ctrl+C`, `Ctrl+X`, `Ctrl+V`, including links between the selected blocks, regions enclosing them, transfer functions and code settings; duplicate IDs get a suffix |
| Text and formulas | Type LaTeX directly in the **文字与公式** (Text and formula) box; symbol buttons insert Greek letters, sub- and superscripts, fractions and more, and a LaTeX preview of the snippet appears after a pause |
| Appearance | **外观** (Appearance) sets fill, border and text colours separately; links can be coloured and drawn solid, dashed or dotted |
| Exact editing | The selection's JSON appears on the right; edit it and click **应用修改** (Apply changes) |
| Undo and save | `Ctrl+Z` undo, `Ctrl+Shift+Z` or `Ctrl+Y` redo, `Ctrl+S` save |

### 19.4 Templates and snippets

1、**Templates** store a whole diagram and must pass the checker: **整张图存为模板…** (Save whole diagram as template) or `circuit-drawer-control save-template my.json lab_dual --title "Lab dual loop"`. Afterwards `list`, `export`, `edit -t` and **从模板新建** (New from template) can use it.

2、**Snippets** store part of a diagram, such as one PI loop: select some blocks and click **选中的方块存为片段…** (Save selected blocks as snippet); **插入所选片段** (Insert selected snippet) places it below the current diagram, selected, so you can drag it into place. Built-in snippets are `foc_speed_pi`, `foc_dq_current_pi`, `foc_ipark_svpwm`, `foc_clarke_park` and `mpc_fcs_core`.

3、Templates and snippets are stored in `~/.circuit_drawer/templates/control/` (snippets in its `snippets/` subfolder), or under `CIRCUIT_DRAWER_TEMPLATES` if set.

### 19.5 Loop analysis and compensator design

![Experimental menu in the control editor](images/manual/en/experimental-control.png)

![Loop analysis](images/manual/en/control-loop-analysis.png)

1、Give the blocks transfer functions (the table's `loop` section) and state how the power stage outside the diagram connects the output back to the measurement tags; the open-loop gain, margins and closed-loop response are then computed from the drawing. The structure comes from the drawn links.

2、**实验功能 → 环路分析** (Experimental > Loop analysis) draws the Bode plot and lists the crossover frequency, phase margin, gain margin and closed-loop poles; tick **黑白** (black and white) if needed, and **导出 Bode 图（单栏 PDF+PNG）** exports the plot.

3、**补偿器设计（k 因子法）** (Compensator design, k-factor method): choose the compensator block and type (automatic, I, II, III), enter the crossover frequency and phase margin, click **计算** (Calculate), and when satisfied **写入表** (Write to table), which can be undone.

4、Transfer function forms: `gain`, `num/den`, `pi`, `pid`, `pr`, `lpf`, `comp` (Type I/II/III), power-stage average models `model: buck/boost/buckboost`, each optionally with a `delay`.

5、Command line:

```bash
circuit-drawer-control loop buck_vmode                       # margins and closed-loop poles (JSON)
circuit-drawer-control bode buck_vmode -o out                # Bode plot
circuit-drawer-control design buck_vmode -b gc --fc 30k --pm 60 -w new.json
```

![Bode plot](images/manual/example-bode.png)

### 19.6 C code generation

1、The table's `code` section sets the sampling frequency `fs`, the discretisation method (`tustin`, `zoh`, `backward`, `forward`), the arithmetic (`fixed` int32 fixed point, `float`, `double`), the fixed-point fraction bits `q`, external blocks and output limits.

2、**生成 C 代码** (Generate C code) in the loop analysis window writes to `<table name>_c/` under the export folder; or use the command line:

```bash
circuit-drawer-control code buck_vmode -o c_out                # <name>.c/.h, cg_lib.h, report
circuit-drawer-control code buck_vmode -o c_out --arith float
circuit-drawer-control code buck_vmode --fs 200k --q 20 --harness   # also a PC test program
```

3、Call the generated `<name>_step()` once per control period. `cg_lib.h` is a header-only runtime with PI, second-order sections, Clarke, Park, inverse Park and SVPWM. The report `<name>_report.json` lists each block's discretisation and the effect of coefficient rounding.

### 19.7 Function blocks

Clarke, inverse Clarke, Park, inverse Park, SVPWM and angle wrapping are declared in the table's `functions` section and used in code generation and closed-loop simulation (floating-point arithmetic required). Their outputs are written `block.output`, for example `sv.da`. Loop analysis is linear and stops at function blocks, saying why. When the controller contains an internal loop (such as a PLL), set `code.blocks.<integrator>.delay` to 1 to break the algebraic loop.

### 19.7a C code blocks

![C code block settings](images/manual/en/custom-c.png)

1、Select an ordinary control block and click **Set C code…**. Map input/output variable names to existing ports using JSON (for example `{"u":"w"}` and `{"y":"e"}`), then enter initial states and C statements. Inputs and old states are read-only. Assign outputs and use `next_stateName` to update states. Apply creates one undo step; Save writes the file. Converting a block or renaming an output on the same port updates the controller's code output selection. Reselect the corresponding output in the circuit's closed-loop settings.

2、The whole controller uses the fixed sample rate `code.fs` and double arithmetic. Applying replaces this block's transfer function and block limits; implement limits in the code. Use pure calculations and explicit state, without static state, external side effects or early return. The editor checks fields and wiring; the target compiler checks C syntax. Loading, editing and drawing do not execute C.

3、Reference the same control table from the circuit and select **Discrete** when exporting to PLECS or Simulink. PLECS uses C-Script; Simulink uses C Function with Unit Delay state storage. Generated C source is also available; call step once per control period. Custom C is not supported by ngspice, Python waveform evaluation, Bode analysis or digital delay/quantization.

4、Open [buck_c_mpc.json](../examples/closed_loop/buck_c_mpc.json) with its [control table](../examples/closed_loop/buck_c_mpc.control.json) in the same folder. It samples at 100 kHz and compares duty candidates over an eight-sample horizon. Output is about 5.08–5.09 V during a 12→9 V supply change; the simplified model has a steady-state error. Check the explicit algorithm constants when changing L, C, load or sample rate. This is a demonstration, not a tuned hardware controller.

### 19.8 Figures

```bash
circuit-drawer-control check my.json
circuit-drawer-control render my.json -o out          # TeX, PDF, SVG, PNG
circuit-drawer-control render my.json --mono          # black and white
circuit-drawer-control render my.json --grid -f png   # coordinate grid for adjusting coordinates
```

![FOC control diagram](images/manual/example-control-foc.png)

---

<a id="ai"></a>

## 20. AI drawing assistant

The AI drawing assistant is optional. Ordinary drawing, modes and simulation do not need it. It helps in two situations: a topology that has no template, or a one-sentence change to an existing figure.

### 20.1 How it works

1、The model writes a complete coordinate table from your description. Coordinates are still written explicitly; the program never calls an automatic layout.

2、The checker checks the table as usual. If it fails, the report (part IDs, coordinates, reasons) goes back to the model for a rewrite, until the table passes or the rounds are used up (4 by default).

3、A passing table is saved as a new file. If the rounds run out, the version with the fewest problems is saved, and you fix the red marks in the editor.

4、The checker can only ensure that the drawing is tidy and agrees with the netlist the model wrote; it cannot tell whether the topology is the right one, so check the structure first for complex circuits.

### 20.2 You need your own API key

1、The assistant calls the model interface you configure and needs your own API key; usage is billed by your provider. The program does not include any key.

2、Requests send your description and reference images, and for edits the current coordinate table and check report, to the base URL you configured. When you do not use the AI features, nothing is sent to any model interface.

3、Two interface types are supported:

| Interface type | Setting | Notes |
| --- | --- | --- |
| Anthropic-compatible interface | `anthropic` | Needs the `anthropic` Python package; default model `claude-opus-5-5`, default base URL `https://api.anthropic.com` |
| OpenAI-compatible interface | `openai` | No extra package; a model name is required; default base URL `https://api.openai.com/v1`, which can be changed to another compatible service |

### 20.3 Configuring the interface

![AI interface settings](images/manual/en/ai-settings.png)

1、**Web settings (recommended):** in the circuit or control diagram editor, choose **AI → AI 接口设置…** (AI > AI interface settings), select the interface type, enter the model ID, base URL and API key, and click **应用设置** (Apply settings).

   1、The API key field has three actions: **保留已配置密钥** (Keep the configured key), **替换密钥** (Replace key) and **清除密钥** (Clear key). The page only shows whether a key is configured and never displays it. Choose "keep" when you change only the model or reasoning effort; a new interface type or base URL requires entering the key again.

   2、With **在当前 Windows 账户中加密记住 API Key** ticked, the key is stored encrypted and need not be entered again; unticked, it is used only for the current server session. This option is not available on other operating systems.

   3、The circuit editor and control diagram editor opened by the same launcher share the settings; they apply to subsequent requests, and requests already running are not affected.

2、**Windows launcher:** enter the same settings under **API 与仿真设置…** (API and simulation settings); see [chapter 2](#2-installation-and-startup).

3、**Environment variables** (PowerShell: `$env:X = "..."`, bash: `export X=...`); restart the editor after setting them:

```powershell
# Anthropic-compatible interface
$env:ANTHROPIC_API_KEY = "<your key>"
$env:ANTHROPIC_BASE_URL = "https://..."          # only for a proxy or gateway

# OpenAI-compatible interface
$env:OPENAI_API_KEY = "<your key>"
$env:OPENAI_BASE_URL = "https://..."             # for another compatible service
$env:CIRCUIT_DRAWER_PROVIDER = "openai"
$env:CIRCUIT_DRAWER_MODEL = "<model name>"
```

If only `OPENAI_API_KEY` is set and there is no Anthropic key, the OpenAI-compatible interface is used automatically.

4、**Reasoning effort:** the structure step and the generate / edit step can each be set to provider default, `low`, `medium`, `high`, `xhigh` or `max`. "Provider default" sends no extra parameter. When nothing is set, the structure step requests `low`, the generate step requests `high` with an Anthropic-compatible interface, and nothing extra with an OpenAI-compatible interface. Which levels are supported depends on the model and provider; if the interface rejects a level, an error is shown, so choose another level or provider default. Higher effort may take longer and use more tokens.

### 20.4 Describing a new topology

![Describe a new topology](images/manual/en/ai-describe.png)

1、Choose **AI → 描述新拓扑…** (AI > Describe a new topology). The top of the dialog shows the interface and model that will be used; a missing key is reported in red straight away. **测试连接** (Test connection) tells you within 10 seconds whether the base URL can be reached.

2、Write a description. A good one covers, in order: the topology and variant (for example diode-clamped or flying-capacitor), device choices (MOSFET or IGBT, whether to draw Lm), inputs and outputs (DC source, AC source, capacitor with resistive load, battery), the annotations you want (current arrows, voltage polarities, part names, colours), and modules and layout (how many modules, how they connect, the colour of each module's region).

3、**Template hint:** when the description contains the name or an alias of an existing template, the dialog offers **已有手画模板……直接载入模板** (a hand-drawn template exists; load it directly), which is faster and more accurate than calling the model. The hint matches by name, so a description of a "four-switch Buck-Boost" also suggests the Boost template; if it is not the topology you want, ignore the hint and generate anyway.

4、**先看结构** (Show the structure first, strongly recommended): the model first lists the parts and connections as it understands them, without drawing. Correct the list in the text box, then click **按这个结构生成** (Generate from this structure). This matters most when you give only a topology name.

5、**直接生成** (Generate now): progress shows the round number, elapsed time, each round's problems and the model's notes, and can be cancelled at any time; the terminal that started the editor prints the same progress.

6、Paste reference images with **Ctrl+V** (**⌘V** on macOS), drop PNG, JPG or WebP files into the dialog, or click **Choose images…**. Each AI dialog holds up to 3 images. Use **Remove image** below a thumbnail to delete it. Large images are resized proportionally to a maximum edge of 2048 pixels. Images that remain too large or cannot be read show an error. Plain text can be pasted as usual. The model must support image input.

6a、Closing, switching and reopening dialogs within the same page preserves the selected images. Adding or removing an image invalidates the previous structure. Request a new structure or generate directly.

7、**Rounds:** "最多 N 轮" (at most N rounds) accepts 1–8. For complex topologies (a dozen switches or more), name the exact variant, check the structure first, switch to double column, and allow 6–8 rounds. If the description mentions three-phase or multilevel circuits, the dialog suggests switching to double column first.

8、The result is saved as a new file; the current file is not overwritten. If it fails at single column but passes at double column, a **切到双栏** (Switch to double column) button appears.

### 20.5 AI edit of the current figure

![AI edit of the current figure](images/manual/en/ai-tweak.png)

1、Choose **AI → AI 修改当前图…** (or **AI 修改** on the toolbar) and say in one sentence what to change, for example "add an input capacitor Cin", "put the v_sw label above the switching node", "add a green region around the primary full bridge", "move RL right by 0.5".

2、The model receives the current table and changes only what you mention; all other parts and wires are kept. Simulation parameters, waveform settings and other extra data are always taken from the table before the edit.

3、The result first appears as a candidate with a list of actual changes; it does not replace the current drawing automatically. Download the candidate, or review it and choose **Confirm, apply and save as…**. Manual edits made while waiting are preserved; applying after the drawing has changed requires confirmation. Adoption can be undone and the original file is retained.

4、With node and branch labels enabled, you can refer to net names and branch numbers, for example "add a 10 Ω resistor between A and B, left of C1". If the project has its own modules, the model receives the module library, so you can say "reuse the project's Boost module, two instances with inputs and outputs in parallel".

5、The circuit **AI edit of the current figure** dialog provides the same reference-image area. Attach a screenshot of the desired layout or labels together with your editing instructions.

### 20.5a Review the rendered drawing

![Review the rendered drawing](images/manual/en/ai-visual-review.png)

1、Both circuit AI dialogs offer **Review rendered drawing**, off by default. After the drawing passes validation at the chosen column width, the program sends its rendered image and coordinate table to the configured model to look for detouring wires, uneven repeated structures and crowded labels. Include your priorities and any positions that must stay fixed in the original request.

2、Specific findings allow at most one further edit. It is kept only if validation passes and the complete pin connections, devices and simulation parameters remain unchanged, including hidden modules. Otherwise the original candidate and findings are retained. Review the final candidate before adopting it.

3、Review adds rendering and model-request time. Missing LaTeX, unsupported image input or request failure keeps the original drawing and reports why. Switch to double columns first if the single-column check fails. This checks layout, does not run a simulation and may miss drawing problems. Add `--review` to CLI `new` or `tweak` to enable it.

### 20.6 AI in the control diagram editor

**AI → 描述新框图…** (Describe a new diagram) and **AI → AI 修改当前图…** in the control diagram editor use the same interface settings. The model returns only the structure (blocks, links, transfer functions); the program places it using templates and the automatic router. It checks for dangling ports, signs, types, algebraic loops and layout, returns errors with hints to the model, and makes at most 3 attempts; **采用结果** (Accept result) appears only when the check passes, accepting can be undone, and **保存** (Save) writes the file.

### 20.7 Command line

```bash
circuit-drawer new "SEPIC converter, low-side MOSFET, input Vin, coupling capacitor C1, inductors L1 and L2, output diode D, Co in parallel with RL" -o sepic.json --edit
circuit-drawer new "five-level inverter" --plan -o plan.txt              # structure only, written to plan.txt
circuit-drawer new "five-level inverter" --structure plan.txt -o inv.json -p double --rounds 8
circuit-drawer new "Boost converter ..." -o boost.json --provider openai --model <model name> --anyway
circuit-drawer tweak buck.json "move D to the right"                     # result in buck_ai.json
```

1、`--anyway`: call the model even when the description matches a template.

2、`--image fig.png`: reference image, repeatable.

3、`--edit`: open the result in the editor.

4、`tweak` leaves the original file untouched and writes `<name>_ai.json` by default; `-o` changes it.

5、Save results you are happy with as templates (see [chapter 3](#3-drawing-from-templates)) so the same topology never needs the model again.

### 20.8 Other settings

`CIRCUIT_DRAWER_TIMEOUT` (seconds without a response before a request is abandoned, default 300), `CIRCUIT_DRAWER_MAX_TOKENS` (output limit, 64000 by default for Anthropic-compatible interfaces), `CIRCUIT_DRAWER_FALLBACK=0` (turn off the fallback parameter for gateways that do not support it). If a reply is cut off by the output limit, the incomplete table is never used; the program asks for one compact rewrite within the remaining rounds.

---

## 21. Export formats, LaTeX and Word

![Export circuit figure](images/manual/en/export-dialog.png)

### 21.1 Exporting from the editor

1、Click **导出** (Export) on the toolbar, or **文件 → 导出电路图…** (File > Export circuit figure).

2、**目标文件夹** (Target folder): browse or type a path; relative paths start from the folder of the current JSON, and new folders are created on export.

3、Tick PDF, SVG, PNG and/or TeX; **包含节点标号** (Include node labels) decides whether node and branch labels appear in the figure.

4、Click **开始导出** (Start export). Only the ticked formats are written; files with the same name are overwritten and other files are left alone. The browser remembers, per table, the last folder and formats that exported successfully.

5、**文件 → 导出全部模态…** (File > Export all modes) writes every mode and the overview in one go. With only TeX ticked, the mode sources are written; PDF, SVG and PNG also include the mode overview.

### 21.2 The four formats

| Format | Use |
| --- | --- |
| PDF | First choice for LaTeX papers, vector |
| SVG | First choice for Word and PowerPoint, vector |
| PNG | Bitmap preview, 600 dpi for single figures, 300 dpi for mode overviews |
| TeX | Standalone CircuiTikZ source, for archiving or manual touch-ups |

Select output formats with `-f`. Include `tex` explicitly when you want the TeX source, for example `-f pdf,tex`.

### 21.3 Size and fonts

Single-column figures are at most 8.79 cm wide, double-column figures at most 17.99 cm. Text is 8 pt Times (`mathptmx`), and symbol sizes are absolute. Do not scale the figure in your paper; keep it at 1:1 so the text matches the caption size.

### 21.4 LaTeX

```latex
\begin{figure}[!t]
  \centering
  \includegraphics{figs/llc_fullbridge.pdf}   % no width=, keeps 8 pt text
  \caption{Full-bridge LLC resonant converter.}
  \label{fig:llc}
\end{figure}
```

1、Use `figure*` for double-column figures. The mode overview `<name>_modes.pdf` is already laid out on one page and can be inserted directly as a `figure*`; alternatively insert `<name>_mode1.pdf`, ... separately and arrange them with `subcaption` or a similar package.

2、The exported `.tex` is a complete document with `\documentclass{standalone}` and cannot be `\input` directly. To use the source in a paper, copy its `circuitikz` environment and add the same `\usepackage{circuitikz}` and `\ctikzset{...}` settings to your preamble; using the PDF is usually simpler.

### 21.5 Word

Word versions that support SVG can insert the SVG directly at its original size; otherwise use the PNG.

### 21.6 Chinese text

When any text in a figure contains Chinese, `CJKutf8` (with the Song typeface) is loaded automatically. Full MiKTeX and TeX Live installations include it; minimal installations need the CJK packages (`latex-cjk-chinese` on Ubuntu). Waveform annotations accept only English and maths.

### 21.7 Black-and-white printing

1、Mode figures: `--mono` on the command line, or tick the black-and-white style in the Modes tab.

2、Regions and arrows: `--mono` on the command line, or **视图 → 色块灰度**.

3、Part colours: printed black automatically in black-and-white output.

---

## 22. Command-line reference

1、Template options for `draw`, `modes`, `export` and `edit` may appear before or after flags: `circuit-drawer edit fb.json -t flyback 二极管在下` also works. Unknown command flags are still rejected.

2、`circuit-drawer export` creates missing parent folders. An existing JSON requires a new name or explicit `--force` to replace it.

3、An output selection such as `-f pdf` does not create or overwrite a companion `.tex`. Include `tex` when source output is wanted.

4、If `check` fails at single-column width but passes at double-column width, stderr suggests `--profile double`. The JSON report and failure exit code remain unchanged; the drawing is never changed automatically.

5、Before `waveforms`, import CSV through **模态与波形 → 波形与模态**, check the curve and mode bindings, and save the table.

6、`new --image` identifies PNG, JPEG, GIF and WebP by the file header rather than the extension. Image and `--structure` read errors are reported before template matching or API-key checks. Structure files must be UTF-8 text. Header detection identifies the format; the model service still checks image completeness.

### 22.1 circuit-drawer

| Command | Purpose | Main options |
| --- | --- | --- |
| `list` | List templates and options | |
| `draw <template> [options...]` | Draw a template | `-p`, `-o`, `-f`, `--name`, `--grid`, `--mono` |
| `export <template> [options...] -o x.json` | Write a template's coordinate table | `-o` (required), `--force` (replace existing JSON) |
| `edit x.json [options...]` | Open the web editor | `-t`, `-p`, `--port`, `-o`, `--no-browser`, `--provider`, `--model` |
| `check x.json` | Check only, print a JSON report; exit code 1 on failure | `-p` |
| `render x.json` | Check and draw | `-p`, `-o`, `-f`, `--name`, `--grid`, `--mono` |
| `modes <template or JSON> [options...]` | Mode figures | `--modes`, `--only`, `--list`, `--no-sheet`, `--color`, `--mono`, `--off grey/dashed`, `--arrows`, plus the drawing options |
| `waveforms x.json` | Export waveforms and modes | `--wave-only`, plus the drawing options |
| `spice x.json` | Export SPICE, optionally run | `-o`, `--name`, `-p`, `--run`, `--ngspice` |
| `emi x.json` | Conducted EMI estimate and filter comparison | `-o`, `-p`, `--compare`, `--ngspice` |
| `impedance x.json` | Buck averaged output impedance | `--probe`、`--f-min`、`--f-max`、`--points`、`--amplitude`、`--settle`、`--compare`、`-o` |
| `dq-impedance x.json` | Grid-connected inverter dq impedance | `--sources A B C`、`--f-min`、`--f-max`、`--points`、`--amplitude`、`--settle`、`--max-step`、`--compare`、`-o` |
| `impedance-ratio model.json` | DC source/load impedance ratio and stability | `-o` |
| `dq-stability model.json` | dq matrix interconnection stability | `-o` |
| `spice-init x.json --preset buck/boost -o y.json` | Write example simulation parameters | `--force`, `-p` |
| `simulink x.json` | Export a Simulink build script | `-o`, `--name`, `-p`, `--diode-current`, `--control continuous/discrete`, `--approximate-mosfet` |
| `plecs x.json` | Export a PLECS Standalone model | As above, plus `--numerical-resistance` |
| `psim x.json` | Export a PSIM 2026 open-loop model builder | `-o`, `--name`, `-p`, `--diode-current`, `--approximate-mosfet` |
| `new "description" -o x.json` | Generate a new topology with AI | `-p`, `--rounds`, `--provider`, `--model`, `--edit`, `--plan`, `--image`, `--anyway`, `--structure`, `--port` |
| `tweak x.json "change"` | Change an existing table with AI | `-o`, `-p`, `--rounds`, `--provider`, `--model`, `--edit`, `--port` |
| `save-template x.json name` | Save as a template | `--title`, `--alias` (repeatable), `-p`, `--dir`, `--force` |

Drawing options: `-p single/double` (or `ieee-single`, `ieee-double`, `单栏`, `双栏`), `-o` output folder, `-f pdf,svg,png,tex`, `--name` file name, `--grid` coordinate grid. Every command accepts `--help` for its full list of options.

### 22.2 circuit-drawer-control

| Command | Purpose |
| --- | --- |
| `list` | List examples and saved templates |
| `export <name> -o x.json` | Write an example as JSON |
| `check x.json` | Check |
| `render x.json` | Draw (`-p`, `-o`, `-f`, `--name`, `--mono`, `--grid`) |
| `edit x.json` | Open the control diagram editor (`-t`, `-p`, `--port`, `-o`, `--no-browser`) |
| `save-template x.json name` | Save as a template (`--title`, `-p`, `--force`) |
| `loop x.json` | Loop gains, margins, closed-loop poles |
| `bode x.json` | Bode plot (`-p`, `-o`, `-f`, `--name`, `--mono`) |
| `design x.json -b block --fc frequency` | Suggest a Type I/II/III compensator (`--pm`, `--type`, `--break`, `--r1`, `-w`) |
| `code x.json` | Generate C code (`-o`, `--arith`, `--fs`, `--method`, `--q`, `--harness`) |

### 22.3 circuit-drawer-launcher

`circuit-drawer-launcher [file]`, with optional `--control` and `--settings settings-file`.

### 22.4 Python interface

```python
from circuit_drawer import from_template, render
lay = from_template("dab", "switch=igbt currents")
res = render(lay, "ieee-single", out_dir="figs")   # raises LayoutRejected if the check fails
```

---

## 23. Environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `CIRCUIT_DRAWER_NGSPICE` | ngspice executable | `ngspice_con` or `ngspice` on PATH |
| `PSIM_HOME` | PSIM installation directory used by the exported builder | None; the builder also accepts `--psim-home` |
| `CIRCUIT_DRAWER_TEMPLATES` | User template folder (`modules/` inside it is your module library, `control/` holds control diagram templates) | `~/.circuit_drawer/templates/` |
| `CIRCUIT_DRAWER_PROVIDER` | AI interface type: `anthropic` or `openai` | `anthropic`; `openai` when only `OPENAI_API_KEY` is set |
| `CIRCUIT_DRAWER_MODEL` | Model ID | `claude-opus-5-5` for Anthropic-compatible interfaces |
| `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) | Key for an Anthropic-compatible interface | None |
| `ANTHROPIC_BASE_URL` | Base URL of an Anthropic-compatible interface | `https://api.anthropic.com` |
| `OPENAI_API_KEY` | Key for an OpenAI-compatible interface | None |
| `OPENAI_BASE_URL` | Base URL of an OpenAI-compatible interface | `https://api.openai.com/v1` |
| `CIRCUIT_DRAWER_PLAN_EFFORT` | Reasoning effort for the structure step; `default` sends none | `low` |
| `CIRCUIT_DRAWER_EFFORT` | Reasoning effort for generating / editing; `default` sends none | `high` for Anthropic-compatible, none for OpenAI-compatible |
| `CIRCUIT_DRAWER_TIMEOUT` | Seconds without a response before a request is abandoned | 300 |
| `CIRCUIT_DRAWER_MAX_TOKENS` | Model output limit | 64000 for Anthropic-compatible |
| `CIRCUIT_DRAWER_FALLBACK` | `0` turns off the fallback parameter | On |

The interface type, model ID, base URL, key, reasoning effort and ngspice path in the launcher's API and simulation settings and in the web AI interface settings correspond to these variables. An editor started separately from the command line follows its own environment; you can also apply settings in its own web page.

---

## 24. FAQ and troubleshooting

**1. The shell cannot find the `circuit-drawer` command.**
Python's Scripts folder is not on PATH. Use `python -m circuit_drawer`, or add the folder printed by `python -c "import sysconfig; print(sysconfig.get_path('scripts'))"` to PATH.

**2. The `examples/...` files in this manual are missing.**
The examples are not in the wheel. Download the source archive or clone the repository and run the commands from its root folder; see [section 2.4](#24-getting-the-example-files).

**3. Export is unavailable or reports that it cannot export.**
The check has not passed. Work through the Issues tab as described in [chapter 11](#11-the-checker-and-the-issues-tab). An empty drawing can be saved, but export needs a passing check.

**4. Preview or PDF export cannot find pdflatex.**
LaTeX is not installed or not on PATH. Run `pdflatex --version` in a new terminal. MiKTeX downloads packages on the first figure, which needs a network connection and some patience.

**5. PDF works but SVG / PNG do not.**
Poppler's `pdftocairo` is missing; install it and add its `bin` folder to PATH.

**6. Figures with Chinese text fail to compile.**
`CJKutf8` is missing. MiKTeX installs it automatically; on Ubuntu install `latex-cjk-chinese`.

**7. The drawing does not fit a single column (`too-cramped`).**
Switch to double column (`-p double` or **文件 → 图纸栏宽**). Full-bridge-rectifier LLCs, three-phase and multilevel circuits usually need double column.

**8. A large circuit turns red when reopened.**
The JSON does not store the column width, so it opens at single column. Use `circuit-drawer edit x.json -p double`, or switch in **文件 → 图纸栏宽**.

**9. Port 8765 is in use.**
Start with another port, for example `--port 8766`. The launcher picks a free port automatically.

**10. I closed the browser. Is the editor still running?**
Yes. Open `http://127.0.0.1:8765` again, or click **重新打开浏览器** in the launcher. Stop an editor started from the command line with `Ctrl+C` in its terminal.

**11. "Export and simulate" cannot find ngspice.**
Set `CIRCUIT_DRAWER_NGSPICE` or choose `ngspice_con.exe` in the launcher, then restart the editor. Without ngspice you can still save parameters and export `.cir` files.

**12. The simulation stops with "timestep too small".**
This usually comes from floating nodes or switching discontinuities. Check the initial conditions, the source rise times and the PWM start delays first; you can also set a numerical stabilising capacitance (for example 100p–1n) in the transient settings and compare whether the quantities you care about change with its value.

**13. The PLECS export reports a state constraint between a voltage source and a capacitor.**
An input capacitor sits directly across an ideal voltage source. Give the capacitors a real ESR as described in [section 18.4](#184-differences-from-ngspice).

**14. The AI menu reports a missing key or cannot connect.**
Enter the settings in **AI → AI 接口设置…** or in the launcher and click **应用设置**; if you use environment variables, restart the editor after setting them. Click **测试连接** to confirm the base URL is reachable, and check that the interface type (Anthropic-compatible or OpenAI-compatible) matches your provider.

**15. An AI request returns 403.**
403 means the interface or gateway refused access. Check the interface type, base URL, model permissions and the provider's access rules as described in the server message included in the error; if the error says the fallback parameter is not supported, set `CIRCUIT_DRAWER_FALLBACK=0`.

**16. The AI connects the topology wrongly, or keeps failing the check.**
Use **先看结构** first and correct the connections in the list before generating; raise the rounds to 6–8 and switch to double column; fix the last few problems by dragging in the editor. For common topologies, start from a template and ask the AI for local changes.

**17. The AI takes a long time.**
A request with no response for `CIRCUIT_DRAWER_TIMEOUT` seconds (default 300) fails with an error. Large circuits are slow to generate; check the structure first, or lower the reasoning effort.

**18. After changing the base drawing, a mode is reported as invalid.**
A deleted part or changed wiring broke a loop. Click the notice in the Modes tab to jump there, then click the conducting devices again or edit the mode JSON.

**19. The part colours I set are gone in mode figures.**
That is by design: mode figures colour parts by conducting / idle state. Text colours are kept.

**20. Export overwrote my earlier figures.**
Files with the same name are overwritten. Use a separate output folder for each paper or version.

**21. Are the operating conditions still right after switching to MOSFET / IGBT?**
The program only changes symbols and does not verify the electrical behaviour; confirm it yourself.

**22. Formulas on the canvas look different from the preview.**
Maths on the canvas is an approximation, and unknown commands are shown as written. The preview and exported figures are authoritative.

**23. "用户指南" (User guide) in the Help menu does not open.**
It opens the project's online documentation in the browser and needs a network connection. When offline, open this manual directly.

---

## 25. Keyboard and mouse reference

### 25.1 Circuit editor

| Action | Key or mouse |
| --- | --- |
| Rotate selected parts clockwise / counterclockwise by 90° (transformers included) | `R` / `Shift+R` |
| Mirror switches and thyristors / reverse two-terminal parts | `F` |
| Enlarge / shrink symbol keeping its aspect ratio | `+` (or `=`) / `-` (or `_`) |
| Cut / copy / paste / duplicate | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` / `Ctrl+D` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Y` or `Ctrl+Shift+Z` |
| Delete selection | `Delete` or `Backspace` |
| Move by one grid step / ten grid steps | Arrow keys / Shift + arrow keys |
| Raise / lower among objects of the same kind | `Ctrl+]` / `Ctrl+[` |
| Bring to front / send to back among objects of the same kind | `Ctrl+Shift+]` / `Ctrl+Shift+[` |
| Previous / next mode | `[` / `]` |
| Finish / cancel the wire, region or arrow being drawn | `Enter` (or double-click) / `Esc` |
| Cancel a group drag in progress | `Esc` |
| Box-select / add to selection | Drag on an empty spot / Shift + drag or Shift + click |
| Pan | Middle-button drag, or hold Space and drag with the left button |
| Zoom | Scroll wheel |
| Snap to the grid only, without smart guides, while dragging | Hold `Alt` |
| Add a corner to a wire | Double-click the segment |
| Expand / fold a module instance | Double-click the instance |
| Open the action menu | Right-click |

These keys do nothing to the canvas while you type in a text field. On macOS `Cmd` can be used in place of `Ctrl`. The circuit editor has no save shortcut.

### 25.2 Control diagram editor

| Action | Key |
| --- | --- |
| Save | `Ctrl+S` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` or `Ctrl+Y` |
| Cut / copy / paste | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` |
| Delete | `Delete` or `Backspace` |
| Link mode | `L` |
| Nudge / faster nudge | Arrow keys / Shift + arrow keys (5 times the step) |

### 25.3 Waveform panel

| Action | Key |
| --- | --- |
| Move the selected annotation by 0.01 cm / 0.1 cm | Arrow keys / Shift + arrow keys |
| Delete the selected annotation | `Delete` |
| Deselect | `Esc` |
| Undo | `Ctrl+Z` |

---

## Appendix A. Coordinate table format

A coordinate table is JSON in layout units (about 1 cm). `circuit-drawer export <template> -o x.json` exports any template as a starting point. The packaged templates in [`src/circuit_drawer/templates/saved_tables/`](../src/circuit_drawer/templates/saved_tables/) are also good format references.

### A.1 Minimal example

```json
{
 "name": "rc",
 "elements": [
  {"id": "V1", "kind": "V", "pins": {"a": [0, 0], "b": [0, 2]}, "label": "$V$", "label_pos": "left"},
  {"id": "R1", "kind": "R", "pins": {"a": [0, 2], "b": [2, 2]}, "label": "$R$"},
  {"id": "C1", "kind": "C", "pins": {"a": [2, 2], "b": [2, 0]}, "label": "$C$"}
 ],
 "wires": [[[2, 0], [0, 0]]],
 "hops": [],
 "annotations": [{"at": [1, 0], "text": "$i$", "arrow": "left", "side": "below"}],
 "netlist": {"V1.b": "N1", "R1.a": "N1", "R1.b": "N2", "C1.a": "N2", "C1.b": "N0", "V1.a": "N0"}
}
```

### A.2 Top-level fields

| Field | Meaning |
| --- | --- |
| `name` | Figure name; `draw` uses it as the default output file name for a template (`render` uses the JSON file name; change it with `--name`) |
| `elements` | List of parts |
| `wires` | Wires, each a list of corners `[[x, y], ...]`, every segment horizontal or vertical |
| `hops` | Crossings that are not connected; each must lie on a horizontal wire |
| `annotations` | Free text and current arrows on wires |
| `netlist` | `"part.pin": "net name"`; optional, but checked pair by pair when present |
| `regions`, `callouts`, `blocks`, `glyphs`, `wave_symbols` | Regions, arrows, blocks, function symbols, standalone waveform marks |
| `modes` | List of modes |
| `waveform` | Waveform and mode settings (CSV reference, channels, intervals), without samples |
| `simulation` | Simulation parameters |
| `emi` | Conducted EMI channels, analysis window, filter and parasitic settings |
| `closed_loop` | Closed-loop control |
| `model_mapping` | Simulation models for the whole drawing |
| `electrical` | Stable node and branch numbering (maintained by the editor) |
| `no_connect` | Intentionally unconnected pins, such as `["R1.b"]` |
| `stack_usage` | Simulation purpose of stacked panels |
| `modules`, `module_view`, `module_authoring` | Module definitions, instances, and the packaging record kept by the editor |

Other top-level fields are read and written back unchanged. When the AI edits a figure, these extra fields are always taken from the table before the edit.

### A.3 Parts

Each part has an `id` (unique in the drawing), a `kind` and `pins`, and optionally `label`, `label_pos` (`above`, `below`, `left`, `right`) and `opts`. Pin names are listed in [chapter 5](#5-parts-library-and-parts). Without `label_pos`, horizontal two-terminal parts have their label above and vertical ones on the right.

`opts` accepts only the keys below; misspelt keys or keys used on the wrong part are reported as `bad-option` with the allowed values:

| Key | Parts | Values (default) | Effect |
| --- | --- | --- | --- |
| `color` | All | Colour name or `#rrggbb` | Colour of symbol, label and current arrow |
| `scale_length`, `scale_width` | All | 0.5–2 (1) | Symbol size along / across the pins |
| `scale` | All | 0.5–2 (1) | Old uniform size, overridden by the two keys above |
| `label_size` | All | 5–14 pt (8) | Size of label, current and ratio text |
| `label_offset` | All | `[dx, dy]` | Offset of a part label or transformer ratio from its default position |
| `dash` | `R`, `L`, `C` | `solid`, `dashed`, `dotted` | Symbol line style; does not change connections or simulation |
| `current` | Two-terminal parts | Text, such as `"$i_L$"` | Current arrow on the lead |
| `current_side` | Two-terminal parts | `^`, `_` (`^`) | Arrow text on the left or right of the a→b direction |
| `invert` | Two-terminal parts | `true`/`false` | Draws the symbol reversed only; ignored by simulation |
| `wave`, `wave_pos`, `wave_scale` | `V`, `I` | `step-up`, `step-down`, `pulse`; `auto`, `left`, `right`, `above`, `below`; 0.5–2 | Small waveform next to the source |
| `mirror` | `nmos`, `pmos`, `igbt`, `scr` | `true`/`false` | Gate on the other side |
| `battery` | `V` | `true`/`false` | Draw as a battery |
| `zener` | `D` | `true`/`false` | Draw as a Zener diode |
| `diode` | `nmos`, `pmos`, `igbt` | `true`/`false` (`true`) | Body / anti-parallel diode; `false` removes it from drawing and simulation |
| `ratio` | `transformer` | Text, such as `"$1:n$"` | Ratio text, display only |
| `dots` | `transformer` | `same`, `opposite`, `false` (`same`) | Dots; must agree with the simulation polarity |
| `core` | `transformer` | `true`/`false` (`true`) | `false` draws a coupled inductor |
| `bare` | `port` | `true`/`false` | No hollow circle, for wires ending at a block edge |

### A.4 Free text

Each `annotations` entry has `at` and `text`, optionally `size` (5–14 pt) and `color`. Adding `arrow` (`right`, `left`, `up`, `down`) makes it a current arrow on a wire, with `side` (`above`, `below`, `left`, `right`, default `above`) placing the text; `at` must then lie on a wire.

### A.5 Regions, arrows and blocks

Regions:

```json
"regions": [
 {"id": "M1", "from": [-0.75, 5.05], "to": [9.0, 9.65], "fill": "green", "label": "DAB 1"},
 {"id": "arm", "from": [0.75, -0.45], "to": [2.75, 5.45], "fill": "green", "label": "phase $a$",
  "stack": {"count": 2, "dx": 0.3, "dy": 0.3, "fills": ["blue", "teal"]}},
 {"id": "bridge", "fit": "members", "members": ["S1", "S2"], "fill": "green"}
]
```

| Field | Meaning |
| --- | --- |
| `from`, `to` | Two opposite corners; optional with `fit` |
| `fit` | Only `"members"`: fit the boundary to the members |
| `fill` | `green`, `blue`, `orange`, `red`, `purple`, `yellow`, `teal`, `gray` (default), `#rrggbb` or `none` |
| `border`, `border_color` | `none`, `solid`, `dashed`, `dotted`; border colour |
| `label`, `label_pos`, `size` | Label, position (`below`, `above`, `left`, `right`, `inside`), text size |
| `members` | Part IDs of the module members |
| `stack` | Stacked panels: `count` (1–6), `dx`, `dy`, `fills` |

Arrows (`callouts`): `from`, `to`, `style` (`block`, `line`, `wave`), `color`, `label`, `label_pos` (including `inline`), `label_at`, `size`; thin arrows also take `bend` (−120 to 120 degrees), `via` (list of corners), `dash`, `head` (`end`, `start`, `both`, `none`) and `isolation` (position along the line, between 0 and 1); wavy arrows take `wave_amplitude` (cm) and `wave_cycles` (1–8); block arrows take `border_color`, `border_width` (0.1–3 pt), `fill_opacity` (0–1), `foreground` and `over_parts`; `detail: false` means the arrow only points and the region is not a zoomed detail.

```json
"callouts": [
 {"from": [3.2, 4.5], "to": [4.85, 4.5], "style": "block"},
 {"from": [0.6, 2.4], "to": [1.6, 1.2], "style": "line", "bend": 50, "color": "blue",
  "label": "$v_\\mathrm{GS}$", "label_pos": "left"}
]
```

Blocks (`blocks`): `id`, `from`, `to`, `text` (one line, LaTeX allowed), optionally `fill` (`white` default, `none`, a palette colour or `#rrggbb`), `border` (`solid`, `dashed`, `dotted`), `color` and `size`. Function symbols (`glyphs`): `{"kind": "dcdc", "from": [0, 0], "to": [2, 2]}`, with `kind` one of `dcdc`, `acdc`, `dcac`, `mosfet`, `igbt`, `diode`.

### A.6 Standalone waveform marks

```json
"wave_symbols": [{"at": [2, 1], "wave": "step-down", "color": "blue", "scale": 1}]
```

`"on_wire": true` means the mark is drawn on a wire on purpose.

### A.7 Modes

Each `modes` entry: `name`, `caption`, `on`, `loops`, and optionally `arrows` (`{"at": [x, y], "dir": "right"}`) and `condition`. See [chapter 15](#15-operating-mode-figures).

### A.8 Simulation parameters

```json
"simulation": {
  "version": 1,
  "ground": "Vin.a",
  "components": {
    "Vin": {"dc": "24"},
    "L1": {"value": "500u", "initial": "0"},
    "D1": {"model": "generic_diode", "is": "1n", "n": "1", "rs": "0.01"},
    "S1": {"model": "ideal_switch", "ron": "0.01", "roff": "1G", "frequency": "50k",
           "duty": "0.5", "delay": "0", "rise": "10n", "fall": "10n"},
    "Co": {"value": "220u", "initial": "0"},
    "RL": {"value": "30"}
  },
  "transient": {"step": "200n", "stop": "80m", "max_step": "200n"},
  "probes": [
    {"name": "v_out", "kind": "voltage", "positive": "RL.a", "negative": "Vin.a"},
    {"name": "i_L", "kind": "current", "element": "L1"},
    {"name": "pwm", "kind": "drive", "element": "S1"}
  ]
}
```

1、`ground` must name a real pin. `components` must have exactly one entry per part, with nothing missing and no leftovers from deleted parts.

2、Source `wave` examples: `{"kind": "step", "initial": "0", "final": "5", "delay": "1m", "rise": "1u"}`; pulses add `fall`, `width`, `period`; sines are `{"kind": "sine", "offset": "0", "amplitude": "24", "frequency": "50", "delay": "0", "phase": "-120"}`.

3、IGBT: `{"model": "simplified_igbt", "vce0": "1.5", "ron": "0.1", "roff": "1G", ...PWM fields}`. Thyristor: `{"model": "simplified_thyristor", "vf": "1", "ron": "0.01", "roff": "1G", ...PWM fields}`, optionally `holding_current`, `latching_current`, `turn_off_time`.

4、Transformer: `{"model": "ideal_transformer", "ratio": "1", "polarity": "same", "reference_resistance": "1T"}`, optionally `magnetizing_inductance` and `leakage_inductance`; current probes on a transformer need `winding` (`primary`, `secondary`, or `upper`, `lower` for a centre tap).

5、Optional fields: `series_resistance` for L; `series_resistance` and `series_inductance` for V and vac; `snubber_resistance`, `snubber_capacitance`, `output_capacitance`, `diode_is`, `diode_n`, `diode_rs`, `phase_shift` (−180 to 180 degrees, positive lags) and `phase_ramp` (seconds) for switches; `transient.node_capacitance` (numerical stabilising capacitance).

### A.9 Simulation models for the whole drawing

```json
"model_mapping": {
  "switch": {"model": "mosfet", "vto": "3", "kp": "10", "gate_voltage": "10"},
  "L": {"model": "parasitic", "series_resistance": "0.1", "parallel_capacitance": "1p"}
}
```

Switches can use `ideal_switch`, `mosfet` or `igbt`; R, L and C can use `ideal` or `parasitic`. Parameters must be positive.

### A.10 Modules

```json
"modules": {"version": 1,
  "instances": [{"id": "M1", "definition": "llc_halfbridge", "display": "collapsed"},
                {"id": "M2", "definition": "llc_halfbridge", "display": "collapsed"}],
  "parts": [{"id": "Vin", "kind": "V", "label": "$V_{in}$"}, {"id": "RL", "kind": "R", "label": "$R_L$"}],
  "links": [["Vin.b", "M1.P"], ["Vin.a", "M1.N"], ["M1.P", "M2.P"], ["M1.N", "M2.N"],
            ["M1.OP", "RL.a"], ["M1.ON", "M2.OP"], ["M2.ON", "RL.b"]]}
```

1、`definitions` holds each definition's local coordinate table (`layout`), published ports (`ports`, referring to real `port` parts) and default simulation parameters (`defaults`); optionally `port_layout`, `editable` and `source`.

2、Instances: `id`, `definition` (the built-in `buck`, `boost`, `llc_halfbridge` and `dab` can be named directly), optionally `at`, `overrides` (differences only), `display` (`expanded`, `collapsed`, `partial`), `symbol`, `collapsed_size` (`{"w": 3, "h": 4}`, at least 0.8 per side), `appearance`, `group`, `unconnected` and `label`.

3、Each `links` entry holds two names: an instance port `instance.port` or a top-level pin such as `Vin.b`. `routes` stores the corners of hand-drawn link routes, keyed by the two end names joined with `|`. `parts` lists two-terminal parts placed automatically outside the ports.

4、New module wiring is stored in top-level `wires`, connecting the coordinates of instance ports. `modules.links` and `routes` remain available for legacy named connections. Expanded part IDs look like `M1/S1`.

### A.11 Stable electrical labels

```json
{"electrical": {"version": 1, "nodes": [{"id": "N1", "name": "OP", "pins": ["R1.a"]}, {"id": "N2", "pins": ["R1.b"]}],
                "branches": {"R1": 1}, "next_node": 3, "next_branch": 2}}
```

Maintained by the editor; normally not written by hand. Names start with a letter and have 1–41 ASCII letters, digits and underscores; the prefixes `cd_` and `ctl_` are reserved.

### A.12 Template files

`save-template` writes `{"template": {"name": ..., "title": ..., "aliases": [...], "checked_profile": ...}, "layout": table}`. `template.default_profile` sets the default column width.

---

## Appendix B. Drawing rules

Any error-level problem rejects the table, and no figure is produced. Codes are given in brackets.

### B.1 Source of the layout

1、Coordinates come only from hand-drawn templates or coordinate tables. The program never computes part positions and never uses a general layout algorithm.

2、When fitting a column, the horizontal scale follows the column width (8.79 cm single, 17.99 cm double, enlarged at most 1.4 times for double); the vertical scale stays between 1.0 and 1.2. Symbols and text have absolute sizes.

### B.2 Geometry (`not-orthogonal`, `too-short`, `zero-length`, `bad-order`)

1、Every wire segment, two-terminal part, switch and transformer winding must be horizontal or vertical. The only exception is parts drawn slanted in the standard drawing (such as the X-shaped capacitors of a Z-source network): two-terminal parts may sit at exactly 45°, and wires may only extend along their line.

2、The distance between a two-terminal part's pins must be at least the symbol length plus 0.06 cm of lead on each side; a switch's pins must be at least the body height apart.

3、Transformer windings are each vertical (or each horizontal after rotation), with pins ordered along the winding.

### B.3 Overlap (`body-overlap`, `line-through-body`, `collinear-overlap`, `too-close`)

1、Any two symbol bodies are at least 0.06 cm apart.

2、Wires and other parts' leads may not cross a symbol body, except for standalone waveform marks with `on_wire: true`.

3、Two wires may not run on top of each other; parallel wires are at least 0.18 cm apart.

### B.4 Crossings and joints (`unmarked-crossing`, `bad-hop`, `touches-lead`)

1、Where two wires cross without connecting, the crossing must be listed in `hops`, drawn as a hop on the horizontal wire; undeclared crossings are always rejected so readers never mistake them for connections.

2、A T-joint counts as a connection and gets a solid dot automatically; a wire ending in the middle of a lead is an error.

3、Three or more segments meeting at a point get a dot automatically.

### B.5 Connections (`open-pin`, `dangling-wire`, `shorted-pins`, `netlist-open`, `netlist-short`)

1、Every pin must connect to something (or be listed in `no_connect`); wire ends may not dangle.

2、When a netlist is declared, the checker derives the connections from the geometry and compares them pair by pair: missing connections and unwanted connections are both rejected.

### B.6 Text (`text-on-symbol`, `text-on-line`, `text-overlap`, `arrow-off-wire`, `subscript-braces`)

1、Labels, ratio text and free text may not lie on symbols or wires and keep at least 0.06 cm from each other. Text size is estimated for 8 pt Times.

2、Current reference arrows must lie on wires.

3、Subscripts with several letters need braces (`$V_{in}$`); otherwise a warning is given, which does not block export.

### B.7 Column width (`too-cramped`)

A horizontal scale below 0.55 means the drawing does not fit the column; the command line checks whether double column would work and says so.

### B.8 Annotations and modules

1、Region borders (including visible edges of stacked panels) may not cut through a part's symbol (`region-cuts-symbol`); module members must be inside their region (`member-outside`).

2、Arrows may not cover parts by default (`callout-on-symbol`); zoomed details may not be wired to the main circuit (`detail-wired`).

3、Blocks may not cover parts (`block-on-symbol`), wires may not cross blocks (`line-through-block`), blocks may not overlap (`block-overlap`), block text must fit (`block-text-overflow`), and thin arrows may not cross blocks (`callout-through-block`).

4、Folded boxes may not overlap each other or hide the symbols, leads, labels or pins of visible outside parts (`module-box-overlap`); port links must be routable (`module-route`).

5、Simulation exports (SPICE, Simulink, PLECS) do not refuse known annotation-only problems (arrows and text covering things, region borders, decorative colours and text formatting); drawing previews and PDF still require them to be fixed or explicitly allowed. Electrical connections, the netlist, parameters, model mappings and instance completeness are always checked.
