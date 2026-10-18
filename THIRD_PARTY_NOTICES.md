# Third-party notices / 第三方声明

项目自有材料的许可见 LICENSE 与 LICENSE_POLICY.md。新许可不会覆盖或替换第三方的版权与许可证，也不会撤销旧版已授予的 MIT 权利。原项目归属信息和旧许可保存在 NOTICE 与 LICENSES/PRIOR-MIT.txt。

## 源码仓库

本候选源码不附带 Python 解释器、依赖 wheel、第三方字体或真实研究数据。依赖通过 pyproject.toml 声明，安装后的第三方包仍遵循各自许可。

| 组件 | 主要许可 / 说明 |
|---|---|
| Python（仅将来的离线发行包） | Python 自身及随附组件的许可 |
| NumPy | BSD 及随附组件声明 |
| Matplotlib | Matplotlib 许可及随附字体/组件声明 |
| Shapely | BSD；其二进制发行中 GEOS 有独立许可义务 |
| pyproj | MIT；PROJ、数据文件等有独立声明 |
| openpyxl | MIT |
| Pillow、fonttools 等传递依赖 | 以所用版本包内许可为准 |
| pytest、setuptools、build、Node.js | 开发/测试工具，各自许可 |

## Windows 离线发行包

离线包会附带解释器和依赖，与本源码包不同。不能复用旧声明中的“没有附带依赖”描述。打包时应保留所有实际组件版权、许可证、版本清单，并核对 GEOS 等组件有关再分发、对应源码或获取方式的要求。**单独列出软件名称或附上许可证，不等于完成全部二进制再分发义务。**

当前提供的是源码候选；新的非商业版离线包应在完成该核对后另建。不要把此前带有 MIT 许可证的旧 ZIP 改名后当作新的非商业包发布。

本项目使用 AI 辅助编写。该事实不构成第三方素材权利保证，使用者仍需核对输入数据、边界与字体的来源。
