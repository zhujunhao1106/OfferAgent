# OfferPilot 生产环境 Runbook（ECS + 镜像化 CI/CD）

本文记录 **2026-09-29** 这套生产环境是怎么搭起来的、当前长什么样、出问题怎么查。
所有基础设施标识（实例 ID、IP、接入点等）按实记录；**任何凭据值（AK/SK、API Key、
ACR 固定密码）都不在本文中出现**，它们只存在于 GitHub repository secrets 与 ECS 上
的 `.env`。

与 `deployment.md` 的分工：

| 文档 | 回答的问题 |
| --- | --- |
| `deployment.md` | 应用**运行时配置契约**：每个环境变量的含义、健康检查语义、数据与恢复 |
| 本文 | 这套**环境**怎么来的、CI/CD 链路怎么连、怎么访问、踩过哪些坑 |

`deployment.md` 的 `## Production (Alibaba Cloud ECS)` 一节只有一段指路，细节都在这里。

---

## 1. 目标拓扑与那条决定性约束

```text
浏览器 ──► Next.js Web/BFF :3000 ──► Python API/Harness :3001 ──► SQLite + Markdown 知识库
   (公网)        (容器内网 BACKEND_URL=http://api:3001)      └──► OpenAI 兼容 LLM / MiMo
```

整套设计只有一个出发点：**生产机是 2 vCPU / 2 GB，绝不能在它上面构建镜像。**
Next.js 生产构建峰值内存 1.5–2 GB，2 GB 的机器一构建就 OOM。这一条否掉了两种常见做法
（在 ECS 上 `docker compose build`、以及 appmanager 那种「上传源码 + ECS 构建」的模型），
只剩一条路：**镜像在 CI 的 runner 上构建 → 推到镜像仓库 → ECS 只负责拉取和运行。**

后面所有看起来"绕"的地方（为什么要有 ACR、为什么用云助手、为什么 ECS 上连 git 都没装），
都是这条约束的直接推论。

---

## 2. ECS 实例

| 项 | 值 |
| --- | --- |
| 实例 ID | `i-2zefdb9ur96jyr9ji6bg` |
| 地域 / 可用区 | `cn-beijing` / `cn-beijing-h` |
| 规格 | `ecs.e-c1m1.large`（2 vCPU / 2 GB） |
| 操作系统 | Alibaba Cloud Linux 3.2104 LTS 64 位（RHEL 系） |
| 公网带宽 | 3 Mbps 固定 |
| 公网 IP | `60.205.125.130` |
| 到期 | 2027-09-29（包年包月） |
| 安全组 | `sg-2ze4kjk5aonfl216rxsr` |
| 账号登录名 | `aliyun6330613892`（AccountId `1833432084390646`） |

**安全组规则**：原本只有 22 / 3389 / ICMP，为上线放通了 **TCP 3000**（Web 入口）。
3001（API）**不开**——它在 compose 里绑定 `127.0.0.1:3001`，只给同机 Web 容器用，见
`docker-compose.prod.yml:18`。这是刻意的：API 直连会绕过 BFF，只应存在于回环。

**宿主机准备**（`deploy/ecs-init.sh` 一次性执行）：安装 Docker Engine 26.1.3 +
compose 插件 v2.27.0（走阿里云镜像源，失败回退官方脚本）、**加 2 G swap**（2C2G 防 OOM）、
建 `/root/offerpilot/`、确认云助手 Agent（`aliyun.service`）在跑。ECS 上**故意不装
git 和 node** —— 这台机器不接触源码，只跑镜像，装了反而是攻击面。

当前资源占用基线：磁盘 40 G / 已用约 8 G；内存 1.8 GiB 总量 / 约 1.2 GiB 可用。

---

## 3. 镜像仓库（ACR 个人版）

| 项 | 值 |
| --- | --- |
| 版本 | 容器镜像服务 **个人版** |
| 接入点 | `crpi-aq7wi69rg06f57t5.cn-beijing.personal.cr.aliyuncs.com` |
| 命名空间 | `offerpilot` |
| 仓库 | `offerpilot-api`、`offerpilot-web` |
| 镜像路径 | `<接入点>/offerpilot/offerpilot-{api,web}:<tag>` |

选个人版是因为免费且够用（两个私有仓库）。要接受它的三条硬约束：

