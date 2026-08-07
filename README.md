# 数据采集平台

面向公开公告列表页的轻量化数据采集平台。通过网页添加站点、设置关键词和邮件参数后，平台按北京时间每天采集前一天的数据、去重并发送日报。

> 新安装实例不包含任何预置采集站点，也不包含任何历史采集结果。请在后台的“自定义采集站点”中按需手动添加已获授权访问的公开公告列表页。

## 功能

- 自定义站点管理：添加、编辑、重新识别、启用/停用和删除。
- 静态列表自动适配：使用 Scrapling 的自适应选择器识别公告链接与日期结构。
- 动态站点支持：可视 Chrome 中完成网站允许的人工登录或验证后，平台复用该会话；定时采集会自动打开并关闭临时标签页。
- 关键词管理：支持逗号或换行批量添加并自动去重。
- SQLite 指纹去重：同一公告不会重复入库或重复邮件发送。
- 邮件日报：可在网页设置 SMTP、发件人、收件人和每天发送时间。
- 安全：SMTP 授权码经 `APP_SECRET` 派生密钥加密保存；管理后台使用登录认证；noVNC 和 Chrome 调试端口不直接暴露公网。

平台不会绕过验证码、登录、访问控制或反爬封禁。遇到验证码、访问频率限制或站点拒绝访问时，会停止该站点任务并给出提示。

## 一键安装（原生 Linux）

支持使用 `systemd` 的 Ubuntu、Debian、RHEL、Rocky Linux、AlmaLinux、CentOS Stream、Fedora、openSUSE 和 Arch Linux。脚本会安装 Python、浏览器运行环境、Nginx、Chrome/Chromium、Xvfb 与 noVNC。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-platform/main/install-linux.sh | sudo bash -s -- https://github.com/dinggood615/data-collection-platform.git
```

默认使用 HTTPS 的 `5555` 端口。更换端口示例：

```bash
sudo -E PORT=8443 bash install-linux.sh https://github.com/dinggood615/data-collection-platform.git
```

首次登录为 `admin / admin`。请在页面底部“管理员账户”立即修改为强密码。

## Docker 安装

适用于已有 Docker 且不使用 systemd 的环境：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-platform/main/install-docker.sh | sudo bash -s -- https://github.com/dinggood615/data-collection-platform.git
```

## 安装后的首次配置

1. 访问 `https://<服务器 IP>:5555`，使用管理员账户登录。
2. 在“筛选关键词”添加业务关键词。
3. 在“邮件与定时”填写 SMTP 发件邮箱、授权码、收件邮箱及发送时间。
4. 在“自定义采集站点”填写网站名称和**公告列表页**网址，然后点击“自动识别并添加”。不要填写首页、项目详情页或搜索结果页。
5. 静态页面识别成功后，确认状态为“已适配（静态列表）”并启用。
6. 如果提示“待人工确认”，点击“打开此站验证”，在可视 Chrome 中完成网站允许的操作并停留在公告列表页，再返回平台点击“重新识别”。识别成功后状态为“已适配（动态浏览器）”。
7. 点击“立即采集”进行首次检查；确认邮件和结果正常后等待定时任务执行。

## 人工验证与动态采集

原生安装会把可视 Chrome 和 noVNC 安全地经平台反向代理提供。点击站点卡片的“打开此站验证”即可打开对应站点，不需要额外的 SSH 隧道。

人工验证仅在网站要求时进行一次。已适配的动态站点在后续采集期间会使用已保存的浏览器会话，在后台创建临时标签页采集并在任务结束后关闭该标签页。浏览器服务保留运行，以维持会话与后续人工验证入口。

## 维护

```bash
sudo systemctl status tender-platform
sudo journalctl -u tender-platform -f
sudo systemctl restart tender-platform
```

交互式安装、卸载入口：

```bash
sudo bash manage.sh
```

## 数据与安全建议

- 原生安装的数据目录：`/opt/tender-collection-platform/data`；Docker 使用 `platform_data` 卷。
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
