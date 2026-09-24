# Oracle ARM64 上的 amd64 模拟部署（实验）

适配基线：`v1.3.47` / `081289cb3e04b60949b10babecde61d0b869b268`。
本分支使用已发布的 amd64 控制面和槽位镜像，在 Linux ARM64 上通过 QEMU 执行。
Docker CLI 使用静态 ARM64 版本，其余应用二进制保持上游版本。

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
| 控制面 | `ghcr.io/dofastted/vm2api:v1.3.47@sha256:8783c24ec9aa79647f4e6c73b5e6ecacde95993adcc299867a69dcaea28fd44f` |
| QEMU 10.2.3 | `tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0` |
| 原生 Docker CLI | `docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c` |
| 本次 Ubuntu guest | `ghcr.io/dofastted/kin-os-ubuntu@sha256:d2c63cd5a7e2cb95d40b0b32ef4c60be578c909e10b0ee56df7ab269fb94e01e` |

准备脚本从固定镜像复制静态程序，不执行镜像中的 privileged 安装脚本。
它只替换 `qemu-x86_64` 注册项，安装到 `/usr/local/libexec/vm2api/qemu-x86_64`，
并以 `/etc/binfmt.d/qemu-x86_64.conf` 在开机时恢复；若发现不同的已有文件会拒绝覆盖。
若系统安装了 binfmt-support，会禁用它管理的旧 x86_64 handler，避免开机覆盖。
当前机器曾安装发行版 `qemu-user-static` / `binfmt-support` 用于初次排查，后续新部署无需依赖其旧版 QEMU。

## 新部署

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

docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml up -d --no-build
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

管理员用户名为 `admin`。在服务器终端查看本实例密码：

```bash
cd /home/ubuntu/vm2api-arm-experiment-20260924
grep '^VM2API_ADMIN_PASSWORD=' .env
```

在管理台为 `vm-01` 导入自己的账号凭证，再用管理台测试聊天。
初次建议单账号、单请求验证，观测 CPU、内存、首字时间后再增加并发。
`/v1` API 与管理台使用相同转发端口；API key 在本实例 `.env` 中。
上游 VM 详情中的 `runtime.ip` 有硬编码回退值，不代表实际监听或出口 IP；访问以此处端口转发为准。

## 停止与恢复

Compose 只管理控制面；槽位由控制面创建，需要先单独停止：

```bash
python3 deploy/probe-emulation.py stop
docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml stop
```

恢复：

```bash
docker compose -f docker-compose.yml -f docker-compose.amd64-emulation.yml up -d --no-build
python3 deploy/probe-emulation.py start
```

以上操作保留账号、SQLite 和槽位数据。无需停止其他容器或重启 Docker。
若以后不再使用 QEMU，先确认没有其他 amd64 容器依赖它，再移除本实验的 binfmt override/解释器；
若需要恢复发行版 handler，使用 `update-binfmts --enable qemu-x86_64`。
不要执行全局 `docker system prune` 来清理这次实验。
