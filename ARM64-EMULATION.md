# Oracle ARM64 原生控制面 + amd64 槽位（实验）

初始适配基线：`v1.3.47` / `081289cb3e04b60949b10babecde61d0b869b268`。
当前应用基线：上游 `v1.3.89` / `33d0582cb336871d756219e321e867c75f941462`（2026-10-01）。
控制面使用本地构建的 `vm2api-arm64-control:v1.3.89`：Node、Python、iptables、Docker CLI、
`kin-egress` 和 `kin-worker` 原生运行在 ARM64；上游未提供 ARM64 版本的槽位 CLI、Rust kernel
和 OAuth helper 继续通过 QEMU 执行。本方案不是全栈原生 ARM，也未做推理性能基准测试。

`deploy/Dockerfile.arm64-control` 从当前 checkout 安装 Node 依赖、复制应用源码和已提交的 `web/dist`，
从 `worker/` 编译原生 Go helpers，并使用 checkout 中的 amd64 kernel、OAuth 和 wrap-cli 资产。
固定上游镜像只提供剩余 amd64 动态程序所需的库和加载器；构建会校验它与 checkout 的 VERSION。
应用、网页和本分支的 `src/lib/vm/egress.mjs` 补丁直接进入镜像，不依赖同版本发布镜像中的旧应用。
原生 helpers 放入 entrypoint 使用的 `image-bin`，确保重启不会被 amd64 版本覆盖。

## 实测结论（2026-09-24）

环境：Oracle Ampere ARM64、4 CPU、24 GB 级内存、Ubuntu 24.04.4、Docker 29.7.2、Compose 5.4.0。

- 控制面 `/health` 和管理台 `/console` 返回 HTTP 200。
- Ubuntu 24.04 amd64 槽容器启动成功，Rust kernel 和 Claude CLI 完成初始化。
- 槽位健康接口：`reachable=true`、`process_up=true`、HTTP 200、`ready_slots=20`。
  这里的 20 是 CLI 内部就绪槽位数，**不是已测出的可用并发数**。
- Claude CLI `--version` 成功：`2.8.4 (Claude Code)`；模拟环境冷启动超过 20 秒。
- 空载一次采样：控制面约 152 MiB、单槽约 386 MiB；两者均无 OOM 或自动重启。
  这不是请求峰值或压力测试，不能据此保证 1 GB VPS 足够。
- 尚未导入账号：槽位保持 `schedulable=false / no_credential`。
  **真实模型请求、TTFT、吞吐、长时间稳定性仍待验证。**
- 验证范围是 `px-local` 直连出口、Rust/Claude 单槽；其他 guest OS、SOCKS5 出口 helper、Go telemetry、Codex 槽未验证。
- 已安装开机 binfmt 配置；未为实验重启宿主机。
- 在新 handler 注册后，槽位停止/启动成功，约 25 秒后 CLI 再次就绪；
  随后重启控制面，原槽位仍然运行、健康接口恢复正常。

## 两个必要修复

1. Ubuntu 提供的 QEMU 8.2.2 执行 amd64 Node 时发生 QEMU 内部 SIGSEGV；固定为 10.2.3 后可运行。
2. 控制面内的 amd64 Docker CLI 在旧 Go runtime 的 `netpoll_epoll` 中崩溃；
   `GODEBUG=asyncpreemptoff=1` 无效。改用官方 Docker 镜像中静态编译的 ARM64 CLI 后，Docker 管理命令正常。
   静态 ARM64 可执行文件可以在 amd64 容器文件系统中原生运行，不依赖容器中的 ARM 动态链接器。

