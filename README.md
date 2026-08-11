# 数据采集管理平台

## 智能自定义站点适配

自定义站点现在按三层策略自动识别：Scrapling 静态 DOM 自适应选择器、可视 Chrome 渲染后的链接/卡片识别、同源公开 JSON 数据接口识别。对于 Vue、React 等单页应用，即使公告由无链接的卡片组成，平台也会从页面自身发出的公开 GET 请求中推断分页、标题、日期和类型字段，保存为可复用规则，后续无需反复人工确认。

安全边界：仅接受与列表页同域的公开 HTTP(S) GET JSON；不保存 Cookie、令牌或请求头，不绕过验证码、登录、访问控制或网站封禁。接口分页默认最多 30 页并带有请求间隔。

对于包含多个公开栏目的站点，适配规则可以保存多个数据源。采集时优先向网站公开接口传入目标起止日期，再根据接口返回的总条数逐页读取，并核对“应读取条数/实际读取条数”；完整性检查失败会明确告警，不会把部分结果静默当作完整结果。默认单栏目最多 30 页、每页 50 条，可通过 `API_PAGE_LIMIT` 调整安全上限。

准确性增强：平台优先使用站点稳定业务 ID 去重并跟踪公告修订；每日默认回查最近 3 天，补录延迟发布或后续修改的公告（可用 `RECHECK_DAYS=1..7` 调整）。匹配采用“核心词 + 业务对象/服务动作组合 + 排除词/产品型采购降权”，宽泛同义词不会再单独触发推送。对于无需登录且公开返回的详情接口，正文会参与评分；遇到 401、验证码或访问控制时会停止，不尝试绕过。

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
- 智能相关度：读取公告标题与详情正文，对软件开发、人力外包、信息化、数字化等核心词自动扩展常见同义表达，并按命中位置计算 0–100 分相关度。
- 可解释筛选：采集结果显示“高相关/可能相关”、分数和标题/正文命中原因；可配置排除词降低无关项目误报。
- 温和详情采集：每个站点单次最多读取 20 个目标日期详情页，默认间隔 0.6 秒；可使用 `DETAIL_FETCH_LIMIT` 和 `DETAIL_FETCH_DELAY` 调整。
- 外部采集器插件：服务器专用采集规则可放在数据目录的 `collector-plugins` 中，与 GitHub 程序代码分离；平台升级不会覆盖插件，也不会让 Git 工作区变脏。
- 专用站点自动接管：检测到专用采集器的站点会直接显示“已自动运行”，不再要求打开浏览器、重新识别或人工确认，也不会被通用采集器重复处理。
- SQLite 指纹去重：同一公告不会重复入库或重复邮件发送。
- 邮件日报：每天定时采集并汇总发送前一自然日的全部命中结果；可设置 SMTP、发件人、发送时间及最多 50 个收件邮箱。页面或助手发起的手动采集只入库，不重复发邮件。
- 企业微信助手：填写 CorpID、HTTPS 地址和管理员 UserID 后自动生成回调配置，可通过聊天查询状态、启动采集、查看结果或创建备份。
- 企业微信按需推送：不再随采集自动发送。管理员向企业微信助手发送“24”后，系统即时整理最近24小时入库数据；配置群机器人 Webhook 时发送到群，未配置时由助手直接回复。
- 健康检查：`/healthz` 返回服务和数据库就绪状态，原生安装完成后会自动校验。
- 自动备份：每日创建 SQLite 一致性备份，可在网页设置备份时间与保留天数。
- 一键迁移：网页导出完整迁移包，在新服务器安装后直接导入站点、关键词、通知配置、历史结果和管理员设置；导入前自动生成回滚备份。
- 一键恢复初始状态：操作前自动创建回滚备份，再清空全部业务数据和通知配置；保留管理员账号、TLS 证书、程序与备份文件。
- 企业级响应式界面：采用低噪声卡片、Bento 数据概览、清晰状态层级和键盘焦点反馈，适配手机、平板与桌面。
- VPS 一键安全更新：自动备份数据库、执行 `git pull --ff-only`、更新依赖和数据库结构、重启服务并完成健康检查；失败时自动恢复更新前代码。
- 多平台 Docker 生命周期管理：同一脚本覆盖 Linux、群晖 DSM、飞牛 OS 与 OpenWrt 的安装、备份更新、状态检查和安全卸载，兼容 Compose v1/v2 与 amd64/arm64 宿主机。
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

## Docker：Linux、群晖、飞牛 OS 与 OpenWrt

Docker 脚本不依赖 `apt`、`systemd` 或 Git，自动兼容 Docker Compose v2（`docker compose`）与 v1（`docker-compose`）。要求设备已经安装并启动 Docker/Compose，同时具备 `curl`、`tar`、`sed` 和 `mktemp`。默认将数据库映射到宿主机目录，更新容器不会丢失数据。

