# OpenClash Region Filter 项目说明

## 背景

这个项目用于解决 OpenClash 订阅自动更新后的节点地区过滤问题。目标是在不使用第三方订阅转换服务、不外传订阅地址的前提下，在本地读取 OpenClash 已生成的 YAML 配置，排除用户禁用的地区节点，并自动重启 OpenClash 生效。

当前默认策略是启用新加坡、美国、日本、台湾和未知地区节点，排除香港和大陆节点。面板只展示当前激活订阅里实际出现的地区，0 节点地区不占用位置；每个订阅独立保留地区状态。

动态地区规则：带国家/地区旗帜的未知节点会自动按旗帜生成地区，例如台湾、英国、德国等；没有可识别旗帜的未知节点会归到“其他地区”。动态地区默认启用，用户可以在面板中关闭。

## 功能

- 提供 Web 面板，默认监听 `0.0.0.0:8088`。
- 扫描 OpenClash 配置文件中的节点，按地区显示节点数量和节点名。
- 支持在面板中通过地区开关启用或禁用节点地区。
- 只展示订阅中实际出现的地区，无法识别地区的节点归入“其他地区”。
- 订阅出现新地区时自动生成地区卡片，默认启用。
- 自动定时拉取原始订阅、过滤并维护 OpenClash 本地配置。
- 过滤 inline `proxies`，并同步重写 `proxy-groups` 中的节点引用。
- 写回前自动备份原始配置到 `.region-filter-backups/`。
- 配置变化时自动重载 OpenClash。
- 自动使用 OpenClash 运行态 API 检查运行中的节点列表。
- 多订阅独立缓存和单激活配置，候选订阅验证成功后才切换。
- 通过 Mihomo 控制 API 测试节点延迟并手动选择当前节点。
- 每个订阅持久保存最近结果、刷新/测速时间和上次手动节点；页面刷新与容器重启不会清空。
- 日常测速不触发自动换线；切换时优先恢复上次选择的可连接节点，没有历史节点时选择本次实测延迟最低的节点。
- 拉取远程 Clash/OpenClash YAML，过滤后生成并安装 OpenClash 本地配置。

## 项目结构

```text
openclash-region-filter/
  app/                    # 服务端与过滤逻辑
  tests/                  # 单元测试
  wheels/                 # 离线 PyYAML wheel
  scripts/
    deploy-ocfilter.sh    # iStoreOS 离线/半离线部署脚本
    deploy-to-istoreos.sh # 本机一键打包、上传、部署
    install-luci-menu.sh  # 在 LuCI 服务菜单下安装入口
    package.sh            # 生成部署 tar 包
  artifacts/              # 本地构建产物目录，不纳入 Git
  Dockerfile
  docker-compose.yml
  README.md
  PROJECT.md
```

## Docker 部署方式

常规环境可以直接在项目根目录运行：

```sh
docker compose up -d --build
```

然后访问：

```text
http://路由器IP:8088
```

iStoreOS 上如果 Docker Hub 拉取不稳定，可以使用本项目的现场部署脚本。先在本机项目目录通过 HTTP 暴露 `openclash-region-filter.tar.gz`，并在路由器上提前准备 `alpine-local` 镜像，然后运行：

```sh
FILE_HOST=192.168.2.190 sh /tmp/deploy-ocfilter.sh
```

`FILE_HOST` 是提供 tar 包下载的内网主机地址，不带协议头时更适合 BusyBox `wget` 场景。脚本会创建并启动容器：

```text
openclash-region-filter
```

默认容器参数包括：

- `--network host`
- `--pid host`
- `--privileged`
- `-v /etc/openclash:/etc/openclash`
- `-v /overlay/upper/opt/openclash-region-filter-data:/data`

这些参数是为了让容器读取 OpenClash 配置，并通过 `nsenter` 重启宿主机上的 OpenClash。

## 推荐的快速部署方式

当前网络若能访问 LuCI/ttyd、但 SSH TCP 握手无法完成，可以使用 ttyd
WebSocket 命令客户端代替网页终端。创建不纳入 Git 的 `.router.env`，设置
权限为 `600`，然后执行：

```sh
node scripts/router-ttyd-exec.mjs --env .router.env -- "命令"
```

环境文件格式见 `.router.env.example`。客户端不会打印凭据或登录阶段内容，
因此适合后续自动状态检查、部署和回滚脚本调用。

后续版本更新建议走 SSH 一键部署，不再使用 LuCI/ttyd 网页终端。首次使用前，把本机 SSH key 安装到路由器：

```sh
ROUTER_HOST=192.168.2.1 scripts/install-router-ssh-key.sh
```

这一步会要求输入一次路由器 root 密码。之后每次更新完代码，只需要在本项目根目录运行：

安装脚本会同时写入普通 Linux 常用的 `~/.ssh/authorized_keys` 和 OpenWrt/iStoreOS Dropbear 常用的 `/etc/dropbear/authorized_keys`，避免不同 SSH 服务读取位置不一致。

```sh
ROUTER_HOST=192.168.2.1 scripts/deploy-to-istoreos.sh
```

这个命令会自动完成：

1. 打包当前项目到 `artifacts/openclash-region-filter.tar.gz`。
2. 通过 `scp` 上传 tar 包和部署脚本到 iStoreOS。
3. 通过 `ssh` 在路由器上重建 `openclash-region-filter:local` 镜像。
4. 重启 `openclash-region-filter` 容器。
5. 检查 `http://路由器IP:8088/api/state` 是否可访问。
6. 默认在 LuCI 左侧“服务”菜单下安装 `OpenClash 地区过滤` 入口。

