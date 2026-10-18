# Third-party notices / 第三方声明

项目自有材料的许可见 LICENSE 与 LICENSE_POLICY.md。新许可不会覆盖第三方的版权或许可证，也不会撤销旧版已授予的 MIT 权利。历史声明在 NOTICE 与 LICENSES/PRIOR-MIT.txt。

## 源码与 Windows 离线包

源码仓库不附带运行环境。Windows 离线包附带解释器与依赖，并保留原始许可文件；在包内可查看 `OFFLINE-DEPENDENCIES.json` 的实际版本清单、`LICENSES/THIRD_PARTY/` 的许可汇总，以及各组件原目录的声明。

| 组件 | 随包许可与说明 |
|---|---|
| Python 3.12.14 | `runtime/LICENSE.txt`；汇总目录另附 Python 官方 History and License，含 OpenSSL、libffi、zlib、bzip2 等组件说明 |
| Tcl/Tk 8.6.12 | 原 tk 声明及上游 Tcl/Tk `license.terms` |
| NumPy、OpenBLAS、LAPACK | 原 NumPy 许可汇总及随附组件声明；包括 GCC Runtime Library Exception |
| Matplotlib、DejaVu、STIX | 保留 Matplotlib 及随附字体的许可证；不额外打包 Windows 系统中文字体 |
| Shapely 2.1.2、GEOS 3.13.1 | Shapely BSD；GEOS LGPL-2.1；见下文源码与替换说明 |
| pyproj、PROJ 及数据库 | 保留 `LICENSE`、`LICENSE_proj` 和随附数据声明 |
| openpyxl、Pillow、fonttools 等 | 保留实际包内版权、许可及第三方组件声明 |
| certifi | MPL-2.0；Python 源文件及证书数据以源码形式保存在 `runtime/Lib/site-packages/certifi/` |
| Microsoft VC runtime | 保留原始未修改运行库和随附 MSVC 声明；不将其重新许可为 PolyForm |

pytest、build、Node.js 是开发/测试工具，Windows 离线包不要求用户安装它们。

## GEOS 对应源码与库替换

离线包的 `THIRD_PARTY_SOURCES/` 包含 GEOS 3.13.1 完整上游源码、Shapely 2.1.2 源码及构建脚本、来源 URL 与 SHA-256 清单。Shapely/GEOS 的六个 DLL/PYD 文件已与 PyPI 官方 Windows wheel 比对一致，未做本项目修改。

GEOS 以可替换的共享 DLL 保存在 `runtime/Lib/site-packages/shapely.libs/`。本项目的非商业许可不限制第三方库原许可授予的权利，也不禁止为修改这些库及调试相关修改而进行其许可允许的逆向工程。详细重建和替换说明见离线包内 `THIRD_PARTY_SOURCES/README.md`；不得将第三方源码单独重新许可为 PolyForm。

本项目使用 AI 辅助编写。输入数据、边界和用户选择的字体仍需使用者确认有权使用。
