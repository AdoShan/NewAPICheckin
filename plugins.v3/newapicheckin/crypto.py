"""
敏感信息加密。

优先使用宿主已提供的 cryptography（AES-256-GCM），缺失时退化为基于
SHA-256 派生密钥的对称混淆，并明确标记为不可靠模式。
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple

_PREFIX_AESGCM = "gcm:"
_PREFIX_FALLBACK = "fb:"


class SecretBox:
    """
    账号口令与会话令牌的加解密封装。
    """

    def __init__(self, key_path: Path) -> None:
        """
        初始化密钥容器。

        :param key_path: 密钥文件路径
        """
        self.key_path = Path(key_path)
        self._key = self._load_or_create_key()
        self._aesgcm = self._try_load_aesgcm()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _load_or_create_key(self) -> bytes:
        """
        读取密钥文件，不存在时生成新的随机密钥。

        :return: 32 字节密钥
        """
        if self.key_path.exists():
            data = self.key_path.read_bytes().strip()
            if data:
                try:
                    return base64.b64decode(data)
                except Exception:  # pylint: disable=broad-except
                    return hashlib.sha256(data).digest()
        key = os.urandom(32)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(base64.b64encode(key))
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    @staticmethod
    def _try_load_aesgcm():
        """
        尝试加载 AESGCM 实现。

        :return: AESGCM 类或 None
        """
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pylint: disable=import-outside-toplevel

            return AESGCM
        except Exception:  # pylint: disable=broad-except
            return None

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        """
        生成兜底模式的密钥流。

        :param nonce: 随机串
        :param length: 需要的长度
        :return: 密钥流字节
        """
        stream = b""
        counter = 0
        while len(stream) < length:
            stream += hashlib.sha256(self._key + nonce + counter.to_bytes(4, "big")).digest()
            counter += 1
        return stream[:length]

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    @property
    def reliable(self) -> bool:
        """
        当前是否为 AES-GCM 可靠加密模式。

        :return: True 表示使用 AES-256-GCM
        """
        return self._aesgcm is not None

    def encrypt(self, plain_text: Optional[str]) -> Optional[str]:
        """
        加密明文。

        :param plain_text: 明文
        :return: 密文文本
        """
        if plain_text is None:
            return None
        raw = str(plain_text).encode("utf-8")
        if not raw:
            return None
        if self._aesgcm is not None:
            nonce = os.urandom(12)
            cipher = self._aesgcm(self._key)
            blob = nonce + cipher.encrypt(nonce, raw, None)
            return f"{_PREFIX_AESGCM}{base64.b64encode(blob).decode('ascii')}"
        nonce = os.urandom(16)
        stream = self._keystream(nonce, len(raw))
        cipher_text = bytes(a ^ b for a, b in zip(raw, stream))
        digest = hashlib.sha256(self._key + nonce + cipher_text).digest()[:8]
        return f"{_PREFIX_FALLBACK}{base64.b64encode(nonce + digest + cipher_text).decode('ascii')}"

    def decrypt(self, cipher_text: Optional[str]) -> Optional[str]:
        """
        解密密文。

        :param cipher_text: 密文文本
        :return: 明文
        """
        if not cipher_text:
            return None
        text = str(cipher_text)
        if text.startswith(_PREFIX_AESGCM):
            if self._aesgcm is None:
                return None
            try:
                blob = base64.b64decode(text[len(_PREFIX_AESGCM):])
                nonce, payload = blob[:12], blob[12:]
                return self._aesgcm(self._key).decrypt(nonce, payload, None).decode("utf-8")
            except Exception:  # pylint: disable=broad-except
                return None
        if text.startswith(_PREFIX_FALLBACK):
            try:
                blob = base64.b64decode(text[len(_PREFIX_FALLBACK):])
                nonce, digest, cipher_payload = blob[:16], blob[16:24], blob[24:]
                stream = self._keystream(nonce, len(cipher_payload))
                plain = bytes(a ^ b for a, b in zip(cipher_payload, stream))
                if hashlib.sha256(self._key + nonce + cipher_payload).digest()[:8] != digest:
                    return None
                return plain.decode("utf-8")
            except Exception:  # pylint: disable=broad-except
                return None
        return None

    def encrypt_pair(self, plain_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """
        加密并返回可直接落库的值。

        :param plain_text: 明文
        :return: 密文或 None
        """
        return self.encrypt(plain_text), plain_text is not None
