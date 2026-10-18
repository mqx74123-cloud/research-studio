# POI Studio

**本地科研绘图工具 · 源码公开，限非商业使用**

作者：**3.1415926** · 联系与商业授权：[mqx74123@gmail.com](mailto:mqx74123@gmail.com)

本仓库已公开 **v0.6.1-rc.1 预发布源码**。项目地址：[mqx74123-cloud/research-studio](https://github.com/mqx74123-cloud/research-studio)。图形界面版本与底层计算引擎版本分别管理；底层 `poi-audit-kit` 仍为 0.3.0。

从 Excel / CSV 导入数据，选择图形和字段，在本机生成 PNG、SVG、PDF。运行不需要 API Key、外部服务、在线底图或 CDN，界面只监听本机 `127.0.0.1`。仓库附带合成示例，不含作者的真实科研数据。

## 支持图形

| 图形 | 典型输入 |
|---|---|
| POI 地图 | 坐标列、已声明坐标系、研究区边界或明确的点位预览范围 |
| 散点图 | 两个数值指标，可选分组 |
| 半小提琴图 | 一个数值指标与分组列 |
| 四象限图 | 两个指标与明确的高低阈值 |
| 热图 / Pearson 相关矩阵 | 多个数值指标；行热图还需要唯一标签 |
| 森林图 | 估计值、区间上下限及区间含义 |
| 哑铃图 | 同一对象的两组同尺度数值 |
| 多指标雨云图 | 分组列与多个原始观测指标 |

八类图形统一在第二步“选择图形”中选择。四种科研图形会进入对应配置页，已读取的自有表格会一起带入。

## 使用

Windows 10/11 x64 用户：前往 [Releases](https://github.com/mqx74123-cloud/research-studio/releases)，在对应版本的 **Assets** 中选择 `POI-Studio-v0.6.1-rc.1-Windows-x64.zip`。离线包已完成本机验证，若页面尚无此附件，表示维护者尚未上传。

1. 将整个 ZIP 解压到有写入权限的目录，不要直接在压缩包内启动。
2. 双击 `启动制图界面-Windows.bat`，浏览器会打开本机界面。
3. 导入 Excel/CSV，第二步选择图形，核对字段后生成并导出。
4. 关闭启动窗口即可停止服务；数据、方案和结果保存在本机 `gui-work/`。

离线包自带 Python 和依赖，无需另装 Python、Node.js，也不需要 API Key。**GitHub 的 Download ZIP 和 Release 自动生成的 Source code ZIP 是源码，不包含运行环境。**

开发者：Python 3.10 或更高版本。在源码目录创建虚拟环境，安装依赖后运行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[gui,dev]"
.venv\Scripts\python.exe launch_gui.py
```

安装依赖通常需要联网；离线开发环境可使用事先准备好的 wheel 包。**安装过程与软件日常运行是两回事**，启动脚本不会自动联网安装依赖。

已有环境可直接执行 `python launch_gui.py`，Windows 也可双击 [启动制图界面-Windows.bat](启动制图界面-Windows.bat)。关闭服务窗口即停止程序。

## 数据与结果

模板在 [examples/templates](examples/templates)。先读取工作表与表头，再核对字段含义、数据单位及每行的观测单位。所有示例、示例坐标和边界均为虚构数据，不应作为真实研究结论。

用户数据快照、方案与历史结果写入本机 `gui-work/`，不应提交 Git。详细操作及统计边界见 [使用说明](docs/USER_GUIDE.md)。

## 测试

```text
python -m pytest tests tests_gui -q
node tests_gui/test_ui_states.js
node tests_gui/test_extent_preview_ui.js
node tests_gui/test_research_ui.js
```

Node.js 仅供前端开发测试，不是普通用户使用软件的依赖。测试使用合成数据与临时目录。

2026-10-18：[GitHub Actions 第 4 次检查](https://github.com/mqx74123-cloud/research-studio/actions/runs/39102338520)的 Windows 与 Ubuntu 任务均通过，覆盖完整 Python 测试及三组 Node 检查。新版 Windows 离线包另以自带解释器通过 186 项 Python 检查、三组 Node 检查、八类图形导出及中文/空格路径启动检查。具体范围和限制见 [验证说明](docs/VALIDATION.md)。

## 许可与引用

本候选采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，商业授权联系作者。允许范围、组织例外、图件使用及历史 MIT 授权见 [LICENSE_POLICY.md](LICENSE_POLICY.md)。旧版已取得的 MIT 权利不会被本候选撤回。第三方依赖遵循各自许可，见 [第三方声明](THIRD_PARTY_NOTICES.md)。

科研使用可引用作者 3.1415926 与软件版本，机器可读信息见 [CITATION.cff](CITATION.cff)。贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。