1. **不提供 OpenAPI**。官方原文「个人版不对外提供 OpenAPI 接口」——命名空间和仓库只能在
   控制台建，无法脚本化；每账号限 1 个实例。CLI 的 `cr` 产品只暴露企业版接口（每个 action
   强制要求 `--InstanceId`），对本账号 `GetInstanceCount` 返回 0，别指望用它自动化。
2. **接入点有两代，用错直接 403**（详见 §8 坑 3）。本实例属于 2026 年在控制台新建的那一代，
   域名形如 `crpi-<id>.<region>.personal.cr.aliyuncs.com`，**不是**老的共享域名
   `registry.cn-beijing.aliyuncs.com`。
3. **拒绝 OCI attestation manifest**（详见 §8 坑 5）。

**凭据语义**（最容易反复错的地方）：`docker login` 的密码是控制台单独设的**「固定密码」**
（实例 → 访问凭证 → 设置固定密码），**不是**阿里云账号登录密码；用户名与密码必须配套——
主账号填账号登录名 `aliyun6330613892`（不是 AccountId），RAM 用户填去掉
`@xxx.onaliyun.com` 后缀的短名，且要用为该 RAM 用户单独设的固定密码。三套凭据互不通用：
**ACR 访问凭证**（给 `docker login`）/ **云助手 RAM 子账号 AK**（给 `aliyun ecs RunCommand`）/
**阿里云账号登录密码**（控制台）。权威说明见文末链接。

---

## 4. ECS 上的落地文件

```text
/root/offerpilot/
├── .env                      # 0600，14 个键，见下
└── docker-compose.prod.yml   # 由 CD 每次部署从仓库同步写入，见 §5.3
/root/.docker/config.json     # ACR 登录态（auths 里只有 crpi 那个 host）
```

`.env` 同时承担**两个角色**（这是理解整套配置的关键）：

1. **compose 变量插值**：`ACR_REGISTRY` / `ACR_NAMESPACE` / `OFFERPILOT_TAG`
   （`docker-compose.prod.yml:16`）；
2. **两个容器的运行时环境变量**：compose 里两个 service 都有 `env_file: .env`
   （`docker-compose.prod.yml:19,46`）。

模板见 `deploy/env.ecs.template`。注意：**`.env` 里显式写的 `ACR_REGISTRY` 会覆盖 compose
中的默认值**，所以改接入点时「仓库四处 + ECS `.env`」必须同步，漏一处会在很晚的阶段才炸。
`.env` 绝不能进 git，也绝不能打进镜像。

---

## 5. CI/CD 是怎么打通的

### 5.1 触发链

```text
push 到 main
  └─► CI（.github/workflows/ci.yml）四个 job：version / backend / web / docker
        └─ 全绿 ─► CD（.github/workflows/cd.yml）workflow_run: completed
              ├─ build-push：CI runner 上构建两个镜像 → 推 ACR
              └─ deploy：云助手在 ECS 上 pull && up -d
```

三个要点：

- **只有 `main` 会触发。** CD 是 `workflow_run` 且 `branches: [main]`，CI 又只在 push/PR 到
  `main` 时跑；所以在分支上的改动**不会触发任何流水线**，必须先合进 main。
- **CD 有两个入口。** 除自动触发外还有 `workflow_dispatch`——改完脚本/凭据直接在 Actions 页
  点 *Run workflow* 重跑，省掉等 CI 的约 20 分钟（CI 跑完还会自动再触发一次，重复但无害）。
  手动触发用的是所选 ref 的最新提交。
- `concurrency: cd-main` + `cancel-in-progress: false`：部署串行，不互相打断。

### 5.2 build-push job 用到的能力

| 能力 | 用途 | 位置 |
| --- | --- | --- |
| `actions/checkout@v4` | 取代码；`ref` 用 `workflow_run.head_sha \|\| github.sha`，保证构建的是 CI 验过的那次提交 | `cd.yml:28-30` |
| `docker/setup-buildx-action@v3` | 启用 BuildKit | `cd.yml:32` |
| `docker/login-action@v3` | 登录 ACR | `cd.yml:34-39` |
| `docker/build-push-action@v6` | 构建并推送，**两个 tag**：commit sha + `latest` | `cd.yml:41-66` |
| GitHub **repository** secrets | 存 ACR 凭据与阿里云 AK | 见 §5.4 |

镜像同时打 `${{ github.sha }}` 和 `latest` 两个 tag 是**为了回滚**：`latest` 给日常部署用，
sha tag 让回滚可以精确到某次提交（§7）。

### 5.3 deploy job 用到的能力

