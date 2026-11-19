"""使用标准库验证 MVP 的健康检查、权限和规则兜底。"""

import json
import urllib.request


BASE = "http://127.0.0.1:8000"


def request(path: str, body: dict | None = None, token: str = "cashier-dev-token") -> dict:
    """发送测试请求并返回 JSON；连接失败时让脚本以非零状态退出。"""

    data = json.dumps(body).encode() if body is not None else None
    request_object = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request_object, timeout=10) as response:
        return json.loads(response.read().decode())


assert request("/health")["ok"] is True
known = request("/api/v1/rights/check", {"coupon_code": "S8-20", "question": "这张优惠券适用条件是什么？"})
assert known["ok"] is True and known["data"]["decision"] in {"answered", "handoff"}
risky = request("/api/v1/rights/check", {"coupon_code": "S8-20", "question": "退货时如何追回积分？"})
assert risky["ok"] is True and risky["data"]["decision"] == "handoff"
print("MVP verification passed")
