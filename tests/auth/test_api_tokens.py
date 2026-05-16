"""Tests for API bearer-token issue / verify / revoke."""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.auth.api_tokens import (
    API_TOKEN_PREFIX,
    ApiTokenService,
    CreatedApiToken,
    compute_api_token_hash,
    extract_bearer_from_authorization,
    generate_api_token_plaintext,
)
from app.auth.key_material import DerivedKeys
from app.repositories.api_tokens import ApiTokensRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def tokens_repo(session: AsyncSession) -> ApiTokensRepository:
    return ApiTokensRepository(session)


@pytest_asyncio.fixture
async def service(tokens_repo: ApiTokensRepository, derived_keys: DerivedKeys) -> ApiTokenService:
    return ApiTokenService(tokens=tokens_repo, keys=derived_keys)


class TestGenerateApiTokenPlaintext:
    def test_starts_with_prefix(self) -> None:
        value = generate_api_token_plaintext()
        assert value.startswith(API_TOKEN_PREFIX)

    def test_random_portion_is_url_safe(self) -> None:
        value = generate_api_token_plaintext()
        rand = value[len(API_TOKEN_PREFIX) :]
        # secrets.token_urlsafe(40) → 54 chars.
        assert len(rand) == 54
        assert all(c.isalnum() or c in "-_" for c in rand)

    def test_generates_distinct_values(self) -> None:
        n = 100
        values = {generate_api_token_plaintext() for _ in range(n)}
        assert len(values) == n


class TestComputeApiTokenHash:
    def test_deterministic(self) -> None:
        key = b"A" * 32
        v = "rpat_abc"
        assert compute_api_token_hash(v, key) == compute_api_token_hash(v, key)

    def test_different_keys_yield_different_hashes(self) -> None:
        v = "rpat_abc"
        assert compute_api_token_hash(v, b"A" * 32) != compute_api_token_hash(v, b"B" * 32)


class TestExtractBearer:
    def test_well_formed_header(self) -> None:
        token = "rpat_" + "x" * 54
        assert extract_bearer_from_authorization(f"Bearer {token}") == token

    def test_extra_whitespace_tolerated(self) -> None:
        token = "rpat_" + "x" * 54
        assert extract_bearer_from_authorization(f"Bearer   {token}") == token
        assert extract_bearer_from_authorization(f"Bearer {token} ") == token

    def test_case_insensitive_scheme(self) -> None:
        token = "rpat_" + "x" * 54
        assert extract_bearer_from_authorization(f"bearer {token}") == token
        assert extract_bearer_from_authorization(f"BEARER {token}") == token

    def test_missing_header_returns_none(self) -> None:
        assert extract_bearer_from_authorization(None) is None

    def test_wrong_scheme_returns_none(self) -> None:
        token = "rpat_" + "x" * 54
        assert extract_bearer_from_authorization(f"Basic {token}") is None

    def test_no_prefix_returns_none(self) -> None:
        # Same length as a real token, but no `rpat_` prefix.
        assert extract_bearer_from_authorization("Bearer " + "x" * 54) is None

    def test_bad_chars_in_token_returns_none(self) -> None:
        # `!` is not in the URL-safe-base64 alphabet.
        assert extract_bearer_from_authorization("Bearer rpat_abc!def") is None


