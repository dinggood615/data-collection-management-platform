# 招标采集管理平台

基于 Scrapling 的中文招标公告采集与邮件日报平台。管理员通过网页选择已适配站点、维护关键词、设置发送时间与邮箱，并查看运行结果。

## 一键启动

```bash
git clone https://github.com/dinggood615/tender-collection-platform.git
cd tender-collection-platform
cp .env.example .env
docker compose up -d --build
```

已发布到 GitHub 后，可选择下列任一安装方式：

### Docker（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/tender-collection-platform/main/install-docker.sh | sudo bash -s -- https://github.com/dinggood615/tender-collection-platform.git
```

### 原生 Linux / systemd

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/tender-collection-platform/main/install-linux.sh | sudo bash -s -- https://github.com/dinggood615/tender-collection-platform.git
```

打开 `http://服务器IP:8000`，使用 `.env` 中的管理员账户 `admin` 和 `ADMIN_PASSWORD` 登录。部署前必须替换示例密码。SMTP 主机、端口、发件邮箱、授权码和收件人均可在后台“邮件与定时”中填写；授权码使用 `APP_SECRET` 派生的密钥加密后保存，页面不会回显。

## 已适配站点

- 华润守正招标公告：服务
- 华润守正采购公告：服务
- 浙江能源招标项目公告

华润网站以已人工验证的持久 Chrome/CDP 会话采集；若验证失效，平台会报告错误，不会绕过验证码、访问控制或封禁。

## 智能站点适配

在“自定义采集站点”输入**公开公告列表页**，平台会只请求一次进行安全探测：

- 对比候选链接的标题长度、日期命中率与链接密度，选择最聚焦的列表选择器；
- 对可靠的静态列表，将选择器交给 Scrapling 的 `auto_save`/`adaptive` 机制记忆，在页面小幅改版后尝试重新定位；
- 页面明显依赖 JavaScript、登录或人工验证时标记“待人工确认”，提示通过可视 Chrome 完成人工操作后重新识别；
- 只允许公网 HTTP(S) 地址，拒绝内网地址，避免从管理页面发起 SSRF 请求。

识别过程不会使用验证码绕过、代理轮换或反爬规避功能。自动采集只会运行状态为“已适配（静态列表）”的自定义站点；站点改版后可点击“重新识别”。

## 数据与安全

- SQLite 数据保存在 Docker volume `platform_data`。
- SMTP 授权码可在后台填写并加密保存，也可由 `.env` 提供；绝不提交到 Git。
- 建议用 Nginx/Caddy 配置 HTTPS，并限制管理后台仅允许可信 IP 或 VPN 访问。
- 定时采集默认每天北京时间 08:00，采集前一天数据并按标题去重。

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
uvicorn app.main:app --reload
```
