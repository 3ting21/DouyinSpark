# 第三方组件与许可

本程序以单文件 exe 分发，内置以下第三方组件；发布包随附可获取到的许可文本，其余以对应项目主页的许可为准。

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| Python | 运行时 | Python Software Foundation License |
| Tcl/Tk | 图形界面 | Tcl/Tk license terms |
| PyInstaller | 打包 | GPL-2.0-or-later with bootloader exception |
| Playwright (Python) | 浏览器自动化 | Apache-2.0 |
| Playwright Node driver | 驱动进程 | Apache-2.0 |
| Node.js (`node.exe`) | 驱动运行时 | MIT |
| greenlet | Playwright 依赖 | MIT |
| pyee | Playwright 依赖 | MIT |

- `Python-LICENSE.txt`：Python 运行时许可（由构建环境提供）。
- `TclTk-LICENSE.txt`：Tcl/Tk 8.6 许可条款（由构建环境提供）。
- `PyInstaller-COPYING.txt`：PyInstaller 的 GPL 许可及打包例外。
- `Playwright-LICENSE.txt`：Playwright 的 Apache-2.0 许可。

PyInstaller 的打包例外允许分发其生成的可执行程序。Microsoft Edge 由操作系统提供，本程序不打包、不再分发 Edge 本体，使用时受 Microsoft 软件许可约束。

本软件与抖音及其关联公司无隶属、赞助或背书关系。自动化操作由使用者自行承担平台规则与账号风险。
