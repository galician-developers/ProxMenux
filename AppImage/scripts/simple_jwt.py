"""
Simple JWT Implementation
A minimal JWT implementation using only Python standard library.
Supports HS256 algorithm without requiring cryptography or PyJWT.
This ensures compatibility across all Python versions and systems.
"""

import hmac
import hashlib
import base64
import json
import time
from typing import Optional, Dict, Any


class ExpiredSignatureError(Exception):
    """Token has expired"""
    pass


class InvalidTokenError(Exception):
    """Token is invalid"""
    pass


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string (no padding)"""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def _base64url_decode(data: str) -> bytes:
    """Decode base64url string to bytes"""
    # Add padding if needed
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data.encode('utf-8'))


def encode(payload: Dict[str, Any], secret: str, algorithm: str = "HS256") -> str:
    """
    Encode a payload into a JWT token.
    
    Args:
        payload: Dictionary containing the claims
        secret: Secret key for signing
        algorithm: Algorithm to use (only HS256 supported)
    
    Returns:
        JWT token string
    """
    if algorithm != "HS256":
        raise ValueError(f"Algorithm {algorithm} not supported. Only HS256 is available.")
    
    # Header
    header = {"typ": "JWT", "alg": "HS256"}
    header_b64 = _base64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
    
    # Payload
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    
    # Signature
    message = f"{header_b64}.{payload_b64}"
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature_b64 = _base64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode(token: str, secret: str, algorithms: list = None) -> Dict[str, Any]:
    """
    Decode and verify a JWT token.
    
    Args:
        token: JWT token string
        secret: Secret key for verification
        algorithms: List of allowed algorithms (ignored, only HS256 supported)
    
    Returns:
        Decoded payload dictionary
    
    Raises:
        InvalidTokenError: If token is malformed or signature is invalid
        ExpiredSignatureError: If token has expired
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise InvalidTokenError("Token must have 3 parts")
        
        header_b64, payload_b64, signature_b64 = parts
        
        # Verify signature
        message = f"{header_b64}.{payload_b64}"
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        actual_signature = _base64url_decode(signature_b64)
        
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise InvalidTokenError("Signature verification failed")
        
        # Decode payload
        payload = json.loads(_base64url_decode(payload_b64).decode('utf-8'))
        
        # Check expiration
        if 'exp' in payload:
            if time.time() > payload['exp']:
                raise ExpiredSignatureError("Token has expired")
        
        return payload
        
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise InvalidTokenError(f"Invalid token format: {e}")


# Compatibility aliases for PyJWT interface
class PyJWTCompat:
    """Compatibility class to mimic PyJWT interface"""
    ExpiredSignatureError = ExpiredSignatureError
    InvalidTokenError = InvalidTokenError
    
    @staticmethod
    def encode(payload, secret, algorithm="HS256"):
        return encode(payload, secret, algorithm)
    
    @staticmethod
    def decode(token, secret, algorithms=None):
        return decode(token, secret, algorithms)
