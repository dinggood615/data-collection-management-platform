# 招标采集管理平台

基于 Scrapling 的中文招标公告采集与邮件日报平台。管理员通过网页选择已适配站点、维护关键词、设置发送时间与邮箱，并查看运行结果。

## 一键启动

```bash
git clone https://github.com/<你的账号>/tender-collection-platform.git
cd tender-collection-platform
cp .env.example .env
docker compose up -d --build
```

已发布到 GitHub 后，可选择下列任一安装方式：

### Docker（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/<你的账号>/tender-collection-platform/main/install-docker.sh | sudo bash -s -- https://github.com/<你的账号>/tender-collection-platform.git
```

### 原生 Linux / systemd

```bash
curl -fsSL https://raw.githubusercontent.com/<你的账号>/tender-collection-platform/main/install-linux.sh | sudo bash -s -- https://github.com/<你的账号>/tender-collection-platform.git
```

打开 `http://服务器IP:8000`，使用 `.env` 中的管理员账户 `admin` 和 `ADMIN_PASSWORD` 登录。部署前必须替换示例密码；SMTP 参数也仅保存在 `.env`。

## 已适配站点

- 华润守正招标公告：服务
- 华润守正采购公告：服务
- 浙江能源招标项目公告

华润网站以已人工验证的持久 Chrome/CDP 会话采集；若验证失效，平台会报告错误，不会绕过验证码、访问控制或封禁。新站点需要编写一个连接器后才会显示在后台。

## 数据与安全

- SQLite 数据保存在 Docker volume `platform_data`。
- SMTP 授权码只能写入 `.env` 或后台受保护设置，绝不提交到 Git。
- 建议用 Nginx/Caddy 配置 HTTPS，并限制管理后台仅允许可信 IP 或 VPN 访问。
- 定时采集默认每天北京时间 08:00，采集前一天数据并按标题去重。

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
uvicorn app.main:app --reload
```
