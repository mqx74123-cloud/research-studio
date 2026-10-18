# 验证范围

## 公开仓库的自动检查（2026-10-18）

- 源码提交：`ef12ee0a389cc4d16d58222d484fd4d875f3967c`。
- [GitHub Actions Tests #4](https://github.com/mqx74123-cloud/research-studio/actions/runs/39102338520)：Windows 与 Ubuntu 两个任务均成功。
- 两个平台均执行 `python -m pytest tests tests_gui -q` 和三组 Node 前端检查。
- 修正了一条遗留引擎版本断言（0.2.0 → 0.3.0），以及两处 Windows 长路径/8.3 短路径比较。路径检查已在本地用同一目录的两种写法复现失败、修复后通过。

## Windows x64 离线包（2026-10-18）

使用新包内 Python 3.12.14 与随附依赖，确认应用和依赖均从包内导入：

- 186 项 Python unittest 回归检查通过，包括 GUI、Excel、投影、上传、科研模板、方案保存恢复与统计计算。
- 三组 Node 前端检查通过，涵盖图形入口、数据会话和结果清除。
- 使用合成 CSV / Excel，八类图形均生成可读取的 PNG、SVG、PDF；检查多页输出。
- 八类图形检查期间，Python 审计钩子禁止 socket 连接及 DNS 查询，未触发网络访问；PROJ 网络开关为关闭。
- 复制到含中文、空格的独立目录后，用实际 Windows 批处理启动；依赖状态、示例预览、Excel 模板、本地 API 生成森林图及 PNG/SVG/PDF/ZIP 下载均通过。
- 服务绑定 `127.0.0.1`，测试结束后已关闭本次验证进程。
- 保留 16 个 Python 依赖包原许可、Python 及其组件声明；附入 GEOS 3.13.1 完整上游源码及 Shapely 2.1.2 上游构建脚本。随包 Shapely/GEOS 二进制与 PyPI 官方 Windows wheel 的六个 DLL/PYD 文件逐字节一致。

## 历史基线

2026-10-16，原修复版本和独立源码候选已分别通过 133 项 GUI/后端及 53 项统计 unittest 检查、三组 Node 检查。此前五个旧 pytest 模块未在离线环境执行；上面的 GitHub Actions 已补上完整测试范围。

## 限制

测试使用合成数据，不能代替对真实研究数据、统计假设、坐标系及图件含义的核对。未在另一台干净 Windows 实机验证；没有提供 macOS/Linux 便携包。运行时字体优先使用本机字体，缺少中文字体时可能出现缺字或替代字体。本次没有增加统计方法。