`DOCKER_DEFAULT_PLATFORM=linux/amd64` 会被控制面的 `execFileSync` 继承，覆盖槽镜像 pull/build/run。
因此当前版本无需修改槽位业务代码；不能仅设置 Compose 的 `platform` 而遗漏动态创建的槽位。
本 override 保留上游 Docker socket/host network 设计，运行时数据迁到 `.local/amd64-emulation/`。
控制面不设容器内存上限；槽位通过 `KIN_VM_MEMORY: "0"` 使用 Docker 的无限制设置，覆盖旧 `.env` 中的 2g 值。
已有槽容器需要停止并重建才能解除旧限制（保留绑定挂载的槽位数据）；`docker update --memory 0` 不会清除已有上限。控制面由 Compose 重建应用配置。

## 固定依赖

| 用途 | 镜像 / 摘要 |
| --- | --- |
| amd64 动态库来源 | `ghcr.io/dofastted/vm2api:v1.3.89@sha256:05dc3fbdc1263e3069e9ad09bef02ac827a191fb33a9cc1aed04580cca37517d` |
| 原生 Node 22 | `node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c` |
| 原生 Go builder | `golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437` |
| QEMU 10.2.3 | `tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0` |
| 原生 Docker CLI | `docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c` |
| 本次 Ubuntu guest | `ghcr.io/dofastted/kin-os-ubuntu@sha256:d2c63cd5a7e2cb95d40b0b32ef4c60be578c909e10b0ee56df7ab269fb94e01e` |

准备脚本从固定镜像复制静态程序，不执行镜像中的 privileged 安装脚本。
它只替换 `qemu-x86_64` 注册项，安装到 `/usr/local/libexec/vm2api/qemu-x86_64`，
并以 `/etc/binfmt.d/qemu-x86_64.conf` 在开机时恢复；若发现不同的已有文件会拒绝覆盖。
若系统安装了 binfmt-support，会禁用它管理的旧 x86_64 handler，避免开机覆盖。
当前机器曾安装发行版 `qemu-user-static` / `binfmt-support` 用于初次排查，后续新部署无需依赖其旧版 QEMU。

## 新部署

### 本机 1.3.89 升级记录（2026-10-01）

- 上游最新提交 `33d0582cb336871d756219e321e867c75f941462`。上游历史与旧 v1.3.75 分支分叉，
  本次以完整最新上游树为代码基线，移植原有 8 个 ARM 部署文件及 egress 源码/测试补丁，
  最终以双亲 merge 保留旧分支历史；升级前分支为 `backup/pre-v1.3.89-20261001`。
- 原生镜像改为直接使用合并后的源码和已提交网页资产，并编译最新 Go worker/egress。
  新增的 Node 依赖（ssh2、ws）在 ARM64 Node 22 环境安装。
- Oracle INPUT 放行继续限定到代理网桥、源/目标子网和 helper 端口，并保留上游 NAT 规则的顺序。
- 集群远程 iptables 生成测试覆盖新 INPUT 检查/插入配对。完整 Node 单元测试：
  2038 项，2028 通过、10 项按上游条件跳过、0 失败。
- 部署备份及最终验证结果在升级完成时记录于本节。

### 本机 1.3.75 升级记录（2026-09-28）

- 合入上游 v1.3.75；本次上游变化集中在代理池管理页与前端格式化工具，未修改 ARM helper、
  槽位运行、egress 或 iptables 代码。原分支备份为 `backup/pre-v1.3.75-20260928`。
- 上游仍只发布 amd64 控制面镜像，因此继续由固定上游镜像提供应用与网页产物，
  由本分支构建 ARM64 Node 运行环境和原生 Go helpers。
- 停止槽位和控制面后备份运行状态：
  `.local/backups/pre-v1.3.75-20260928/state.tar.gz`，199070458 bytes，目录 0700、文件 0600，
  SHA-256 `608257db1496c7afcbc86b50791adda1878f86061a8d15934dd5957981beb6ce`。
  备份中的 Compose 已指向 v1.3.75；回退镜像配置须从备份 Git 分支取回，并恢复匹配的运行状态。