### 通用 Docker 一键安装、更新和卸载

安装：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | sh -s -- install
```

更新（更新前自动尝试创建数据库备份）：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | sh -s -- update
```

卸载程序和默认数据目录，需要交互确认：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | sh -s -- uninstall
```

无人值守卸载使用 `uninstall --yes`。如果 `DATA_DIR` 位于安装目录之外，默认会保留；只有明确设置 `DELETE_DATA=1` 才会删除外部数据目录。脚本始终保留 Docker/Container Manager 本身。

### 群晖 DSM 7 Container Manager

通过 SSH 运行以下命令。默认路径是 `/volume1/docker/data-collection-management-platform`；如果套件和共享文件夹位于其他存储卷，请同时修改 `INSTALL_DIR` 与 `DATA_DIR`：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | \
  INSTALL_DIR=/volume1/docker/data-collection-management-platform \
  DATA_DIR=/volume1/docker/data-collection-management-platform/data \
  PLATFORM_PORT=8000 sh -s -- install
```

也可以在 Container Manager 的“项目”中选择该安装目录并导入 `docker-compose.yml`。请避免使用已被 DSM 其他服务占用的端口。

### 飞牛 OS（fnOS）

在飞牛终端中，将路径换成存储空间里专门创建的 Docker 项目目录：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | \
  INSTALL_DIR=/你的存储路径/docker/data-collection-management-platform \
  DATA_DIR=/你的存储路径/docker/data-collection-management-platform/data \
  PLATFORM_PORT=8000 sh -s -- install
```

脚本不会修改飞牛 Docker 服务，也不要求 systemd；安装后可以继续在飞牛 Docker 图形界面管理容器。

### OpenWrt Docker

OpenWrt 建议把 Docker Root Dir 和本平台数据放到 ext4 等 Linux 文件系统的外接存储，不建议使用内部闪存、FAT 或 NTFS。示例路径请按实际挂载点修改：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/install-docker.sh | \
  INSTALL_DIR=/mnt/external/docker/data-collection-management-platform \
  DATA_DIR=/mnt/external/docker/data-collection-management-platform/data \
  PLATFORM_PORT=8000 sh -s -- install
```

OpenWrt 可通过 `dockerd`、`docker`、`docker-compose` 或 LuCI 的 `luci-app-dockerman` 提供容器环境。由于本项目需要在设备上构建 Python 镜像，更适合 x86_64/aarch64、外接存储且内存充足的软路由；低内存路由器可能无法完成镜像构建。

### Docker 可配置参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `INSTALL_DIR` | Linux `/opt/...`；群晖 `/volume1/docker/...`；OpenWrt `/opt/docker/...` | 程序与 Compose 项目目录 |
| `DATA_DIR` | `$INSTALL_DIR/data` | SQLite、选择器与备份的持久化目录 |
| `PLATFORM_PORT` | `8000` | 管理页面宿主机端口 |
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `BRANCH` | `main` | 安装或更新的 GitHub 分支 |
| `GITHUB_TOKEN` | 空 | 私有仓库源码包访问令牌 |
| `DELETE_DATA` | `0` | 卸载时是否删除安装目录外的数据目录 |

Docker 版提供静态站点采集、数据库、邮件、企业微信、备份迁移和恢复功能；不会安装宿主机可视 Chrome/noVNC。需要人工浏览器验证的动态站点，建议使用原生 Linux 版本。

## 安装后的首次配置

1. 访问 `https://<服务器 IP>:5555`，使用管理员账户登录。
2. 在“智能筛选规则”添加核心关键词；系统会对已支持的业务主题扩展常见同义表达。可填写保洁外包、食堂服务等排除词以降低误报。
3. 在“邮件与定时”填写 SMTP 主机、端口、发件邮箱、授权码、收件邮箱及发送时间。多个收件邮箱可使用逗号、分号或换行分隔，保存时会自动校验和去重。系统在该时间采集并邮件发送前一自然日的全部结果；手动采集不会发邮件。
4. 在“自定义采集站点”填写网站名称和**公告列表页**网址，然后点击“自动识别并添加”。不要填写首页、项目详情页或搜索结果页。
5. 静态页面识别成功后，确认状态为“已适配（静态列表）”并启用。
6. 如果提示“待人工确认”，点击“打开此站验证”，在可视 Chrome 中完成网站允许的操作并停留在公告列表页，再返回平台点击“重新识别”。识别成功后状态为“已适配（动态浏览器）”。
7. 点击“立即采集”进行首次检查；确认邮件和结果正常后等待定时任务执行。