deploy job 不 SSH，而是通过**云助手（Cloud Assistant）**下发命令，好处是不用开 22 端口给
CI、不用往 runner 上放私钥：

```bash
# 1) 把远程脚本 base64 后下发（--InstanceId.1 是 repeatList，不是 JSON 数组）
aliyun ecs RunCommand --RegionId "$ECS_REGION" --InstanceId.1 "$ECS_INSTANCE_ID" \
  --Type RunShellScript --ContentEncoding Base64 --Timeout 900 \
  --CommandContent "$ENCODED"                        # cd.yml:117-124

# 2) 轮询结果（每 10s × 90 次），Success 才算部署成功
aliyun ecs DescribeInvocationResults --RegionId "$ECS_REGION" --InvokeId "$INVOKE_ID"
```

远程脚本做的事（`cd.yml:99-112`）：`cd /root/offerpilot` → **把仓库版 compose 写回本地**
→ `docker compose pull` → `up -d` → `docker image prune -f` → **轮询 `/health/ready` 与
`/api/health` 最多 30×5s**。最后这步很关键：命令返回不等于服务起来了，必须等两个健康检查
都过才敢说部署成功。

**为什么要同步 compose**：ECS 上的 `docker-compose.prod.yml` 不是 checkout 出来的，最初是
一次性放上去的静态副本——它不会随仓库更新而更新。deploy job 因此也做一次
`actions/checkout`，把文件 base64 后塞进下发脚本，用
`base64 -d > docker-compose.prod.yml` 覆盖写入，**让仓库始终是唯一事实源**。

**`ALIBABA_CLOUD_REGION_ID` 为什么单独设**（`cd.yml:89`）：aliyun CLI 的 region 上下文**只认
这个环境变量或 config 文件**，`--RegionId` 只是发请求时的 API 参数，替代不了它；缺了会在真正
发起请求前就中止（§8 坑 6）。

### 5.4 为什么密钥放在 repository secrets

CD 的两个 job 都**没有**声明 `environment:`，所以密钥必须是 **repository 级** secrets，
environment 级的在这里读不到。当前 5 个：

`ACR_NAMESPACE`、`ACR_USERNAME`、`ACR_PASSWORD`、`ALIYUN_ACCESS_KEY_ID`、
`ALIYUN_ACCESS_KEY_SECRET`。

**待办**：目前 `ALIYUN_ACCESS_KEY_*` 用的是主账号 AK，权限过大。应换成 RAM 子账号，只授
`ecs:RunCommand` + `ecs:DescribeInvocationResults`，且资源限定到
`acs:ecs:cn-beijing:*:instance/i-2zefdb9ur96jyr9ji6bg`。

---

## 6. 如何访问与验证

**用户入口**：<http://60.205.125.130:3000>（已在外部实测返回 200）。
API 不对外，只在 `127.0.0.1:3001`。

**在 ECS 上自查**：

```bash
curl --fail http://127.0.0.1:3001/health/ready   # api
curl --fail http://127.0.0.1:3000/api/health     # web → api 链路
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f --tail=100 api
```

健康的 `/health/ready` 长这样（`knowledgeEntries` 是**观测值**，随 Markdown 文件增减变化）：

```json
{
  "status": "ready", "version": "0.4.1", "ready": true,
  "harness": "ready", "modelConfigured": true, "speechConfigured": true,
  "knowledgeEntries": 486
}
```

**两个排查习惯**：

- **判断部署成没成，别看流水线颜色就下结论**——直接查 ECS 容器状态与上面两个健康端点。
  deploy job 的绿色只代表远程脚本退出 0，最终真相在容器里。
- 查 Actions 状态时，**workflow badge 不吃 GitHub API 配额**：
  `https://github.com/zhujunhao1106/OfferAgent/actions/workflows/{ci,cd}.yml/badge.svg`
  里的 `<title>CI - passing</title>` / `CD - failing` 就是答案。匿名 REST API 只有
  **60 次/小时**，拿它轮询很快会被 `403 rate limit exceeded` 打回。

---

## 7. 常用运维操作