如果路由器上已经存在 `openclash-region-filter:local` 镜像，部署脚本会复用该镜像作为构建基底，只替换应用代码，避免每次重新安装 Python 依赖。只有首次部署或镜像被删除时，才会回退到 `alpine-local` 并安装依赖。

如果不想安装 LuCI 菜单入口，可以执行：

```sh
INSTALL_LUCI_MENU=0 ROUTER_HOST=192.168.2.1 scripts/deploy-to-istoreos.sh
```

前提条件：

- 路由器 SSH 可用。
- 路由器 Docker 可用。
- 路由器已经有 `alpine-local` 镜像；本次现场部署已经创建过。
- 如果换新路由器或清空 Docker，需要先重新导入 Alpine rootfs，或改造成可拉取基础镜像的部署方式。

## 自动运行参数

工具自动生成并维护以下配置文件：

```text
/etc/openclash/config/openclash-region-filter.yaml
```

默认重载命令：

```sh
nsenter -t 1 -m -u -i -n -p -- /etc/init.d/openclash restart
```

运行态验证 API 默认是 `http://127.0.0.1:9090`。验证、重载、输出路径和轮询都由后台管理，不在日常页面暴露。工具会优先使用已有验证配置，并尝试从 OpenClash 运行配置中自动读取所需信息。

## 订阅代理模式

订阅本身已经是 Clash/OpenClash YAML 时，在面板添加订阅并刷新。工具会：

1. 拉取远程 YAML。
2. 扫描节点并补充新出现的动态地区。
3. 按当前地区启用/禁用状态过滤节点。
4. 写入 `/data/subscription-filtered.yaml` 缓存。
5. 自动安装为 `/etc/openclash/config/openclash-region-filter.yaml`。
6. 配置变化时自动重载 OpenClash并验证运行节点。

兼容用途仍可读取：

```text
http://192.168.2.1:8088/subscription.yaml
```

当前推荐方式是让 OpenClash 直接使用自动生成的本地配置文件。远程订阅失败时不会删除最后一次成功生成的 YAML。

## 面板布局规则

桌面端面板为两行左右布局：

1. 订阅设置 / 订阅额度
2. 地区规则 / 最近结果

页面上方使用卡片管理多个订阅，每张卡片包含名称、地址、刷新频率、最近刷新、额度、编辑、刷新和激活状态。下方只展示当前激活订阅的地区与节点；节点列表可展开查看延迟并选择待用节点。订阅“刷新”和待机订阅“激活”各自承担单一职责，地区与节点修改先保留为页面草稿，再通过一次“保存并应用”统一提交。最近结果保留可滚动的 JSON 明细，包含全部节点、保留/排除节点和地区汇总。

## 已验证状态

本次部署后，面板可通过以下地址访问：

```text
http://192.168.2.1:8088
```

当时扫描到的节点分布：

- 美国：6 个
- 日本：5 个
- 新加坡：4 个

当前面板只展示订阅中实际存在的地区；香港出现时默认保持禁用。

地区与节点统一应用后，工具会确认运行态中不存在被禁用地区节点，并确认待用节点已经生效。失败时会恢复之前的订阅缓存、输出配置和地区状态。

## 开发和测试

运行单元测试：

```sh
python3 -m unittest discover -s tests -v
```

重新打包源码供路由器下载：

```sh
scripts/package.sh
```

如果要给路由器下载，可以在项目根目录启动临时 HTTP 服务：

```sh
python3 -m http.server 80 --bind 0.0.0.0
```

## 这次踩过的坑

- iStoreOS 路由器宿主机没有 Python，不能直接在宿主机跑脚本，必须放进 Docker 或改用 OpenClash 原生规则。
- iStoreOS 的网页终端 ttyd 输入长命令很不稳定，粘贴不可用时逐字符输入非常慢。
- ttyd/输入法环境里冒号 `:` 容易被转换或输入异常，尽量避免需要 `http://host:port/...` 的命令。
- BusyBox `wget` 在本次环境里使用 `192.168.2.190/file` 比 `http://192.168.2.190:18089/file` 更稳定。
- Docker Hub 拉取不可靠时，可以本地下载 Alpine rootfs，再在路由器上 `docker import` 成 `alpine-local`。
- 构建容器如果中途停止，续跑脚本要先 `docker start`，不能假设容器存在就一定在运行。
- OpenClash 运行态验证 API 如果配置了 secret，未填写密钥时 `/proxies` 会返回 401。这不是过滤失败，而是验证权限不足。
- 过滤器面板当前没有登录认证，只建议在可信内网访问，或者后续加 LuCI 反代认证。
- 隐藏 0 节点地区时，保存设置不能丢掉后台排除规则。当前实现只更新页面上可见的开关，隐藏地区的既有启用/禁用规则会保留，例如大陆仍保持禁用。

## 建议事项

- 后续开发都基于本目录：`/Users/80399017/Workplace/releases/openclash-region-filter`。
- 不要把真实订阅 YAML、OpenClash 配置、Dashboard secret 或订阅 URL 提交进 Git。
- 优先通过 `http://路由器IP:8088` 面板或 API 操作，不再依赖网页终端做长时间部署。
- 如果要继续增强，建议优先做三件事：面板认证、Dashboard secret 的安全保存、支持 `proxy-providers` provider 文件过滤。
