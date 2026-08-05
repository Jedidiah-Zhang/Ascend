"""存档加密 — 密钥生成、管理与文件加解密。

加密目的（Issue #13 讨论确认）：防止其他工具直读/篡改存档，
不追求防读取/防迁移——密钥随档分发（藏于 manifest 的混淆层），
游戏自动解锁，存档可移植、可分享。

威胁模型（"防君子不防小人"）：
  - 密钥不落盘为明文 base64，而是用 world_id + seed 派生密钥加密后
    藏进 manifest.json 的 secrets_blob 字段——普通工具看到的只是乱码；
  - world_id/seed 本身在 manifest 明文，派生密钥可被推算，因此这只是
    混淆层（防直读/防手贱），不是真实加密（真实加密见 PBKDF2 用户密码方案）；
  - 防篡改的真实防线是 HMAC（decrypt 先验签名）。

文件加密格式:
    HMAC_SHA256(ciphertext) || ciphertext
    - 外部 HMAC 覆盖整个密文（防替换/截断/拼接）
    - ciphertext 为 Fernet token（Fernet 自身亦带完整性校验）
    - 解密流程: 校验 HMAC → Fernet 解密

密钥生成: 每存档自动生成 32 字节 Fernet 密钥 + 32 字节签名密钥，
加密后藏于 manifest.secrets_blob（随档分发，可移植）。
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken



# 前缀区长度：HMAC-SHA256 摘要 32 字节
HMAC_LEN: int = 32

# 密钥混淆层的派生域分隔（防与其它派生用途碰撞）
_SECRETS_DOMAIN: bytes = b"ascend-secrets-v1"


class SaveCryptoError(Exception):
    """存档解密/校验失败（密钥缺失、被篡改、损坏）。"""


def _b64(data: bytes) -> str:
    """bytes → urlsafe base64 字符串。"""
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    """urlsafe base64 字符串 → bytes。"""
    return base64.urlsafe_b64decode(text.encode("ascii"))


@dataclass(slots=True)
class SaveKeys:
    """存档密钥对：Fernet 加密密钥 + HMAC 签名密钥（各 32 字节）。"""

    fernet_key: bytes
    sign_key: bytes

    # ── 加密 ──────────────────────────────────────────────

    def encrypt(self, data: bytes) -> bytes:
        """加密并签名：返回 HMAC(ciphertext) || ciphertext。

        Args:
            data: 明文字节。

        Returns:
            HMAC 前缀 + Fernet 密文。
        """
        ciphertext = Fernet(self._fernet_b64()).encrypt(data)
        return hmac.new(
            self.sign_key, ciphertext, hashlib.sha256,
        ).digest() + ciphertext

    def decrypt(self, token: bytes) -> bytes:
        """校验 HMAC 并解密。

        Args:
            token: encrypt() 的输出。

        Returns:
            明文字节。

        Raises:
            SaveCryptoError: 签名不匹配或解密失败（被篡改/损坏）。
        """
        if len(token) < HMAC_LEN:
            raise SaveCryptoError("存档文件过短，疑似损坏")
        sig, ciphertext = token[:HMAC_LEN], token[HMAC_LEN:]
        expected = hmac.new(
            self.sign_key, ciphertext, hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sig, expected):
            raise SaveCryptoError("存档签名校验失败，文件被篡改或损坏")
        try:
            return Fernet(self._fernet_b64()).decrypt(ciphertext)
        except InvalidToken as exc:
            raise SaveCryptoError(f"存档解密失败: {exc}") from exc

    def sign_bytes(self, data: bytes) -> bytes:
        """独立签名（供非加密文件做完整性校验）。"""
        return hmac.new(self.sign_key, data, hashlib.sha256).digest()

    # ── 密钥管理 ──────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为可写 JSON 的字典。"""
        return {
            "fernet_key": _b64(self.fernet_key),
            "sign_key": _b64(self.sign_key),
        }

    @staticmethod
    def from_dict(data: dict) -> "SaveKeys":
        """从字典反序列化。

        Raises:
            SaveCryptoError: 字段缺失或格式非法。
        """
        try:
            return SaveKeys(
                fernet_key=_unb64(data["fernet_key"]),
                sign_key=_unb64(data["sign_key"]),
            )
        except (KeyError, ValueError, base64.binascii.Error) as exc:
            raise SaveCryptoError(f"密钥数据格式非法: {exc}") from exc

    @staticmethod
    def generate() -> "SaveKeys":
        """生成随机密钥对。"""
        return SaveKeys(
            fernet_key=secrets.token_bytes(32),
            sign_key=secrets.token_bytes(32),
        )

    # ── 混淆层（藏入 manifest.secrets_blob） ──────────────

    @staticmethod
    def _derive_obfuscation_key(world_id: str, seed: int) -> bytes:
        """从存档身份（world_id + seed）派生混淆密钥。

        两者都在 manifest 明文，故可被推算——本层只防直读，
        不提供真实安全性（威胁模型见模块文档）。
        """
        return base64.urlsafe_b64encode(hashlib.sha256(
            _SECRETS_DOMAIN
            + world_id.encode("utf-8")
            + b"\x00"
            + str(int(seed)).encode("ascii")
        ).digest())

    def protect(self, world_id: str, seed: int) -> str:
        """把密钥对加密为可藏入 manifest 的混淆串。

        Args:
            world_id: 存档位 ID（派生输入）。
            seed: 世界种子（派生输入）。

        Returns:
            Fernet token 字符串（base64），普通工具看到的是乱码。
        """
        payload = json.dumps(
            self.to_dict(), ensure_ascii=False,
        ).encode("utf-8")
        return Fernet(
            self._derive_obfuscation_key(world_id, seed)
        ).encrypt(payload).decode("ascii")

    @staticmethod
    def from_protected(blob: str, world_id: str, seed: int) -> "SaveKeys":
        """从混淆串还原密钥对。

        Args:
            blob: protect() 的输出。
            world_id: 存档位 ID。
            seed: 世界种子。

        Raises:
            SaveCryptoError: 串格式非法或与存档身份不匹配（如存档
                被外部工具修改了 world_id/seed）。
        """
        try:
            payload = Fernet(
                SaveKeys._derive_obfuscation_key(world_id, seed)
            ).decrypt(blob.encode("ascii"))
            return SaveKeys.from_dict(json.loads(payload.decode("utf-8")))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise SaveCryptoError(
                f"密钥混淆串校验失败（可能被篡改或存档身份不符）: {exc}"
            ) from exc

    def _fernet_b64(self) -> bytes:
        """Fernet 密钥的 urlsafe base64 形式（Fernet 构造函数要求）。"""
        return base64.urlsafe_b64encode(self.fernet_key)
