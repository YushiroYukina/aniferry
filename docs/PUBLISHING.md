# GitHub 发布准备

## 仓库资料

- 建议仓库名：`aniferry`
- 显示名称：番舟 · AniFerry
- 描述：Windows 追番资源追踪与下载器转交工具，支持多源检索、Bangumi资料、集数管理和字幕工作流导出。
- Topics：`anime`、`bangumi`、`bittorrent`、`rss`、`windows`、`tkinter`、`python`、`qbittorrent`、`aria2`
- 许可证：已准备MIT文本；发布前由项目持有人核对采用的许可和署名。
- 当前版本：`v2.0.0`。名称未作商标或所有平台同名排查；创建仓库时以实际可用性为准。

## 准备上传的内容

运行以下命令会生成新的 `publishing/AniFerry-2.0.0-时间戳/`：

```powershell
python tools/prepare_release.py
```

其中 `repository/` 是干净的源码目录，适合创建Git仓库；`*-Source.zip` 是源码备份包；`*-Windows-x64.zip` 才是给普通用户的可运行压缩包。`SHA256SUMS.txt` 提供校验值，`RELEASE_NOTES.md` 可复制到Release说明。

脚本通过允许清单复制源码、测试、文档、原创图标、许可证和工作流，不复制用户数据库、缓存、种子、任务文件、本地备份、验证笔记、旧构建或一次性诊断脚本。展示截图由 `tools/make_demo.py` 使用虚构数据生成。

## 创建源码仓库

1. 在GitHub创建一个名为 `aniferry` 的空仓库；不要另外生成README、许可证或.gitignore，以使用本项目提供的文件。
2. 进入准备包的 `repository/`，初始化Git并提交。提交身份使用你自己的Git配置，下面命令不替你设置身份。

```powershell
git init -b main
git add .
git diff --cached --stat
git commit -m "Prepare AniFerry 2.0.0"
```

3. 从GitHub新仓库页面复制真实远程地址，再设置 `origin` 并推送 `main`。不要把本机整个开发目录拖进仓库。当前准备脚本不创建远程仓库、不推送、不发布。

## 创建Release

源码上传并检查后，创建 `v2.0.0` 标签及Release，标题建议为“番舟 AniFerry 2.0.0”。说明采用 `RELEASE_NOTES.md`，附件上传 `AniFerry-2.0.0-Windows-x64.zip` 和 `SHA256SUMS.txt`；源码ZIP可选附上。

不要单独上传EXE替代完整Windows包；`_internal/` 是程序依赖的一部分。截图是演示，评分和候选均为虚构数据。

## 自动构建状态

已经准备 `.github/workflows/windows-build.yml`，在 `main` 的推送/PR以及手动触发时测试并构建Windows压缩包，仅使用仓库读取权限，不自动发布Release。[此前1.10.0已通过的云端构建](https://github.com/YushiroYukina/aniferry/actions/runs/39095487280)包含测试、Windows打包与启动检查。

发布新版本前应查看对应提交的 Actions 运行结果。二进制尚未进行代码签名；Windows可能显示未知发布者提示。

参考：[GitHub Releases说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)、[actions/checkout](https://github.com/actions/checkout)、[actions/setup-python](https://github.com/actions/setup-python)、[actions/upload-artifact](https://github.com/actions/upload-artifact)。
