# Outcome Skills（结果型 Skill）

[English](README.md) | [简体中文](README.zh-CN.md)

一段输入，一份成品交付。

免费公测：直接获得一份可以立即使用的销售资产。

三个可独立安装的 Skill，把销售上下文整理成边界明确、可审阅的交付物。
仓库同时提供一个小型网关和仅监听回环地址的演示；仓库本身不提供托管好的公网
服务地址。

## 仓库包含什么

公开范围固定为三个彼此独立的 Skill：

| Skill | 适用场景 | 网关返回 |
| --- | --- | --- |
| `outcome-offer` | 粗略的服务、产品、受众或问题 | 报价卡 |
| `proof-pack` | 已验证的结果、指标、笔记或原始证言 | 证据包 |
| `reply-to-close` | 一条客户异议和产品上下文 | 有依据的下一步回复 |

报价卡包含成果表述、产品名、客户、购买时机、交付内容、利益点、风险逆转、标题
和可直接粘贴的销售段落。证据包包含证明标题、提案简介、案例故事、三条证据、社媒
文案、销售对话版本、溯源、缺失证据和质量检查。推进成交回复包含主回复、短回复、
异议分类、一个下一步、假设与溯源说明以及质量检查。

交付合同由服务端维护。缺失事实会继续标为未知或假设；这些 Skill 不承诺收入、
转化、会议、购买或成交。`examples/` 中的三个文件是受控演示输出，不是客户案例。

## 当前状态

当前版本用于免费公测。仓库内的演示不收费，也不需要钱包、商户账号、支付证明或
支付凭证。美元和人民币支付适配器作为未来运营配置的接口保留，但目前没有启用；
人民币/京东入驻也没有配置。

仓库没有托管好的网关 URL。真正部署还需要运营方自己的组合入口、受保护的输入存储、
结果引擎和部署密钥。SQLite 输入存储和固定结果引擎只是本地演示组件，不是生产设施。
参见 [部署说明](docs/deploy.md) 和 [未来支付说明](docs/activate-live-payments.md)。

## 安装 Skill

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -e .
python scripts/install.py --target codex --scope project --dry-run
python scripts/install.py --target codex --scope project
```

安装器会复制完整的 Skill 目录。使用 `--skill` 可只选择一个 Skill；重复该选项可以
选择多个。默认不会覆盖同名目录，只有显式传入 `--force` 才会替换；强制替换前先运行
`--dry-run`。

支持的安装目标和目录约定如下：

| 目标 | 项目范围 | 用户范围 |
| --- | --- | --- |
| `codex` | `.agents/skills` | `~/.agents/skills` |
| `joycode` | `.joycode/skills` | `~/.joycode/skills` |
| `openclaw` | `skills/` | `~/.openclaw/skills` |

这些是安装器的目标目录；宿主是否会发现目录，由宿主自身决定。仓库的 `skills/` 是
唯一安装来源。必须复制整个 Skill 目录，其中包含客户端和引用资料。

示例：

```powershell
python scripts/install.py --target joycode --scope user --dry-run
python scripts/install.py --target openclaw --scope project `
  --project-root C:\workspace --dry-run
python scripts/install.py --target codex --scope project --skill proof-pack
```

带有 `v*` 标签的版本会通过[发布工作流](.github/workflows/release.yml)为每个 Skill
发布一个独立压缩包。

## 运行本地免费演示

演示只监听回环地址，使用仓库目录之外的临时存储，进程退出后清理。它使用确定性的
固定结果引擎，不调用模型，也不发生资金流转。

终端 1：

```powershell
python scripts/run_demo_gateway.py --port 8000
```

终端 2：把买家输入通过 UTF-8 标准输入传递，并设置回环网关地址：

```powershell
$env:OUTCOMES_GATEWAY_URL = "http://127.0.0.1:8000"
Get-Content -Raw -Encoding utf8 .\buyer-input.txt |
  python skills\outcome-offer\scripts\client.py quote --input-stdin `
    --currency USD --locale en-US --idempotency-key "demo-001"
python skills\outcome-offer\scripts\client.py status --order-id "<ORDER_ID>"
python skills\outcome-offer\scripts\client.py fulfill --order-id "<ORDER_ID>"
```

`quote` 会打印访问模式和订单号。访问令牌保存在客户端的私有本地状态中，后续状态和
结果请求由客户端自动发送；令牌不会作为公开交付物输出。返回结果由固定引擎生成。
部署到网关时，客户端要求精确的 HTTPS 源站，并拒绝凭据、查询参数、片段和重定向；
私有输入仍应通过标准输入传递。运营方预检见 [docs/deploy.md](docs/deploy.md)。

三个受控示例：

- [Outcome Offer](examples/outcome-offer.md)
- [Proof Pack](examples/proof-pack.md)
- [Reply to Close](examples/reply-to-close.md)

## 开发与测试

安装项目和测试依赖后运行仓库检查：

```powershell
python -m pip install -e . pytest
python -m pytest -q
python scripts/validate_repo.py
```

验证器在不发起网络请求的情况下检查三个 Skill 的固定范围、可移植文件、示例、发布
副本、插件清单以及敏感值/路径卫生。`git diff --check` 可作为本地改动的额外检查。

仓库没有预先组合好的生产 ASGI 启动命令。组合部署前请阅读 [docs/deploy.md](docs/deploy.md)，
不要把演示脚本当作生产服务运行。

## 目录说明

- `skills/`：三个可安装的 Skill、客户端和引用资料
- `gateway/`：目录、订单状态、结果访问和支付适配器接口
- `scripts/`：安装器、回环演示启动器和仓库验证器
- `examples/`、`evals/`：受控示例与评估材料
- `tests/`：API、客户端、支付合同、安装和验证测试
- `docs/`：部署与未来支付说明

## 安全、贡献与许可证

不要提交凭证、钱包密钥、支付证明、买家输入或结果访问令牌。`.env.example` 只包含
占位值。运行网关前请阅读 [SECURITY.md](SECURITY.md)，提交改动请参阅
[CONTRIBUTING.md](CONTRIBUTING.md)。

项目采用 [MIT 许可证](LICENSE)，行为规范见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