- `vm2api-arm64-control:v1.3.75` 构建成功；13 项 Node egress 测试以及 Go
  `internal/egress`、`internal/proxy` 测试通过。VERSION=1.3.75，Node `process.arch=arm64`，
  两个 Go helper 均为静态 ARM aarch64。
- 槽位冷启动后 Rust 健康 HTTP 200、`ready_slots=20`；槽内 DNS/HTTPS 出口正常。
  本地 `/health`、本地与公网 `/console` 均返回 HTTP 200。管理员 `KySheep` 登录返回 HTTP 200。
- SQLite `quick_check=ok`；users=1、accounts=1、vms=1、api_keys=0、proxies=3，与升级前一致。
  空载一次采样：控制面约 54 MiB、槽位约 385 MiB，两个容器 restart count 均为 0。
- 槽位仍为 `no_credential`；本次未验证真实模型请求、TTFT、吞吐或并发能力。

### 本机 1.3.74 / 原生 ARM64 控制面升级记录（2026-09-28）

- 合入上游 v1.3.74；原分支备份为 `backup/pre-v1.3.74-20260928`。
- 停止控制面与槽位后备份 `.env`、Compose 和运行数据：
  `.local/backups/pre-v1.3.74-20260928/state.tar.gz`，目录 0700、文件 0600，
  SHA-256 `17274fe305598409f7fbb25135a902509eb5b0a9d393c78f96b097c68db831bd`。
  此快照的 Compose 已改为新镜像；回退镜像配置需从备份 Git 分支取回，同时恢复匹配的数据库和运行文件。
- 原 amd64 Go egress 在 QEMU 中出现 `taggedPointerPack` / netpoll 崩溃，amd64 iptables 出现
  `Failed to initialize nft: Protocol not supported`。改为原生控制面后，Go helper 与 iptables 正常运行。
- Oracle 宿主 INPUT 默认 REJECT 会阻断 REDIRECT 到本机的代理 TCP/DNS。程序现按代理的网桥、
  源/目标子网和 helper 端口插入 ACCEPT，先检查避免重复，并在删除代理时对称清理。
  保留原有 FORWARD DROP，不放开全局端口，也不修改其他防火墙规则。
- 验证：Node `process.arch=arm64`，两个 Go helper 为静态 ARM aarch64；13 项 Node egress 测试、
  Go `internal/egress` 和 `internal/proxy` 测试通过。槽内 DNS 与经既有 SOCKS 出口的 HTTPS 成功，
  本地 `/health` 和公网管理台 HTTP 200；Rust 健康 HTTP 200、CLI 内部 `ready_slots=20`。
  控制面重启后原生二进制与 DNS/HTTPS 仍正常，槽容器保持运行，未自动重启。
- SQLite `quick_check=ok`，users/accounts/vms/proxies 数量与备份一致。管理员 ID 保持不变。
  升级前后均保留一条账号记录，但该行 credentials 为空对象，槽内无实际 credentials.json；
  当前 `no_credential`，真实模型调用、TTFT、吞吐与并发上限尚未验证。
- 一次空载采样：控制面约 52 MiB，槽位约 391 MiB。这不是性能对照实验或容量承诺。

### 本机 1.3.65 升级记录（2026-09-27）

- 合并 origin/main 后，控制面镜像固定为 `v1.3.65@sha256:625cd9b0c04666bae727ae9e0d9a7fed4a5c2b146c59f8c1b5b6e2b2d1cae0fc`；保留 ARM64 Docker CLI、QEMU、槽容器与数据挂载。
- 停止控制面后备份 `.env`、Compose 和 `.local/amd64-emulation`；备份 `.local/backups/pre-v1.3.65-20260927/state.tar.gz`，SHA-256 `af6f4a5d35cb5bb304f6fc5eb155eb6b06fbb725e531db493c17570eb24f5b30`，目录 0700、文件 0600。
- 控制面 VERSION=1.3.65，`/health` 和 `/console` 均 HTTP 200，SQLite `quick_check=ok`。`vm-01` 的 Rust 健康 HTTP 200、`ready_slots=20`、`schedulable=true`。
- 同步过程已复制新版 `cli-node`、`cc-node`、`kin-kernel`，槽内哈希与模板一致。QEMU 冷启动超过同步接口的 30 秒等待，接口返回 HTTP 400 `health_timeout`，但随后槽位恢复健康；以后升级需以槽位最终健康状态核验。
- 未发起真实模型请求；当前检查不能证明真实推理性能或账号状态。

