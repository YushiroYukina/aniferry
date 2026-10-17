# 第三方组件与服务

番舟源码使用 MIT 许可证。Windows 发行包还包含 Python、Tcl/Tk、Pillow 及 PyInstaller 引导程序，各自许可证见发行包内 `licenses/`；构建脚本从实际构建环境收集 Python、Pillow 和 PyInstaller 的许可文本，Tcl/Tk 文本由项目保存。

| 组件 | 用途 | 许可文本 |
| --- | --- | --- |
| CPython 与标准库 | 程序运行 | `licenses/Python-LICENSE.txt` |
| Tcl/Tk | 桌面界面 | `licenses/Tcl-license.terms`、`licenses/Tk-license.terms` |
| Pillow | 封面显示、圆角和阴影绘制 | `licenses/Pillow-LICENSE.txt` 及其附带的其他许可文本 |
| PyInstaller bootloader | Windows 程序启动封装 | `licenses/PyInstaller-COPYING.txt`，包含 Bootloader Exception |

外部数据服务包括 Nyaa、AnimeGarden、蜜柑、動漫花園、Bangumi、AniList、Jikan。番舟通过其公开页面、RSS 或 API 获取数据；不代表与这些服务或下载器存在官方合作。数据和封面的权利属于各自权利人，不随番舟源码许可证转让。

本仓库展示截图使用虚构作品、评分与候选，不包含用户追番记录或抓取的第三方封面。标识和演示封面由项目绘制。

许可文本来源：

- [CPython](https://github.com/python/cpython/blob/3.12/LICENSE)
- [Tcl 8.6](https://github.com/tcltk/tcl/blob/core-8-6-16/license.terms)
- [Tk 8.6](https://github.com/tcltk/tk/blob/core-8-6-16/license.terms)
- [Pillow](https://github.com/python-pillow/Pillow/blob/main/LICENSE)
- [PyInstaller](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt)
