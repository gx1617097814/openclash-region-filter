# OpenClash Region Filter

本地 OpenClash 配置过滤器：直接读取你填写的 Clash/OpenClash YAML 订阅，排除禁用地区的节点，生成本地配置并自动交给 OpenClash 使用。它不使用第三方订阅转换服务，订阅地址不会外传。

## 功能

- 支持多个订阅档案，同一时间只激活一个
- 每个订阅独立保存名称、地址、刷新频率、额度、地区规则和测速记录
- Web 面板显示当前激活订阅实际出现的地区和节点
- 通过地区开关启用或禁用节点地区
- 订阅出现新地区时自动生成地区卡片，默认启用
- 自动定时刷新远程订阅，配置变化时自动更新并重载 OpenClash
- 当前订阅支持一键全局延迟测试，默认每 4 小时自动测试
- 可参考延迟手动选择节点，测速不会自动切换线路
- 原子写回配置文件，并在写回前自动备份
- 自动重启 OpenClash
- 可通过 OpenClash 运行态验证 API 检查运行中的节点列表
- 可配置远程 Clash/OpenClash YAML 订阅，过滤后自动生成 OpenClash 本地配置

## 默认规则

默认启用：

- 新加坡
- 美国
- 日本
- 未知地区节点

默认排除：

- 香港
- 大陆

面板只展示当前激活订阅中实际存在的地区，不显示 0 节点地区。带旗帜的节点会自动按旗帜归类，`TW-`、台湾、台灣等名称也会识别为台湾；无法识别地区的节点会归入“其他地区”。新出现的动态地区默认启用，但香港和大陆会继续按排除规则处理。每个订阅独立保存地区状态，地区暂时消失后再次出现时仍沿用之前的设置。

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

工具会自动维护 `/etc/openclash/config/openclash-region-filter.yaml`，无需在页面选择配置文件或填写输出路径。

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

## OpenClash 接入

工具会自动生成并维护 OpenClash 本地配置。订阅卡片的“刷新”更新该订阅；当前订阅刷新后自动过滤并应用，待机订阅只更新私有缓存。地区区的“保存并应用”只保存地区开关并执行必要的应用流程。

工具会在后台通过 `http://127.0.0.1:9090` 自动验证 OpenClash/Mihomo 当前运行节点。验证参数不在日常页面展示；工具会优先使用已保存配置，并尝试从 OpenClash 运行配置中自动读取验证信息。

## 订阅代理模式

如果远程订阅本身已经是 Clash/OpenClash YAML，可在面板添加一个或多个订阅档案。激活待机订阅时，工具会自动完成：

- 拉取远程 YAML。
- 识别并展示订阅中实际出现的地区。
- 按地区开关过滤节点。
- 写入 `/etc/openclash/config/openclash-region-filter.yaml`。
- 配置发生变化时自动重载 OpenClash。
- 自动验证运行节点。

切换会先在独立缓存完成下载、过滤和验证，再原子替换稳定输出；配置没有变化时不会重载。切换验证失败会恢复之前的配置。之后每个订阅按各自频率更新，远程订阅临时失败时不会删除最后一次成功生成的配置。

兼容用途的 `/subscription.yaml` 端点仍然保留，但当前推荐方式是让 OpenClash 使用工具自动生成的本地配置文件。

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
- 面板上方展示订阅卡片，下方展示当前订阅的地区、节点延迟、当前节点和 JSON 结果。订阅可独立编辑、刷新或激活；地区开关统一由“保存并应用”生效。