### 本机 1.3.52 升级记录（2026-09-25）

- 合并上游 `5b4af18d6968a898d9a87bb48991d6fee718f16b`，当时控制面固定到 v1.3.52 镜像。
- 本版上游未修改 CLI 二进制，保留已同步的槽位文件，不额外重启槽位。
- 备份：`.local/backups/pre-v1.3.52-20260925/state.tar.gz`，目录 0700、文件 0600；停止控制面后备份数据库和运行时文件，槽位仍运行。
- 备份 SHA-256：`df65a1011c65096b7e1391c449fdd1879e9ebd2200c7d23a35c771bf795be2b2`。
- 保留 ARM64 Docker CLI、QEMU 和不限制内存的配置。
- Tailscale Serve 管理台：`http://oracle-sg.tail056706.ts.net:8787/console`，仅 tailnet 可访问；后端仍监听 `127.0.0.1:8787`。

### 本机 1.3.51 升级记录（2026-09-25）

- 合并上游 `ee4cfc73af8354e8eb4e04869245f9ba81b1a8b2`，并将控制面升级到当时的 v1.3.51 镜像。
- 停止控制面后备份完整 `.local/amd64-emulation`、`.env` 和 Compose；槽位当时仍运行，故槽位日志不是停机快照。
- 备份：`.local/backups/pre-v1.3.51-20260925/state.tar.gz`，目录 0700、文件 0600。
- 备份 SHA-256：`b3ed2d0399468da171b42ba3741d4e9587da82f02ca3325f8960f751ac740ef2`。
- 自动 CLI 同步成功 `1/1, failed=0`，槽容器已重启，Rust 健康 HTTP 200、CLI `ready_slots=20`。
- 源码、镜像、运行时 share 和 vm-01 的 `cli-node` SHA-256 一致：`2539083cb26ac7915fbb6dd417c4e854b35d705e4799e9ab86f212ae514cddb1`。
- 镜像中的 `cc-node`、entrypoint 和 unit-circuit 源码摘要与此次上游一致。
- 管理台 HTTP 200、SQLite `quick_check=ok`、`sticky_sessions.generation` 已迁移，原生 Docker CLI 可用。
- 控制面和槽位 `memory.max=max`，其他服务未重启。当前仍为 `no_credential`，真实模型请求未验证。
- 回退需同时恢复旧镜像、数据库和运行时文件；此备份包含敏感数据，不提交仓库。

### 本机 1.3.48 升级记录

- 先停止控制面，备份 SQLite、配置、`.env` 和升级前 Compose；槽位继续运行。
- 备份位于安装目录下 `.local/backups/pre-v1.3.48-20260924/state.tar.gz`，目录权限 0700、文件权限 0600。
- 备份 SHA-256：`fb47370da7bac05aeb474bdaf0eae84159c0e614a26876a3bbfd271ac1a28e21`。
- 核对新旧镜像的 Rust kernel、Go worker、egress、kernel wrapper 摘要一致后，只重建控制面。
- 升级后容器版本为 1.3.48；`sticky_sessions.device_id` 已迁移，SQLite `quick_check` 返回 `ok`。
- 管理台 HTTP 200，原槽位保持同一启动时间，Rust/CLI 健康 HTTP 200；两容器 `memory.max` 均为 `max`。
- 本次仍未导入账号，因此未验证真实模型调用。
- 如需回退，应先停止控制面，并同时恢复旧镜像配置和升级前数据库；备份包含敏感信息，不要上传 GitHub。