| 场景 | 操作 |
| --- | --- |
| 发版 | 合进 `main`，等 CI 绿 → CD 自动跑。急的话 Actions 页 *Run workflow* 手动触发 CD |
| 回滚 | 把 ECS `.env` 的 `OFFERPILOT_TAG` 改成目标 commit sha，再 `docker compose -f docker-compose.prod.yml up -d`。**不要**在面试进行中回滚，等会话结束 |
| 改运行时配置 | 改 ECS `.env` 后 `docker compose -f docker-compose.prod.yml up -d --force-recreate` |
| 改模型配置（config 编辑器） | 编辑器写的是 `app-config` 卷里的 `.env`，**API 只在启动时读**，存完要 `docker compose restart api` |
| 看磁盘/内存 | `df -h` / `free -h` / `docker system df` |
| 清无用镜像 | `docker image prune -f`（CD 每次部署已自动做） |

回滚为何用 sha 而不是重建：镜像已经在仓库里，切换 tag 是秒级的；而「重建」在 2C2G 的机器上
不可行（§1）。

---

## 8. 上线过程中踩的坑（按暴露顺序）

这 7 个坑是**层层遮蔽**的：前一个不解决，后一个根本暴露不出来。每一层都记下现象、根因、修法。

### 坑 1 — CI 的 `web` job 被 `npm audit` 拦下（真正的起点）

- **现象**：CI 挂在 `npm audit --audit-level=high`，整条 CD 因此从不触发——部署卡住的第一因
  其实在依赖上。
- **根因**：新公布的 advisories 命中旧版传递依赖。
- **修法**：升 `next` / `undici` / `sharp` / `postcss` / `nanoid` / `browserslist` /
  `baseline-browser-mapping`，在 `package.json` 里用 `overrides` 钉住，再用 `node:24-slim`
  重生成 lockfile（**本机 node 是 v22，低于 web 要求的 `>=24 <25`，直接 `npm install` 会报
  `Cannot read properties of null (reading 'edgesOut')`**）。
- **副产品坑**：为修 audit 曾加 `@xmldom/xmldom: 0.9.12` override，结果**弄坏了 mammoth**——
  0.9 把 `DOMParser.parseFromString` 的 mimeType 参数改成必填，而 mammoth
  （pin 的是 `^0.8.6`）无第二参调用，抛
  `TypeError: ... the provided mimeType "undefined" is not valid`。**改成让 mammoth 自己解析到
  0.8.15**（advisory 范围是 `<=0.8.14`，0.8.15 恰好安全又保留了宽松签名）。用一段 DOCX 冒烟
  脚本验证后，删掉该 override。
- **文件**：`web/package.json`、`web/package-lock.json`、`web/next-env.d.ts`（Next 16.3.6 构建
  自动改写，一并提交）。

### 坑 2 — `docker/login-action` 返回 `unauthorized: authentication required`（401）

- **根因**：密码/用户名语义搞错——用了阿里云账号登录密码而不是 ACR **固定密码**。
- **修法**：控制台「实例 → 访问凭证 → 设置固定密码」，用户名填账号登录名。
- **要点**：**401 才是用户名/密码不对**，与下面的 403 是两回事，别混为一谈。

### 坑 3 — 换对密码后变成 `403 Forbidden`

- **根因**：**接入点代际不匹配**——拿新实例的凭据去登老的共享域名
  `registry.cn-beijing.aliyuncs.com`。个人版有两代域名，新实例必须用自己的专属域名
  `crpi-<id>.<region>.personal.cr.aliyuncs.com`。地址以控制台「实例 → 仓库管理 → 访问凭证」
  页面显示的登录命令为准。
- **修法**：全局替换成 `crpi-aq7wi69rg06f57t5.cn-beijing.personal.cr.aliyuncs.com`，共
  **6 处 4 个文件 + ECS 的 `.env`**：`cd.yml`、`docker-compose.prod.yml`（两处 image 默认值）、
  `deploy/env.ecs.template`、`deploy/ecs-init.sh`，再加 ECS `/root/offerpilot/.env`。
- **教训**：这五处是一整套，漏一处会**在很晚的阶段才炸**（甚至只在 ECS 拉取时才炸）。

### 坑 4 — ECS 侧从未 `docker login`

- **现象**：CI 能推上去，ECS 拉不下来（仓库是私有的）。
- **修法**：在 ECS 上执行
  `docker login crpi-aq7wi69rg06f57t5.cn-beijing.personal.cr.aliyuncs.com`，凭证落到
  `/root/.docker/config.json`，长期有效。
- **切忌加 `sudo`**：官方 FAQ 专门列了这个坑——`sudo docker login` 会先要 Linux 用户密码，
  输错就会被误判成 ACR 认证失败。

### 坑 5 — 推送被拒：`denied: unknown manifest class for application/vnd.oci.empty.v1+json`

