# Windows 双击启动器

## 一、首次安装

1、64 位 Windows 10／11 用户下载 `CircuitDrawer-Setup-2.1.0.exe`，双击并按向导安装。安装界面可选简体中文或英文，采用当前用户安装。[发布下载页](https://github.com/ahainur/circuit-drawer-for-ee/releases)

2、安装目录使用 ASCII 字符，允许空格。图纸和导出目录可包含中文。安装后从桌面或开始菜单打开 Circuit Drawer，包内已经提供 Python、TinyTeX、Poppler、ngspice 和 AI 依赖。绘图、出图和 ngspice 仿真可离线完成。

3、安装包尚未签名，Windows 可能显示“未知发布者”或“Windows 已保护你的电脑”。核对来源和校验和后，可选择“更多信息 → 仍要运行”。将下面命令输出的 SHA256 与同一发布页 `SHA256SUMS.txt` 的对应条目比较。

```powershell
Get-FileHash .\CircuitDrawer-Setup-2.1.0.exe -Algorithm SHA256
```

4、升级或卸载前先保存图纸，关闭启动器和正在运行的 Circuit Drawer 命令行任务。升级时运行新版安装程序。通过 Windows“设置 → 应用”卸载 Circuit Drawer，用户图纸和设置会保留。新图默认从用户目录选择保存位置，请将文档保存在自己的图纸目录。

### 开发者使用源码环境

1、项目已通过 Python 安装后，在项目目录执行下面的命令。

```powershell
python scripts/create_windows_launcher.py
```

2、生成的 `Circuit Drawer.exe` 就在项目目录，以后直接双击即可，也可以右键创建桌面快捷方式。

3、源码启动器使用本机 Python、LaTeX 和 Poppler。更新源码后，启动器使用更新后的代码。移动项目或重新安装 Python 后，重新运行上述安装命令。

## 二、打开和新建电路

1、**已有电路：** 选择以前保存的 `.json`，例如 `boost.json`。JSON 保存元件、坐标、连线和模态，是可继续编辑的源文件；PDF / SVG / PNG 是导出的图。

2、**新建电路：** 默认选择“空白电路（自己绘制 / AI 生成）”。保存后，从编辑器右侧元件库添加元件、画线；也可以点“描述新拓扑”输入要求，让大模型生成。若要在基础拓扑上修改，再选 Buck、LLC 等模板。为保护已有图，新建功能要求使用尚不存在的文件名；空白图可保存，画好并通过校验后才能导出。

3、**继续上次：** 默认记住最近使用的文件，下次双击会自动打开。也可以取消自动打开，从最近文件中选择。

4、**关闭浏览器后：** 在启动器选中正在运行的编辑器，点“重新打开浏览器”。启动器可以最小化；退出它会停止它启动的编辑器，退出前先在浏览器保存。

每个编辑器优先复用上次的本机端口，以保留浏览器记住的导出目录等设置；端口被占用时才改用空闲端口，此时浏览器设置需要重新选择。地址中的 `127.0.0.1` 始终指这台电脑；启动器不会占用或关闭你已在 PowerShell 中启动的编辑器。

## 三、API 和仿真设置

1、原来 PowerShell 中的 API 环境变量，现在可以在“API 与仿真设置”里填写。接口类型对应 `CIRCUIT_DRAWER_PROVIDER`，模型 ID 对应 `CIRCUIT_DRAWER_MODEL`，接口地址与密钥对应所选服务的 `OPENAI_*` 或 `ANTHROPIC_*`。DeepSeek 等服务要按使用的兼容协议选择接口类型。

2、API Key 默认仅用于本次启动器会话。勾选“在当前 Windows 账户中加密记住 API Key”后，下次不用再输；取消勾选并应用会移除保存的密钥。密钥通过 [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) 加密，设置保存在 `%LOCALAPPDATA%\CircuitDrawer\launcher.json`，不放进项目、JSON 电路或 Git。

3、绘图、模态导出和本机仿真可直接使用。Windows 安装包自带 ngspice，需要使用自备仿真器时，在这里选择 `ngspice_con.exe`，对应 `CIRCUIT_DRAWER_NGSPICE`。

4、点“应用设置”后，启动器内已打开的编辑器会在后续请求中使用新设置。先前从其他 PowerShell 窗口启动的编辑器仍使用它自己的配置。

5、电路编辑器和控制框图编辑器也可点 **AI → AI 接口设置…**，直接填写接口类型、模型 ID、接口地址、API Key 和思考强度。网页与启动器复用同一份设置；在同一启动器启动的窗口间，应用后立即对后续请求生效。正在运行的请求继续使用启动时的模型、地址、密钥和强度。

6、网页只显示密钥是否已配置，始终不回显已有密钥。只改模型或强度时选“保留现有密钥”；换接口类型或地址时选“替换密钥”并重新输入；选“清除密钥”后应用会清除当前接口的会话密钥及保存的密钥。记住密钥仍使用上述 Windows 加密存储，未勾选时仅在本次服务会话中使用。非 Windows 环境不提供加密记住功能。

7、若同一启动器下另一个网页或设置窗口刚改过配置，旧窗口会拒绝覆盖，请关闭后重新打开。另一启动器进程需重启才能读取已保存配置，未记住的密钥需重填。由命令行独立启动的服务仍以自己的启动环境为准，可在它的网页中单独应用设置；不会同步其他进程的内存配置。

8、启动器仍负责打开／新建文件、启动／停止编辑器、最近文件和 ngspice 路径。网页的 AI 设置不修改仿真器路径；配置时不会自动调用付费模型。命令行启动的编辑器若显式指定过模型或接口，在网页应用后，后续 AI 请求采用网页设置。

## 四、思考强度设置

1、在“API 与仿真设置”中分别选择“先看结构：思考强度”和“生成 / 修改：思考强度”。可选服务商默认、low、medium、high、xhigh、max。点“应用设置”后，后续请求使用新值，重新启动也会记住；设置仍保存于启动器配置，不写入电路 JSON。

2、“服务商默认”不发送额外思考参数，不代表关闭思考。它会覆盖之前保存或通过环境变量设置的强度。旧配置没有这两个字段时维持原行为：结构默认 low；OpenAI 兼容接口的绘图阶段不指定强度，Anthropic 绘图阶段默认 high。切换接口类型时，两个下拉框回到服务商默认，避免把上一家的档位直接套用。

3、这是一组请求参数，并非所有模型通用的能力开关。具体兼容关系如下：

| 接口或模型 | 本项目发送方式与限制 |
| --- | --- |
| OpenAI 兼容接口 | 使用 `reasoning_effort`；模型与网关必须支持相应档位。参见 [OpenAI 文档](https://developers.openai.com/api/docs/guides/reasoning)。 |
| Anthropic 兼容接口 | 指定档位时使用 `output_config.effort` 和 adaptive thinking；服务商默认时两者都不额外发送。支持范围见 [Claude 文档](https://platform.claude.com/docs/en/build-with-claude/effort)。 |
| DeepSeek | 官方接口支持 effort，但可能将不同请求档位映射到同一级，并非五档彼此独立。参见 [DeepSeek 文档](https://api-docs.deepseek.com/guides/thinking_mode/)。 |
| 千问等带独立开关／预算的接口 | 兼容 OpenAI 协议不等于支持 `reasoning_effort`。千问的 `enable_thinking`、`thinking_budget` 暂不由此设置控制，建议选择服务商默认。参见 [千问文档](https://platform.qianwenai.com/docs/api-reference/toolkitframework/openai-compatible/overview)。 |

4、接口明确拒绝所选强度时，程序显示错误并提示换档或选服务商默认，不再悄悄删掉 effort 参数重试。页面显示的是**请求发送的强度**；网关可能映射或忽略参数，程序不能据此证明模型实际采用了哪一档。已有的输出截断重试仍可能降到 low，每轮统计会分别记录，服务商默认不会自动改成 low。

5、命令行继续支持 `CIRCUIT_DRAWER_PLAN_EFFORT` 与 `CIRCUIT_DRAWER_EFFORT`。值为 `default` 表示不额外指定；例如需要结构 medium、绘图 high，可在启动器直接选择，或在启动命令前设置：

```powershell
$env:CIRCUIT_DRAWER_PLAN_EFFORT = "medium"
$env:CIRCUIT_DRAWER_EFFORT = "high"
```

提高强度可能增加等待时间和 token 用量，不保证消除拓扑、排布或校验问题。先用同一张电路比较效果，再决定默认档位。

## 五、开发说明

1、可设置 `CIRCUIT_DRAWER_RUNTIME` 指向运行环境根目录。它覆盖打包程序可执行文件旁的 `runtime` 目录。目录内程序位于 `tex/bin/windows/pdflatex.exe`、`poppler/bin/pdftocairo.exe`、`ngspice/bin/ngspice_con.exe`。ngspice 的显式设置优先，其余工具优先查找运行环境，缺少对应程序时使用系统 PATH。

2、包内 TinyTeX 的安装路径应使用英文字符，支持空格。图纸、输出文件和用户缓存支持中文路径。可写缓存位于 `%LOCALAPPDATA%/CircuitDrawer/runtime-cache/tex`，只在启动包内 LaTeX 的子进程中配置。缺失宏包时，错误信息会给出缺失文件名。

入口使用 Python 的 [`gui_scripts`](https://packaging.python.org/en/latest/specifications/entry-points/#use-for-scripts)：Windows 安装时生成没有控制台窗口的 `.exe`。仓库只跟踪启动器源码和安装脚本，不跟踪与本机 Python 路径关联的二进制文件。安装后的入口名称为 `circuit-drawer-launcher`，复制到项目目录后命名为 `Circuit Drawer.exe`。