### 安装步骤

前提：Ubuntu ARM64、Docker Engine、Compose、Python 3、`file`、systemd-binfmt，
当前用户可使用 Docker 和 `sudo`。端口 8787、容器名 `vm2api-arm-experiment`、
槽位名 `kin-01` 及网络名 `kin-eg-px-local` 应空闲。本方案在同一 Docker daemon 上只部署一套实例。

```bash
git clone --branch feat/arm64-qemu https://github.com/SmileYangzy/vm2api.git vm2api-arm
cd vm2api-arm
python3 deploy/prepare-emulation.py
python3 deploy/init-emulation-env.py

# 固定本次实测的 guest 镜像，然后赋予上游需要的本地标签。
docker pull --platform linux/amd64 ghcr.io/dofastted/kin-os-ubuntu@sha256:d2c63cd5a7e2cb95d40b0b32ef4c60be578c909e10b0ee56df7ab269fb94e01e
docker tag ghcr.io/dofastted/kin-os-ubuntu@sha256:d2c63cd5a7e2cb95d40b0b32ef4c60be578c909e10b0ee56df7ab269fb94e01e ghcr.io/dofastted/kin-os-ubuntu:24.04

docker compose --progress plain -f docker-compose.yml -f docker-compose.amd64-emulation.yml -f docker-compose.arm64-native.yml build
docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml -f docker-compose.arm64-native.yml up -d --no-build
python3 deploy/probe-emulation.py start
# 冷启动需等待，之后检查 kernel_detail.rust_health。
python3 deploy/probe-emulation.py status
```

`.env` 由脚本以 0600 权限创建，随机生成管理员密码、API key、DB secret；已有文件不会覆盖。
不要在这条部署路线执行上游一键更新脚本，也不要改为 `latest`，否则会离开实测版本。
后续升级需要重新验证 QEMU、Docker CLI 和槽位启动兼容性。

## 当前实验实例与访问

安装目录：`/home/ubuntu/vm2api-arm-experiment-20260924`。
只监听远端 `127.0.0.1:8787`；通过 Netcatty 本地端口转发访问：

- SSH 主机：当前 Oracle ARM 主机。
- 本地监听：`127.0.0.1:18787`。
- 远端目标：`127.0.0.1:8787`。
- 浏览器：`http://127.0.0.1:18787/console`。

首次初始化默认管理员用户名为 `admin`；现有部署的登录身份以数据库为准。
`.env` 用于初始化或 fallback，单独修改它不会更新已有管理员的密码 hash。
在服务器终端查看私有初始化配置：

```bash
cd /home/ubuntu/vm2api-arm-experiment-20260924
grep '^VM2API_ADMIN_' .env
```

在管理台为 `vm-01` 导入自己的账号凭证，再用管理台测试聊天。
初次建议单账号、单请求验证，观测 CPU、内存、首字时间后再增加并发。
`/v1` API 与管理台使用相同转发端口；API key 在本实例 `.env` 中。
上游 VM 详情中的 `runtime.ip` 有硬编码回退值，不代表实际监听或出口 IP；访问以此处端口转发为准。

## 停止与恢复

Compose 只管理控制面；槽位由控制面创建，需要先单独停止：

```bash
python3 deploy/probe-emulation.py stop
docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml -f docker-compose.arm64-native.yml stop
```

恢复：

```bash
docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml -f docker-compose.arm64-native.yml up -d --no-build
python3 deploy/probe-emulation.py start
```

以上操作保留账号、SQLite 和槽位数据。无需停止其他容器或重启 Docker。
若以后不再使用 QEMU，先确认没有其他 amd64 容器依赖它，再移除本实验的 binfmt override/解释器；
若需要恢复发行版 handler，使用 `update-binfmts --enable qemu-x86_64`。
不要执行全局 `docker system prune` 来清理这次实验。
