import hashlib
import hmac

from fastapi import Request


async def verify_signature(request: Request, secret: str) -> bool:
    """验证 Langfuse 请求签名；无密钥或签名无效时返回 False。"""
    if not secret:
        return False

    signature_header = request.headers.get("x-langfuse-signature", "")
    parts = signature_header.split(",")
    if (
        len(parts) != 2
        or not parts[0].startswith("t=")
        or not parts[1].startswith("v1=")
    ):
        return False

    timestamp = parts[0][2:]
    signature = parts[1][3:]
    if not timestamp or not signature:
        return False

    try:
        received = bytes.fromhex(signature)
    except ValueError:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + await request.body(),
        hashlib.sha256,
    ).digest()
    return hmac.compare_digest(received, expected)
