# 数据采集管理平台

面向公开公告列表页的智能数据采集管理平台。平台提供响应式管理控制台、站点自动识别、关键词筛选、数据去重、邮件与企业微信推送、可视浏览器验证、自动备份以及跨服务器一键迁移。通过网页完成配置后，系统可按北京时间定时采集前一天的数据并推送日报。

> GitHub 发行版采用完全空白的业务初始状态：不包含采集站点、关键词、采集结果、运行记录、邮件定时或企业微信配置。请安装后在网页中按需配置。

## 全新安装的初始状态

| 项目 | 初始状态 |
| --- | --- |
| 控制台统计、采集结果、最近运行 | `0`，不包含演示记录 |
| 自定义采集站点 | 空，不预置任何网站 |
| 筛选关键词 | 空，不预置任何行业词 |
| 邮件与定时 | 全部留空，未配置前不会启动每日定时采集 |
| 企业微信推送 | Webhook 为空，默认关闭 |
| 企业微信助手 | CorpID、回调地址、管理员和密钥均为空 |
| 管理员 | 保留首次登录所需的 `admin / admin`，登录后必须修改 |
| 数据库自动备份 | 保留平台运维默认策略，可在页面修改 |

仓库不跟踪 SQLite、数据库日志、迁移 ZIP 或浏览器会话目录。执行 GitHub 更新只更新程序，不会删除已经部署实例的数据库；因此现有服务器升级时仍会保留原站点、结果和通知配置。

## 功能

- 自定义站点管理：添加、编辑、重新识别、启用/停用和删除。
- 静态列表自动适配：使用 Scrapling 的自适应选择器识别公告链接与日期结构。
- 动态站点支持：可视 Chrome 中完成网站允许的人工登录或验证后，平台复用该会话；定时采集会自动打开并关闭临时标签页。
- 关键词管理：支持逗号或换行批量添加并自动去重。
- SQLite 指纹去重：同一公告不会重复入库或重复邮件发送。
- 邮件日报：可在网页设置 SMTP、发件人、收件人和每天发送时间。
- 企业微信助手：填写 CorpID、HTTPS 地址和管理员 UserID 后自动生成回调配置，可通过聊天查询状态、启动采集、查看结果或创建备份。
- 企业微信机器人推送：保存群机器人 Webhook，采集后自动发送日报并支持测试消息。
- 健康检查：`/healthz` 返回服务和数据库就绪状态，原生安装完成后会自动校验。
- 自动备份：每日创建 SQLite 一致性备份，可在网页设置备份时间与保留天数。
- 一键迁移：网页导出完整迁移包，在新服务器安装后直接导入站点、关键词、通知配置、历史结果和管理员设置；导入前自动生成回滚备份。
- 企业级响应式界面：采用低噪声卡片、Bento 数据概览、清晰状态层级和键盘焦点反馈，适配手机、平板与桌面。
- VPS 一键安全更新：自动备份数据库、执行 `git pull --ff-only`、更新依赖和数据库结构、重启服务并完成健康检查；失败时自动恢复更新前代码。
- 安全：SMTP 授权码经 `APP_SECRET` 派生密钥加密保存；管理后台使用登录认证；noVNC 和 Chrome 调试端口不直接暴露公网。

平台不会绕过验证码、登录、访问控制或反爬封禁。遇到验证码、访问频率限制或站点拒绝访问时，会停止该站点任务并给出提示。

## 一键安装（原生 Linux）

支持使用 `systemd` 的 Ubuntu、Debian、RHEL、Rocky Linux、AlmaLinux、CentOS Stream、Fedora、openSUSE 和 Arch Linux。脚本会安装 Python、浏览器运行环境、Nginx、Chrome/Chromium、Xvfb 与 noVNC。

安装器会等待最多 60 秒让应用启动。等待期间不会显示误导性的临时连接错误；只有服务真正失败或超时才会输出 systemd 状态与最近日志。后端和 HTTPS 入口均通过健康检查后才会显示“安装完成”。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-linux.sh | sudo bash -s -- https://github.com/dinggood615/data-collection-management-platform.git
```

默认使用 HTTPS 的 `5555` 端口。更换端口示例：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-linux.sh | sudo env PORT=8443 bash -s -- https://github.com/dinggood615/data-collection-management-platform.git
```

如需企业微信聊天助手或受信任 HTTPS，先将域名 A 记录解析到服务器公网 IP，然后直接运行上方的一键安装命令。安装过程中会询问是否配置 HTTPS、域名和证书通知邮箱；确认后会自动监听 `443`、在已启用 UFW 时放行 `80/443`、申请 Let's Encrypt 证书并启用自动续期。

腾讯云、阿里云等平台仍需在云安全组中放行 TCP `80`、`443`。选择跳过 HTTPS 时平台会使用自签名证书，企业微信聊天回调不可用。

首次登录为 `admin / admin`。请在页面底部“管理员账户”立即修改为强密码。

## Docker 安装

