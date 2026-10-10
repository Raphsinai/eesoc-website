"""A software passkey authenticator, and a cookie-keeping "browser", for pytest.

The api's WebAuthn checks are only worth testing against real cryptography, so
this does what a phone does: holds a P-256 key per credential, builds the
authenticator data and attestation object in CBOR, and signs with ECDSA. What
it returns is the same JSON a browser produces (base64url fields), so the api
cannot tell it from Chrome — which is the point.

Deliberately not a test of the browser half: the Playwright suite drives a real
Chromium against a CDP virtual authenticator for that (tests/e2e/passkeys/).

Named `passkey_authenticator` rather than `helpers`: pytest puts each test
directory on sys.path, so a module name must be unique across the whole tree.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from urllib.parse import urlsplit

import cbor2
import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

# The origin the test stack's api expects: SITE_ORIGIN in backend/compose.yaml.
ORIGIN = os.environ.get("EESOC_SITE_ORIGIN", "http://localhost:8080")

# Authenticator data flags (WebAuthn §6.1).
UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


class SoftAuthenticator:
    """Makes passkeys and signs with them. One instance is one device."""

    def __init__(self, origin: str = ORIGIN):
        self.origin = origin
        # credential id (bytes) -> {"key", "user_handle", "counter"}
        self.credentials: dict[bytes, dict] = {}

    def create(self, options: dict, *, origin: str | None = None, counter: int = 0,
               synced: bool = True) -> dict:
        """navigator.credentials.create(), answering the server's options."""
        rp_id = options["rp"]["id"]
        cred_id = os.urandom(32)
        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        # COSE_Key for ES256: kty EC2, alg -7, crv P-256, x, y.
        cose = cbor2.dumps({
            1: 2, 3: -7, -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })
        flags = UP | UV | AT | ((BE | BS) if synced else 0)
        auth_data = (
            _sha256(rp_id.encode())
            + bytes([flags])
            + struct.pack(">I", counter)
            + bytes(16)  # AAGUID: none, as from "attestation: none"
            + struct.pack(">H", len(cred_id))
            + cred_id
            + cose
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = json.dumps({
            "type": "webauthn.create",
            "challenge": options["challenge"],
            "origin": origin or self.origin,
            "crossOrigin": False,
        }).encode()

        self.credentials[cred_id] = {
            "key": key,
            "user_handle": options["user"]["id"],
            "counter": counter,
            "rp_id": rp_id,
        }
        return {
            "id": b64url(cred_id),
            "rawId": b64url(cred_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal", "hybrid"],
            },
        }

    def get(self, options: dict, *, credential_id: str | None = None,
            origin: str | None = None, counter: int | None = None) -> dict:
        """navigator.credentials.get(): sign the challenge with a passkey.

        With no `credential_id`, the only one this device holds — which is what
        a discoverable sign-in looks like when there is one passkey to pick.
        """
        if credential_id is None:
            assert len(self.credentials) == 1, "say which passkey to use"
            cred_id = next(iter(self.credentials))
        else:
            cred_id = unb64url(credential_id)
        cred = self.credentials[cred_id]
        if counter is not None:
            cred["counter"] = counter

        auth_data = (
            _sha256(options.get("rpId", cred["rp_id"]).encode())
            + bytes([UP | UV])
            + struct.pack(">I", cred["counter"])
        )
        client_data = json.dumps({
            "type": "webauthn.get",
            "challenge": options["challenge"],
            "origin": origin or self.origin,
            "crossOrigin": False,
        }).encode()
        signature = cred["key"].sign(
            auth_data + _sha256(client_data), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(cred_id),
            "rawId": b64url(cred_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": cred["user_handle"],
            },
        }


class Browser:
    """An httpx client that behaves like one browser tab: it keeps the cookies
    the api sets and echoes the CSRF cookie in the header on unsafe methods,
    exactly as frontend/src/api/index.js does."""

    def __init__(self, base_url: str):
        self.http = httpx.Client(base_url=base_url, timeout=15.0)

    def _headers(self) -> dict:
        csrf = self.http.cookies.get("csrf")
        return {"X-CSRF-Token": csrf} if csrf else {}

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.http.get(path, **kwargs)

    def post(self, path: str, json=None, **kwargs) -> httpx.Response:
        return self.http.post(path, json=json if json is not None else {},
                              headers=self._headers(), **kwargs)

    def delete(self, path: str, **kwargs) -> httpx.Response:
        return self.http.delete(path, headers=self._headers(), **kwargs)

    def dev_login(self, email: str, make_admin: bool = False) -> dict:
        res = self.http.post("/auth/dev-login", json={"email": email, "make_admin": make_admin})
        res.raise_for_status()
        return res.json()

    def session(self) -> dict:
        return self.get("/auth/session").json()

    # ---- the three ceremonies, end to end ----

    def add_passkey(self, device: SoftAuthenticator, name: str = "Test device") -> httpx.Response:
        options = self.post("/auth/passkeys/options")
        options.raise_for_status()
        answer = device.create(options.json())
        return self.post("/auth/passkeys", {"credential": answer, "name": name})

    def passkey_sign_in(self, device: SoftAuthenticator, **get_kwargs) -> httpx.Response:
        options = self.post("/auth/passkey/options")
        options.raise_for_status()
        answer = device.get(options.json(), **get_kwargs)
        return self.post("/auth/passkey/verify", {"credential": answer})

    def redeem_link(self, token: str, device: SoftAuthenticator) -> httpx.Response:
        options = self.post("/auth/passkey-link/options", {"token": token})
        options.raise_for_status()
        answer = device.create(options.json())
        return self.post(
            "/auth/passkey-link/register",
            {"token": token, "credential": answer, "name": "Link device"},
        )


def token_from(url: str) -> str:
    """The link's token: everything after the '#'."""
    parts = urlsplit(url)
    assert parts.path == "/passkey-setup", url
    assert parts.fragment, url
    return parts.fragment