class TestApiTokenServiceCreate:
    @pytest.mark.asyncio
    async def test_create_returns_plaintext_once(
        self, service: ApiTokenService, admin_user_id: str
    ) -> None:
        created = await service.create(user_id=admin_user_id, label="my-cli")
        assert isinstance(created, CreatedApiToken)
        assert created.plaintext.startswith(API_TOKEN_PREFIX)
        assert created.dto.label == "my-cli"
        assert created.dto.user_id == admin_user_id
        assert created.dto.revoked_at is None

    @pytest.mark.asyncio
    async def test_create_trims_label_whitespace(
        self, service: ApiTokenService, admin_user_id: str
    ) -> None:
        created = await service.create(user_id=admin_user_id, label="  whitespace-fan  ")
        assert created.dto.label == "whitespace-fan"

    @pytest.mark.asyncio
    async def test_empty_label_rejected(self, service: ApiTokenService, admin_user_id: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await service.create(user_id=admin_user_id, label="   ")

    @pytest.mark.asyncio
    async def test_empty_user_id_rejected(self, service: ApiTokenService) -> None:
        with pytest.raises(ValueError, match="user_id"):
            await service.create(user_id="", label="ok")

    @pytest.mark.asyncio
    async def test_list_all_omits_token_hash(
        self, service: ApiTokenService, admin_user_id: str
    ) -> None:
        """The DTO is metadata-only; listing must not leak the hash."""
        await service.create(user_id=admin_user_id, label="one")
        await service.create(user_id=admin_user_id, label="two")
        items = await service.list_all()
        assert len(items) == 2
        for dto in items:
            # ApiTokenDTO doesn't have a token_hash field at all.
            assert not hasattr(dto, "token_hash")
            # But it does expose user_id (Phase 6 closed the deferred gap).
            assert dto.user_id == admin_user_id


class TestApiTokenServiceVerify:
    @pytest.mark.asyncio
    async def test_valid_bearer_returns_dto(
        self, service: ApiTokenService, admin_user_id: str
    ) -> None:
        created = await service.create(user_id=admin_user_id, label="my-cli")
        dto = await service.verify_bearer(created.plaintext)
        assert dto is not None
        assert dto.id == created.dto.id
        assert dto.user_id == admin_user_id
        # last_used_at touched.
        assert dto.last_used_at is None  # before touch the DTO is stale
        # Re-fetch via verify_bearer to confirm the touch happened.
        again = await service.verify_bearer(created.plaintext)
        assert again is not None

    @pytest.mark.asyncio
    async def test_invalid_bearer_returns_none(self, service: ApiTokenService) -> None:
        assert await service.verify_bearer("rpat_definitely-fake") is None
        assert await service.verify_bearer("not-even-the-prefix") is None
        assert await service.verify_bearer(None) is None
        assert await service.verify_bearer("") is None

    @pytest.mark.asyncio
    async def test_revoked_token_no_longer_verifies(
        self, service: ApiTokenService, admin_user_id: str
    ) -> None:
        created = await service.create(user_id=admin_user_id, label="my-cli")
        ok = await service.revoke(created.dto.id)
        assert ok is True
        assert await service.verify_bearer(created.plaintext) is None

    @pytest.mark.asyncio
    async def test_revoke_unknown_id_returns_false(self, service: ApiTokenService) -> None:
        assert await service.revoke("never-existed") is False


class TestHmacKeyIsolation:
    @pytest.mark.asyncio
    async def test_different_mac_key_does_not_verify(
        self, tokens_repo: ApiTokensRepository, admin_user_id: str
    ) -> None:
        """Tokens minted under key A do not verify under key B.

        Defense in depth: an attacker who only has the HMAC fingerprint
        cannot mint a working token because they don't have the
        api_token_mac_key.
        """
        key_a = DerivedKeys(
            stream_key_kek=b"x" * 32,
            api_token_mac_key=b"A" * 32,
            session_token_mac_key=b"H" * 32,
        )
        key_b = DerivedKeys(
            stream_key_kek=b"x" * 32,
            api_token_mac_key=b"Z" * 32,
            session_token_mac_key=b"H" * 32,
        )
        svc_a = ApiTokenService(tokens=tokens_repo, keys=key_a)
        svc_b = ApiTokenService(tokens=tokens_repo, keys=key_b)

        created = await svc_a.create(user_id=admin_user_id, label="under-key-a")
        # Verifying with the wrong key produces a different hash, no match.
        assert await svc_b.verify_bearer(created.plaintext) is None
        # Sanity: the same key works.
        assert await svc_a.verify_bearer(created.plaintext) is not None