- **根因**：`docker/build-push-action` v4+ 默认给镜像附加 **provenance attestation**，
  BuildKit 会把它作为独立 manifest 推送（config 类型 `application/vnd.oci.empty.v1+json`），
  ACR 个人版不认，整个 push 失败。注意报错发生在**推送**阶段——镜像其实已构建成功，所以
  ACR 上一个 tag 都没有，容易误判成构建失败。
- **修法**：两个 build step 显式 `provenance: false` + `sbom: false`（`cd.yml:49-50,61-62`）。

### 坑 6 — `ERROR: region can't be empty`

- **根因**：aliyun CLI 的 **region 上下文**未设置。`--RegionId` 只是请求参数，满足不了 CLI
  自身的前置校验，所以它在组装请求前就中止，并附带
  `Configuration failed, use aliyun configure to configure it`。
- **修法**：在 deploy step 加 `ALIBABA_CLOUD_REGION_ID: cn-beijing`（`cd.yml:89`）。同时确认
  AK 变量名 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 是正确的
  （另有 `ALIBABA_CLOUD_IGNORE_PROFILE=TRUE` 可忽略 runner 上的 profile 文件）。

### 坑 7 — 云助手 300s 超时不够用

- **根因**：首次部署要在 2C2G 的机器上拉两个镜像（web 753 MB + api 415 MB）再启动，默认窗口
  太短。
- **修法**：`--Timeout` 提到 **900**，CD 侧轮询同步放宽到 90×10s（`cd.yml:122,127`）。

### 险些踩到的三个（自查拦下）

- **在 `\` 续行中间插注释**：我一度想把说明注释插进 `aliyun ecs RunCommand \` 的命令里。
  bash 会先吃掉 `\`+换行再分词，那会让其后的 `--Timeout 900`、`--CommandContent`、`| jq`
  全部变成注释被吞掉。改成把注释放在 `INVOKE_ID=$(...)` 那一行**之上**。
- **`--InstanceId.1` 的形态**：它是 repeatList，不是 JSON 数组字符串；写成 `[...]` 会静默不生效。
- **ECS 上的 compose 副本在静默漂移**：核对时发现 ECS 上的 `docker-compose.prod.yml` 与仓库版
  md5 不一致（ECS `c8b88b8e60c626b271562689523db7ae` vs 仓库
  `9ca8b34a023f7533b6e723d74f41a170`）。当时行为恰好等价，**只是因为 `.env` 显式覆盖了
  `ACR_REGISTRY`**；再往后只要 compose 有实质改动（新增环境变量、改 healthcheck），就会静默
  沿用旧配置——这类问题不会立刻报错，而是下次部署时才以难查的方式暴露。**已修**：deploy job
  现在每次部署都把仓库版写回 ECS（见 §5.3）。

---

## 9. 已知隐患与待办

compose 副本漂移已修（§8 第三条），以下为仍然打开的项：

1. **主账号 AK 跑 CI**，权限过大，应换 RAM 子账号（见 §5.4）。
2. **`app-config` 卷里的 `.env` 是明文的**，且 `docker-compose.prod.yml` 中
   `OFFERPILOT_ENABLE_CONFIG_API=false`——公开部署必须保持关闭，并给该卷加主机权限/备份保护。
3. **无域名、无 HTTPS**，入口是裸 IP + 3000 端口。上线正式使用前应补域名与证书。
4. **单点、无数据备份**：`app-data` 卷里的 SQLite 无定时备份，实例到期（2027-09-29）需续费。
5. **安全组 3389/22 对公网开放**，建议收敛到固定来源 IP。

---

## 10. 权威文档

- ECS 初始化脚本：`deploy/ecs-init.sh`；`.env` 模板：`deploy/env.ecs.template`
- CD 工作流：`.github/workflows/cd.yml`；CI：`.github/workflows/ci.yml`
- 生产 compose：`docker-compose.prod.yml`
- ACR 访问凭证与固定密码：<https://help.aliyun.com/zh/acr/user-guide/access-credentials>
- docker login/push/pull 失败 FAQ（401、sudo 坑）：<https://help.aliyun.com/zh/acr/support/faq-about-errors-of-docker-login-docker-push-and-docker-pull>
- 个人版推送拉取（两代接入点格式）：<https://www.alibabacloud.com/help/zh/acr/user-guide/use-a-container-registry-personal-edition-instance-to-push-and-pull-images>
- aliyun CLI 环境变量全表：<https://help.aliyun.com/zh/cli/environment-variables>
