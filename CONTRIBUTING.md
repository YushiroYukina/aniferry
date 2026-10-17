# 参与番舟

欢迎提交资源匹配、下载器适配、界面及文档改进。修改前请说明具体触发条件，避免把一次网络失败直接归因为检索逻辑错误。

## 本地开发

使用 Windows x64、Python 3.12（含 Tcl/Tk）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest -v test_core.py test_ui.py test_details.py test_downloaders.py
.\.venv\Scripts\python.exe app.py --data-dir .\build\dev-data
```

`--data-dir` 可隔离开发数据。不要使用真实追番库做破坏性测试，也不要在测试中创建真实下载任务。接口测试使用本地模拟服务；图形测试需要可运行 Tk 的 Windows 会话。

## 修改与验证

- 修改资源筛选时保留片名、季度、集数和 infohash 核对；不要用删掉季度信息的方法扩大命中。
- 下载器提交成功不等于任务接收；接收需匹配哈希和目录，完成需确认完整文件。
- 修改界面后检查浅色、深色、1120×740 紧凑布局和多选操作。
- 图形对象只在 Tk 主线程创建、更新及释放。后台线程只把结果放入消息队列。
- 删除和清理功能要保留下载历史与文件，并防止晚返回的后台结果恢复已删除条目。
- PR 说明应包含问题、最终行为、验证方式及尚未验证的范围。

## 构建与演示

```powershell
.\build.ps1 -Python .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe tools\make_demo.py
.\.venv\Scripts\python.exe tools\prepare_release.py
```

演示脚本只使用虚构数据，不读取真实追番库。打包及发布目录由 Git 排除。
