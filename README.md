# 招标采集管理平台

面向公开招标与采购公告的轻量管理平台：按关键词筛选前一天公告、SQLite 去重、定时邮件日报，并提供站点自动识别与人工验证入口。

## 功能

- 红色商务风管理后台：站点、关键词、邮件、运行记录和入库结果集中管理；
- 默认适配华润守正招标/采购公告与浙江能源公告；可在页面启停；
- 自定义公开列表页：低频识别链接密度、标题日期及动态特征，自动选择静态采集规则；
- Scrapling 自适应选择器：为静态站点保存列表结构，在小幅页面改版后尝试重新定位；
- 动态页、登录或验证码：显示“待人工确认”，可使用可视 Chrome 完成人工验证后重新识别；
- 每天按北京时间执行，默认采集前一天数据；指纹去重后发送邮件日报；
- SMTP 授权码使用 `APP_SECRET` 派生密钥加密保存，不在页面回显。

> 平台仅面向公开且允许访问的信息。它不会绕过验证码、登录、访问控制或反爬封禁，也不使用代理轮换。

## 一键安装（原生 Linux）

适用于具有 `systemd` 的 Ubuntu、Debian、RHEL、Rocky Linux、AlmaLinux、CentOS Stream、Fedora、openSUSE 与 Arch Linux。脚本会识别 `apt`、`dnf`、`yum`、`zypper` 或 `pacman` 并安装所需依赖。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/tender-collection-platform/main/install-linux.sh | sudo bash -s -- https://github.com/dinggood615/tender-collection-platform.git
```

脚本会自动安装 Nginx、生成本机 TLS 证书，并在 HTTPS `5555` 端口提供后台与人工验证入口；应用、VNC、noVNC 与 Chrome 调试端口只监听本机。可用环境变量改公网端口：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/tender-collection-platform/main/install-linux.sh | sudo PORT=8443 bash -s -- https://github.com/dinggood615/tender-collection-platform.git
```

首次登录账户为 `admin / admin`，请立即在页面顶部修改为强密码。

## Docker 安装

适合没有 systemd 的环境（例如容器化宿主机或 Alpine）：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/tender-collection-platform/main/install-docker.sh | sudo bash -s -- https://github.com/dinggood615/tender-collection-platform.git
```

## 可视 Chrome 人工验证

原生脚本会安装 Chrome/Chromium、Xvfb、noVNC、Nginx 和安全代理。无需 SSH 隧道：在自定义站点卡片点击“打开此站验证”，平台会先把可视 Chrome 导航到该站点，再打开验证窗口。

```bash
ssh -L 6080:127.0.0.1:6080 <用户>@<服务器IP>
```

随后打开 `http://127.0.0.1:6080/vnc.html?autoconnect=1`，在可视浏览器中自行完成网站允许的人工操作。该浏览器的已验证会话可供动态站点连接器复用。

## 常用维护命令

```bash
sudo systemctl status tender-platform
sudo journalctl -u tender-platform -f
sudo systemctl restart tender-platform
```

运行仓库内的 `manage.sh` 可选择原生安装、Docker 安装或经二次确认后卸载：

```bash
sudo bash manage.sh
```

## 数据与安全

- 原生数据目录：`/opt/tender-collection-platform/data`；Docker 使用 `platform_data` volume。
- `.env` 权限为仅服务账户可读；不要将 SMTP 授权码、服务器密码或 `APP_SECRET` 提交到 Git。
- 建议限制管理后台到可信 IP、VPN 或反向代理认证，并配置有效 TLS 证书。
- 使用系统防火墙仅放行反向代理端口；noVNC 与 Chrome 调试端口不应直接暴露公网。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```
