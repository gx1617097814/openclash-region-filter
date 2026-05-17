# OpenClash Region Filter

本地 OpenClash 配置过滤器：在 OpenClash 自动更新订阅后，自动读取本机 YAML 配置，排除你禁用的地区节点，并重启 OpenClash 生效。它不使用第三方订阅转换服务，订阅地址不会外传。

## 功能

- Web 面板显示当前配置里的所有地区和节点数量
- 通过地区开关启用或禁用节点地区
- 自动监听配置文件变化，适配 OpenClash 自动更新
- 原子写回配置文件，并在写回前自动备份
- 自动重启 OpenClash
- 可通过 OpenClash 运行态验证 API 检查运行中的节点列表

## 默认规则

默认启用：

- 新加坡
- 美国
- 日本
- 韩国
- 印尼
- 未知地区节点

默认排除：

- 香港
- 大陆

如果订阅里没有韩国或印尼节点，面板会显示对应地区为 0 个节点。

## Docker 部署

在 iStoreOS 上把本目录放到任意位置后运行：

```sh
docker compose up -d --build
```

然后访问：

```text
http://路由器IP:8088
```

首次启动会生成：

```text
./data/config.json
```

如果你的配置文件不是默认的 `/etc/openclash/config/猎户座.yaml`，在面板里改“配置文件路径”并保存。

默认重载命令是：

```sh
nsenter -t 1 -m -u -i -n -p -- /etc/init.d/openclash restart
```

这是给 Docker 部署准备的：容器会使用宿主机 PID namespace 和 privileged 权限进入宿主机命名空间重启 OpenClash。

## 快速部署到 iStoreOS

首次配置免密 SSH：

```sh
ROUTER_HOST=192.168.2.1 scripts/install-router-ssh-key.sh
```

之后每次更新版本：

```sh
ROUTER_HOST=192.168.2.1 scripts/deploy-to-istoreos.sh
```

部署脚本默认会在 LuCI 左侧“服务”菜单下安装 `OpenClash 地区过滤` 入口，指向本服务的 8088 面板。如果不想安装菜单入口：

```sh
INSTALL_LUCI_MENU=0 ROUTER_HOST=192.168.2.1 scripts/deploy-to-istoreos.sh
```

## 推荐 OpenClash 设置

OpenClash 里可以重新打开“自动更新配置文件”。本服务会在配置文件更新时间变化后自动过滤并重启 OpenClash。

“运行态验证 API”默认是 `http://127.0.0.1:9090`，用于服务容器向 OpenClash/Mihomo 查询当前运行中的节点列表。它不是日常必须操作的页面；只有开启“应用后通过控制面板验证”并且 OpenClash 设置了外部控制密钥时，才需要填写验证密钥。

如果你打开运行态验证，请在面板里填写：

```text
运行态验证 API: http://127.0.0.1:9090
验证密钥: OpenClash 外部控制密钥
```

## 一次性执行

也可以不启动面板，直接过滤一次：

```sh
python -m app.service --config ./data/config.json --once
```

## 备份

每次写回前会把原文件备份到配置文件所在目录的：

```text
.region-filter-backups/
```

## 注意

- 当前版本过滤 inline `proxies` 和 `proxy-groups`。如果配置使用 `proxy-providers`，会给出警告，但不会改写 provider 文件。
- 面板没有做登录认证，建议只在内网访问，或者由 iStore/反代层加认证。
