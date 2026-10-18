# POI Studio v0.6.1-rc.1 · Windows 本地离线预发布

作者：3.1415926。Excel/CSV 导入，八类图形，PNG/SVG/PDF 导出。

## 下载与使用

Windows 10/11 x64 用户选择附件 `POI-Studio-v0.6.1-rc.1-Windows-x64.zip`，完整解压后双击 `启动制图界面-Windows.bat`。自带 Python 和依赖，日常运行无需联网、API Key 或另装开发环境。关闭启动窗口即可停止服务，用户数据和结果在本机 `gui-work/` 中。

Release 的 Source code ZIP/TAR.GZ 仅供开发者，不是免安装包。旁边的 `.sha256` 文件用于校验下载完整性。

## 本次内容

- 地图、散点、半小提琴、四象限、热图/相关矩阵、森林图、哑铃图、多指标雨云图统一到“选择图形”。
- 修复切换图形或参数后旧预览、下载链接残留，读取成功提示和导入失败后的旧数据状态。
- 修正旧版本断言和 Windows 长短路径测试兼容问题。
- 整理非商业许可、第三方许可证、GEOS 对应源码、上游构建脚本和依赖清单。

## 验证

GitHub Actions Windows/Ubuntu 自动检查通过。离线包自带解释器通过 186 项 Python 检查、三组前端检查、八类图形导出及中文/空格目录启动检查。尚未在另一台干净 Windows 实机验证，因此标记为预发布。

## 许可

项目自有材料采用 PolyForm Noncommercial License 1.0.0，以原文规定的允许用途及组织例外为准。商业授权联系 mqx74123@gmail.com。旧版 MIT 已授予的权利不受影响；第三方组件继续遵循各自许可。
