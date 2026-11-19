# 拾光连锁｜会员权益与门店服务

FDE 业务挖掘与现场调研提交材料。

## 项目选题

会员优惠券适用规则核验助手（只读）。

项目面向门店收银员，帮助核对优惠券适用条件、规则版本、更新时间和依据来源。系统只提供可追溯解释和收银辅助，不自动改价、发券、退款、调整积分或修改会员资料。

## 目录

- `docs/会员权益与门店服务-业务挖掘报告.md`：阶段 01 业务挖掘报告
- `docs/会员权益与门店服务-现场调研报告.md`：阶段 02 现场调研报告
- `docs/discovery-report.md`：软件导出的完整访谈与需求发现报告
- `docs/会员权益与门店服务-现状流程图.md`：当前人工流程图、角色与系统说明
- `docs/原有资料/`：网站导出的四份可核对资料

## 关键约束

- 退货积分已兑换新券、资料缺少依据、规则版本冲突或关键系统不可用时必须转人工。
- 涉及退款、积分更改、对外承诺或会员资产变更时，必须由对应审批人确认。
- 未核实的节点耗时、权限字段、审核时限和正式留存期限保持为“待补证据”，不把训练假设写成真实业务事实。

## 资料说明

本项目材料来自 FDE 训练中心的合成场景访谈。真实业务日志、制度文件和现场观察记录需要后续补充核验。

## MVP 运行

```powershell
cd .\outputs\retail-membership-fde
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ollama serve
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

规则核验：

```powershell
$body = @{ coupon_code = 'S8-20'; question = '这张优惠券适用条件是什么？' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/rights/check -Method Post -Headers @{Authorization='Bearer cashier-dev-token'} -ContentType 'application/json' -Body $body
```

自测：

```powershell
python tests/verify_mvp.py
```

容器部署：

```powershell
docker build -t retail-membership-agent .
docker run --rm -p 8000:8000 -v ${PWD}/config.yaml:/app/config.yaml retail-membership-agent
```

容器需要能够访问 Ollama；如果 Ollama 在宿主机运行，按部署环境把 `config.yaml` 中的 `ollama.base_url` 改为容器可访问的宿主机地址。`config.yaml` 中的开发 token 只用于本地演示，上线前必须替换为环境注入的密钥并启用 HTTPS。
