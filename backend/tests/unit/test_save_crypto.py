"""存档加密单元测试 — 密钥管理、加解密往返与防篡改。

覆盖 ascend/save/crypto.py。
"""

import json

import pytest

from ascend.save.crypto import SaveKeys, SaveCryptoError


class TestSaveKeys:
    """密钥生成与管理。"""

    def test_generate_creates_valid_keys(self):
        """生成的密钥对长度正确且可构造 Fernet。"""
        keys = SaveKeys.generate()
        assert len(keys.fernet_key) == 32
        assert len(keys.sign_key) == 32

    def test_to_from_dict_roundtrip(self):
        """序列化往返保持密钥不变。"""
        keys = SaveKeys.generate()
        restored = SaveKeys.from_dict(keys.to_dict())
        assert restored.fernet_key == keys.fernet_key
        assert restored.sign_key == keys.sign_key

    def test_from_dict_rejects_missing_field(self):
        """缺字段的密钥字典被拒绝。"""
        with pytest.raises(SaveCryptoError):
            SaveKeys.from_dict({"fernet_key": "aGVsbG8="})

    def test_from_dict_rejects_bad_base64(self):
        """非法 base64 被拒绝。"""
        with pytest.raises(SaveCryptoError):
            SaveKeys.from_dict({
                "fernet_key": "!!!not-base64!!!",
                "sign_key": "!!!not-base64!!!",
            })


class TestEncryptDecrypt:
    """加解密往返与防篡改。"""

    def test_roundtrip(self):
        """加密解密往返还原明文。"""
        keys = SaveKeys.generate()
        token = keys.encrypt(b"hello world")
        assert keys.decrypt(token) == b"hello world"

    def test_ciphertext_is_scrambled(self):
        """密文不包含明文（防其他工具直读）。"""
        keys = SaveKeys.generate()
        token = keys.encrypt(b"secret-player-position")
        assert b"secret-player-position" not in token

    def test_two_encryptions_differ(self):
        """同明文两次加密产生不同密文（Fernet 随机 IV）。"""
        keys = SaveKeys.generate()
        a = keys.encrypt(b"data")
        b = keys.encrypt(b"data")
        assert a != b

    def test_tampered_token_rejected(self):
        """篡改密文任意字节导致解密失败。"""
        keys = SaveKeys.generate()
        token = bytearray(keys.encrypt(b"important data"))
        token[len(token) // 2] ^= 0xFF
        with pytest.raises(SaveCryptoError):
            keys.decrypt(bytes(token))

    def test_truncated_token_rejected(self):
        """截断的密文被拒绝。"""
        keys = SaveKeys.generate()
        token = keys.encrypt(b"data")
        with pytest.raises(SaveCryptoError):
            keys.decrypt(token[:10])

    def test_empty_token_rejected(self):
        """空密文被拒绝。"""
        keys = SaveKeys.generate()
        with pytest.raises(SaveCryptoError):
            keys.decrypt(b"")

    def test_wrong_keys_rejected(self):
        """用错误密钥解密失败（密钥不匹配/误用）。"""
        token = SaveKeys.generate().encrypt(b"data")
        with pytest.raises(SaveCryptoError):
            SaveKeys.generate().decrypt(token)

    def test_sign_bytes_detects_tamper(self):
        """独立签名可检测数据篡改。"""
        keys = SaveKeys.generate()
        sig = keys.sign_bytes(b"payload")
        assert len(sig) == 32
        assert keys.sign_bytes(b"payload") == sig
        assert keys.sign_bytes(b"payload!") != sig


class TestSecretsObfuscation:
    """密钥混淆层（manifest.secrets_blob）。"""

    def test_protect_from_protected_roundtrip(self):
        """混淆往返还原同一密钥对。"""
        keys = SaveKeys.generate()
        blob = keys.protect("world-abc", 12345)
        restored = SaveKeys.from_protected(blob, "world-abc", 12345)
        assert restored.fernet_key == keys.fernet_key
        assert restored.sign_key == keys.sign_key

    def test_blob_is_not_plaintext(self):
        """混淆串不含明文密钥材料。"""
        keys = SaveKeys.generate()
        blob = keys.protect("world-abc", 12345)
        assert "fernet_key" not in blob
        assert "sign_key" not in blob

    def test_wrong_identity_rejected(self):
        """存档身份（world_id/seed）不符时拒绝解出。"""
        keys = SaveKeys.generate()
        blob = keys.protect("world-abc", 12345)
        with pytest.raises(SaveCryptoError):
            SaveKeys.from_protected(blob, "world-other", 12345)
        with pytest.raises(SaveCryptoError):
            SaveKeys.from_protected(blob, "world-abc", 99999)

    def test_tampered_blob_rejected(self):
        """篡改混淆串拒绝解出。"""
        keys = SaveKeys.generate()
        blob = keys.protect("world-abc", 12345)
        bad = ("A" if blob[0] != "A" else "B") + blob[1:]
        with pytest.raises(SaveCryptoError):
            SaveKeys.from_protected(bad, "world-abc", 12345)
