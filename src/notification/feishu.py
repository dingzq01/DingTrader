import json

import requests

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


def send_feishu_message(msg: str) -> bool:
    """推送文本消息到飞书 Webhook。

    Returns: True if successful, False otherwise.
    """
    settings = get_settings()
    webhook = settings.feishu.webhook_url
    keyword = settings.feishu.keyword

    if not webhook:
        logger.warning("feishu_webhook_not_configured")
        return False

    msg_with_keyword = f"{keyword} {msg}"

    try:
        data = {
            "msg_type": "text",
            "content": {"text": msg_with_keyword},
        }
        headers = {"Content-Type": "application/json"}
        resp = requests.post(
            webhook,
            data=json.dumps(data),
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("feishu_send_failed", status=resp.status_code, body=resp.text)
            return False
        logger.info("feishu_sent", msg_len=len(msg))
        return True
    except Exception as e:
        logger.error("feishu_send_exception", error=str(e))
        return False


def format_strategy_result_msg(strategy_name: str, results: list[dict]) -> str:
    """格式化策略选股结果为飞书消息文本。"""
    if not results:
        return f"【{strategy_name}】暂无符合条件的股票"

    lines = [f"【{strategy_name}】共选中{len(results)}只股票："]
    for r in results:
        code = r.get("stock_code", "")
        extras = ", ".join(f"{k}={v}" for k, v in r.items() if k != "stock_code")
        lines.append(f"代码：{code} | {extras}")

    return "\n".join(lines)