# OpenClash Region Filter 项目说明

## 背景

这个项目用于解决 OpenClash 订阅自动更新后的节点地区过滤问题。目标是在不使用第三方订阅转换服务、不外传订阅地址的前提下，在本地读取 OpenClash 已生成的 YAML 配置，排除用户禁用的地区节点，并自动重启 OpenClash 生效。

当前默认策略是启用新加坡、美国、日本、韩国、印尼和未知地区节点，排除香港和大陆节点。实际订阅里如果暂时没有某个地区节点，面板会显示为 0，后续订阅更新出现后会按对应开关自动处理。

## 功能

- 提供 Web 面板，默认监听 `0.0.0.0:8088`。
- 扫描 OpenClash 配置文件中的节点，按地区显示节点数量和节点名。
- 支持在面板中通过地区开关启用或禁用节点地区。
- 自动监听配置文件变更，适配 OpenClash 的“自动更新配置文件”。
- 过滤 inline `proxies`，并同步重写 `proxy-groups` 中的节点引用。
- 写回前自动备份原始配置到 `.region-filter-backups/`。
- 写回后可执行 OpenClash 重载命令。
- 可选使用 OpenClash 运行态验证 API 检查运行中的节点列表。

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

## 运行设置

默认配置文件路径：

```text
/etc/openclash/config/猎户座.yaml
```

默认重载命令：

```sh
nsenter -t 1 -m -u -i -n -p -- /etc/init.d/openclash restart
```

运行态验证 API 默认是 `http://127.0.0.1:9090`，用于服务容器向 OpenClash/Mihomo 查询当前运行中的节点列表。它不是日常必须操作的页面；只有开启“应用后通过控制面板验证”并且 OpenClash 设置了外部控制密钥时，才需要填写验证密钥。未填写密钥时可能返回 `401 Unauthorized`，这不会影响文件过滤本身。

## 已验证状态

本次部署后，面板可通过以下地址访问：

```text
http://192.168.2.1:8088
```

当时扫描到的节点分布：

- 美国：6 个
- 日本：5 个
- 新加坡：4 个
- 韩国：0 个
- 印尼：0 个
- 香港：0 个

执行一次立即过滤后，配置中未发现香港或大陆节点，当前配置已经符合默认规则。

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

## 建议事项

- 后续开发都基于本目录：`/Users/80399017/Workplace/releases/openclash-region-filter`。
- 不要把真实订阅 YAML、OpenClash 配置、Dashboard secret 或订阅 URL 提交进 Git。
- 优先通过 `http://路由器IP:8088` 面板或 API 操作，不再依赖网页终端做长时间部署。
- 如果要继续增强，建议优先做三件事：面板认证、Dashboard secret 的安全保存、支持 `proxy-providers` provider 文件过滤。
