# SBS 本地桌面版

本分支新增 Windows x64 与 Mac Apple Silicon 桌面版。无需域名、服务器、Python 或 Git。

## 生成 Release

1. 合并本分支到 main。
2. 打开 Actions → Build desktop release，先用 Run workflow 测试。
3. 测试通过后，在 Releases → Draft a new release 创建标签（例如 v1.0.0）。
4. 发布标签后，工作流会自动附加：
   - SBS-Spark-Windows-x64.zip
   - SBS-Spark-macOS-Apple-Silicon.dmg

## 使用

1. 下载对应系统文件并解压/安装。
2. 打开 SBS Spark。
3. 填写好友昵称和随机消息。
4. 点击“扫码登录”，在抖音 App 确认后点击“完成扫码”。
5. 先点击“检查任务”，成功后再点击“立即发送”。

本地数据、登录状态和日志保存在用户应用数据目录，不会写入安装目录。

## 注意

- Windows 压缩包解压后运行 SBS-Spark.exe。
- macOS 安装包未经过 Apple 开发者签名和公证。首次打开若被拦截，请在“系统设置 → 隐私与安全性”选择“仍要打开”。
- 本地定时仅在软件保持运行且电脑未休眠时生效。
- storage-state.json 等同账号登录凭证，请勿上传或分享。
- 自动化操作可能触发平台安全验证或账号限制，仅建议个人账号低频使用。