相关度规则优先考虑标题核心词，其次是正文核心词和同义词；排除词会扣减分数。60 分及以上显示为“高相关”，20–59 分显示为“可能相关”，低于 20 分不进入推送结果。公告详情读取有数量上限和请求间隔，不会尝试绕过验证码、登录限制或站点封禁。

### 服务器专用采集器

GitHub 发行版不内置任何现有站点。需要长期保留的服务器专用规则应安装到 `COLLECTOR_PLUGIN_DIR`（原生安装默认 `/data/collector-plugins`，可在 `.env` 修改）。每个可信的 Python 插件需提供 `collect(target_date, enabled_codes, keywords, exclusions)`，并返回 `(结果列表, 提示文本)`。插件拥有与平台进程相同的权限，只能安装经过审核的代码；数据库、SMTP 授权码或服务器凭据不得写入插件源码。

如果是从旧服务器迁移，请先在旧平台“备份与迁移”中下载完整备份；完成新服务器一键安装后，在同一区域上传该 ZIP 文件并确认恢复。导入完成后，旧站点、关键词、SMTP、企业微信配置、历史结果和管理员账户都会恢复，无需逐项重新填写。

## 备份、导入与迁移

- “下载完整备份”会生成可跨服务器使用的 ZIP 迁移包。
- 迁移包包含数据库和恢复通知服务所需的敏感配置，请像密码文件一样保管，不要上传到网盘公开链接或提交到 GitHub。
- 导入仅接受平台生成的迁移包，并会检查文件结构、版本、SQLite 完整性和 100 MB 大小上限。
- 导入前会自动创建当前数据库备份；导入成功后，页面会显示回滚备份文件名。
- 如果迁移包恢复了旧管理员密码，导入后请使用旧平台管理员账户重新登录。
- “一键恢复初始状态”会清空站点、关键词、结果、运行记录、邮件定时和企业微信配置。平台会先创建 SQLite 回滚备份，并保留当前管理员账号、备份策略、证书和已有备份文件。

## 企业微信助手

企业微信改为管理员按需推送，不会在每日采集后自动发送。页面仅需填写 **CorpID、平台 HTTPS 公网地址、管理员 UserID**，系统会自动生成并展示回调地址、Token 与 EncodingAESKey；群机器人 Webhook 仅用于收到助手指令后把最近24小时结果转发到群。

将这三项复制到企业微信管理后台“应用管理 → 自建应用 → 接收消息”并保存验证。验证成功后，可在企业微信应用中发送：`状态`、`立即采集`、`最新结果`、`备份`。

企业微信后台的首次回调验证必须由企业管理员完成；平台不会绕过该安全机制。回调地址使用 `https://你的域名/wecom/callback`，请确保域名具有有效 HTTPS 证书且平台反向代理已开放标准 HTTPS 入口。验证后向助手发送“24”：若已保存群机器人 Webhook，结果会推送到群；否则助手直接回复最近24小时结果。原“推送24小时”指令仍兼容。此操作不会重新采集网站，也不会触发邮件。

## 人工验证与动态采集

原生安装会把可视 Chrome 和 noVNC 安全地经平台反向代理提供。点击站点卡片的“打开此站验证”即可打开对应站点，不需要额外的 SSH 隧道。

人工验证仅在网站要求时进行一次。已适配的动态站点在后续采集期间会使用已保存的浏览器会话，在后台创建临时标签页采集并在任务结束后关闭该标签页。浏览器服务保留运行，以维持会话与后续人工验证入口。

### 中石油招标网

添加 `https://www.cnpcbidding.com/#/tenders` 后，平台会自动选用“中石油专用动态浏览器”。点击“打开此站验证”，在可视 Chrome 中手动完成网站允许的验证并停留在公告列表，然后返回平台点击“重新识别”。适配器读取页面已解密的 Vue 公告数据，并调用页面自身的翻页和详情操作，因此不依赖传统链接或表格选择器；识别成功后状态自动切换为“已适配（动态浏览器）”。定时采集复用该持久会话、按目标日期受控翻页；会话失效时只暂停该站点并提示重新验证。

专用适配器不会破解验证码、绕过访问控制或复刻网站的加密接口。

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

下列命令用于原生 Linux 安装，会停止平台服务，并删除平台程序、SQLite 数据、浏览器会话、平台 TLS 文件和专用 Nginx 配置；如检测到 Compose 项目也会先停止容器。不会卸载 Nginx、Docker 或其他系统服务。群晖、飞牛 OS、OpenWrt 等 Docker 环境优先使用前文的 `install-docker.sh uninstall`。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/data-collection-management-platform/main/uninstall-linux.sh | sudo bash -s -- --yes
```

## 数据与安全建议

- 原生安装的数据目录：`/opt/data-collection-management-platform/data`；Docker 使用 `DATA_DIR` 指定的宿主机绑定目录，默认为 `$INSTALL_DIR/data`。
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