适用于已有 Docker 且不使用 systemd 的环境：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | sudo bash -s -- https://github.com/dinggood615/data-collection-management-platform.git
```

## 安装后的首次配置

1. 访问 `https://<服务器 IP>:5555`，使用管理员账户登录。
2. 在“筛选关键词”添加业务关键词。
3. 在“邮件与定时”填写 SMTP 主机、端口、发件邮箱、授权码、收件邮箱及发送时间；保存前定时采集保持关闭。
4. 在“自定义采集站点”填写网站名称和**公告列表页**网址，然后点击“自动识别并添加”。不要填写首页、项目详情页或搜索结果页。
5. 静态页面识别成功后，确认状态为“已适配（静态列表）”并启用。
6. 如果提示“待人工确认”，点击“打开此站验证”，在可视 Chrome 中完成网站允许的操作并停留在公告列表页，再返回平台点击“重新识别”。识别成功后状态为“已适配（动态浏览器）”。
7. 点击“立即采集”进行首次检查；确认邮件和结果正常后等待定时任务执行。

如果是从旧服务器迁移，请先在旧平台“备份与迁移”中下载完整备份；完成新服务器一键安装后，在同一区域上传该 ZIP 文件并确认恢复。导入完成后，旧站点、关键词、SMTP、企业微信配置、历史结果和管理员账户都会恢复，无需逐项重新填写。

## 备份、导入与迁移

- “下载完整备份”会生成可跨服务器使用的 ZIP 迁移包。
- 迁移包包含数据库和恢复通知服务所需的敏感配置，请像密码文件一样保管，不要上传到网盘公开链接或提交到 GitHub。
- 导入仅接受平台生成的迁移包，并会检查文件结构、版本、SQLite 完整性和 100 MB 大小上限。
- 导入前会自动创建当前数据库备份；导入成功后，页面会显示回滚备份文件名。
- 如果迁移包恢复了旧管理员密码，导入后请使用旧平台管理员账户重新登录。

## 企业微信助手

企业微信群机器人适合接收日报；如需通过聊天控制平台，请使用“企业微信助手”。页面仅需填写 **CorpID、平台 HTTPS 公网地址、管理员 UserID**，系统会自动生成并展示回调地址、Token 与 EncodingAESKey。

将这三项复制到企业微信管理后台“应用管理 → 自建应用 → 接收消息”并保存验证。验证成功后，可在企业微信应用中发送：`状态`、`立即采集`、`最新结果`、`备份`。

企业微信后台的首次回调验证必须由企业管理员完成；平台不会绕过该安全机制。回调地址使用 `https://你的域名/wecom/callback`，请确保域名具有有效 HTTPS 证书且平台反向代理已开放标准 HTTPS 入口。

## 人工验证与动态采集

原生安装会把可视 Chrome 和 noVNC 安全地经平台反向代理提供。点击站点卡片的“打开此站验证”即可打开对应站点，不需要额外的 SSH 隧道。

人工验证仅在网站要求时进行一次。已适配的动态站点在后续采集期间会使用已保存的浏览器会话，在后台创建临时标签页采集并在任务结束后关闭该标签页。浏览器服务保留运行，以维持会话与后续人工验证入口。

## 维护

```bash
sudo systemctl status tender-platform
sudo journalctl -u tender-platform -f
sudo systemctl restart tender-platform
```

健康检查：

```bash
curl -k https://127.0.0.1:5555/healthz
```

## VPS 一键更新

原生 Linux 一键安装的实例可使用下面的命令更新到 GitHub `main` 最新版本：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/update-linux.sh | sudo bash
```

脚本会自动完成：

1. 识别 `/opt/data-collection-management-platform` 或旧版 `/opt/tender-collection-platform` 安装目录。
2. 检查当前分支和本地代码修改；存在未提交修改时停止，避免覆盖定制采集器。
3. 创建 `pre-update-日期时间.sqlite3` 数据库备份。
4. 从 GitHub `main` 执行安全的 `git pull --ff-only`。
5. 更新 Python 依赖、初始化兼容数据库结构并调整迁移包上传限制。
6. 重启平台和 Nginx，完成健康检查；失败时自动恢复更新前代码。

自定义安装目录可明确指定：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/update-linux.sh | sudo env INSTALL_DIR=/opt/你的安装目录 bash
```

也可进入安装目录运行交互式管理菜单，选择“原生 Linux 更新”：

```bash
cd /opt/data-collection-management-platform
sudo bash manage.sh
```

数据库备份默认保存在安装目录的 `data/backups/`。可在后台“数据库备份”区域调整备份时间和保留天数。

交互式安装、卸载入口：

```bash
sudo bash manage.sh
```

## 一键卸载

下列命令会停止平台服务，并删除平台程序、SQLite 数据、浏览器会话、平台 TLS 文件、专用 Nginx 配置，以及该平台创建的 Docker 容器和卷（如存在）。不会卸载 Nginx、Docker 或其他系统服务。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/uninstall-linux.sh | sudo bash -s -- --yes
```

## 数据与安全建议

- 原生安装的数据目录：`/opt/data-collection-management-platform/data`；Docker 使用 `platform_data` 卷。
- 不要提交 `.env`、SMTP 授权码、服务器密码或 `APP_SECRET`。
- 生产环境应配置有效 TLS 证书，并仅向可信网络开放管理端口。
- noVNC、VNC 和 Chrome 调试端口仅应监听本机；不要单独开放到公网。
- 定期备份 SQLite 数据库和 `.env`，并在升级前确认备份可用。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```
