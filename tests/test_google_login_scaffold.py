"""Contract tests for the Google sign-in scaffold and lab membership rules.

These are **contract tests against fakes**, not live validation. No request in
this suite reaches Google, no credential exists here to make one with, and
nothing in this file may be cited as evidence that production Google login
works. What they do establish is that the flow refuses everything it should
refuse before anyone points it at a real client ID.
"""

from __future__ import annotations

import unittest

from voice_workflow_agent.google_login import (
    GOOGLE_SCOPES,
    REQUIRED_ENVIRONMENT,
    SESSION_COOKIE_NAME,
    ChallengeStore,
    GoogleLoginConfigurationError,
    GoogleLoginRejected,
    GoogleLoginSettings,
    LabInvitation,
    VerifiedGoogleIdentity,
    begin_login,
    complete_login,
    issue_session_cookie,
    new_session_token,
    resolve_lab_membership,
)
from voice_workflow_agent.identity import Role


CLIENT_ID = "1234567890-example.apps.googleusercontent.com"


def settings(**overrides) -> GoogleLoginSettings:
    base = {
        "enabled": True,
        "client_id": CLIENT_ID,
        "redirect_uri": "https://lab.example.org/auth/google/callback",
    }
    base.update(overrides)
    return GoogleLoginSettings(**base)


def claims(**overrides) -> dict:
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "108000000000000000001",
        "email": "researcher@example.org",
        "email_verified": True,
        "name": "김연구",
    }
    base.update(overrides)
    return base


class ConfigurationTests(unittest.TestCase):
    def test_the_feature_is_off_unless_explicitly_enabled(self):
        self.assertFalse(GoogleLoginSettings.from_environment({}).enabled)

    def test_enabling_without_a_client_id_fails_at_configuration_time(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            GoogleLoginSettings.from_environment(
                {"VOICE_WORKFLOW_AGENT_GOOGLE_LOGIN_ENABLED": "1"})

    def test_a_plain_http_redirect_is_refused_outside_localhost(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            settings(redirect_uri="http://lab.example.org/callback").validate()

    def test_localhost_http_is_allowed_only_without_a_secure_cookie(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            settings(redirect_uri="http://localhost:8000/cb").validate()
        settings(
            redirect_uri="http://localhost:8000/cb",
            require_secure_cookie=False,
        ).validate()

    def test_a_malformed_allowed_domain_is_refused(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            settings(allowed_hosted_domains=("not a domain",)).validate()

    def test_the_client_secret_is_read_from_the_environment_not_stored(self):
        configured = settings()
        # The variable *name* is part of the configuration contract; the value
        # must never be captured on the object and so can never reach a repr,
        # a log line or a crash dump through it.
        self.assertIn(
            "GOOGLE_OIDC_CLIENT_SECRET",
            configured.client_secret_environment_variable)
        self.assertNotIn("s3cret", repr(configured))
        with self.assertRaises(GoogleLoginConfigurationError):
            configured.client_secret({})
        self.assertEqual(
            configured.client_secret({"GOOGLE_OIDC_CLIENT_SECRET": "s3cret"}),
            "s3cret",
        )

    def test_every_required_variable_is_documented_for_the_runbook(self):
        names = {name for name, _ in REQUIRED_ENVIRONMENT}
        self.assertIn("GOOGLE_OIDC_CLIENT_ID", names)
        self.assertIn("GOOGLE_OIDC_CLIENT_SECRET", names)
        self.assertIn("GOOGLE_OIDC_REDIRECT_URI", names)


class AuthorizationRequestTests(unittest.TestCase):
    def test_the_authorization_url_carries_state_nonce_and_pkce(self):
        challenge = begin_login(settings(), now=0.0)
        url = challenge.authorization_url
        self.assertTrue(url.startswith("https://accounts.google.com/"))
        self.assertIn(f"state={challenge.state}", url)
        self.assertIn(f"nonce={challenge.nonce}", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn(f"code_challenge={challenge.code_challenge}", url)
        self.assertNotIn(challenge.code_verifier, url)

    def test_only_identity_scopes_are_requested(self):
        self.assertEqual(set(GOOGLE_SCOPES), {"openid", "email", "profile"})

    def test_state_and_nonce_are_unpredictable_and_never_reused(self):
        seen = {
            (c.state, c.nonce, c.code_verifier)
            for c in (begin_login(settings(), now=0.0) for _ in range(20))
        }
        self.assertEqual(len(seen), 20)
        for state, nonce, verifier in seen:
            self.assertGreaterEqual(len(state), 40)
            self.assertGreaterEqual(len(nonce), 40)
            self.assertGreaterEqual(len(verifier), 80)

    def test_a_disabled_flow_cannot_start_a_login(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            begin_login(settings(enabled=False), now=0.0)


class CallbackValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = settings()
        self.challenge = begin_login(self.settings, now=0.0)
        self.exchanged: list[dict] = []

    def exchange(self, endpoint, payload):
        self.exchanged.append(dict(payload))
        return {"id_token": "fake.id.token", "access_token": "fake-access"}

    def complete(self, *, token_claims=None, **overrides):
        arguments = {
            "returned_state": self.challenge.state,
            "code": "auth-code",
            "now": 1.0,
            "exchange": self.exchange,
            "verify_id_token": lambda _token: (
                token_claims if token_claims is not None
                else claims(nonce=self.challenge.nonce)),
            "environment": {"GOOGLE_OIDC_CLIENT_SECRET": "s3cret"},
        }
        arguments.update(overrides)
        return complete_login(self.settings, self.challenge, **arguments)

    def test_a_valid_callback_returns_the_verified_identity(self):
        identity = self.complete()
        self.assertEqual(identity.email, "researcher@example.org")
        self.assertEqual(identity.display_name, "김연구")
        self.assertTrue(identity.email_verified)

    def test_the_code_exchange_is_server_side_and_carries_the_pkce_verifier(self):
        self.complete()
        payload = self.exchanged[0]
        self.assertEqual(payload["grant_type"], "authorization_code")
        self.assertEqual(payload["code_verifier"], self.challenge.code_verifier)
        self.assertEqual(payload["client_secret"], "s3cret")
        self.assertEqual(payload["redirect_uri"], self.settings.redirect_uri)

    def test_a_mismatched_state_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(returned_state="attacker-supplied")

    def test_a_missing_state_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(returned_state="")

    def test_an_expired_challenge_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(now=self.settings.challenge_lifetime_seconds + 10)

    def test_a_mismatched_nonce_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(token_claims=claims(nonce="different"))

    def test_a_foreign_issuer_or_audience_is_refused(self):
        for override in (
            {"iss": "https://accounts.example.com"},
            {"aud": "some-other-client-id"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(GoogleLoginRejected):
                    self.complete(
                        token_claims=claims(
                            nonce=self.challenge.nonce, **override))

    def test_an_unverified_email_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(
                token_claims=claims(
                    nonce=self.challenge.nonce, email_verified=False))

    def test_an_account_outside_the_allowed_domain_is_refused(self):
        self.settings = settings(allowed_hosted_domains=("example.org",))
        self.challenge = begin_login(self.settings, now=0.0)
        with self.assertRaises(GoogleLoginRejected):
            self.complete(
                token_claims=claims(
                    nonce=self.challenge.nonce, hd="other.example.com"))
        identity = self.complete(
            token_claims=claims(nonce=self.challenge.nonce, hd="example.org"))
        self.assertEqual(identity.hosted_domain, "example.org")

    def test_a_token_response_without_an_id_token_is_refused(self):
        with self.assertRaises(GoogleLoginRejected):
            self.complete(exchange=lambda endpoint, payload: {"access_token": "a"})

    def test_a_replayed_callback_cannot_reuse_a_consumed_challenge(self):
        store = ChallengeStore()
        store.remember(self.challenge)
        self.assertIsNotNone(store.take(self.challenge.state))
        self.assertIsNone(store.take(self.challenge.state))

    def test_expired_challenges_are_purged(self):
        store = ChallengeStore()
        store.remember(self.challenge)
        self.assertEqual(store.purge_expired(0.0, self.settings), 0)
        self.assertEqual(
            store.purge_expired(
                self.settings.challenge_lifetime_seconds + 1, self.settings),
            1,
        )
        self.assertFalse(store.challenges)


class LabMembershipTests(unittest.TestCase):
    def identity(self, email="researcher@example.org") -> VerifiedGoogleIdentity:
        return VerifiedGoogleIdentity(
            subject="sub-1", email=email, email_verified=True,
            display_name="김연구")

    def test_signing_in_is_not_joining(self):
        resolution = resolve_lab_membership(
            self.identity(), (), settings())
        self.assertFalse(resolution.granted)
        self.assertEqual(resolution.reason, "no_invitation")
        self.assertIn("연구실 관리자에게 초대를 요청", resolution.message)
        self.assertIsNone(resolution.lab_id)

    def test_an_invited_member_enters_their_lab_with_the_invited_role(self):
        resolution = resolve_lab_membership(
            self.identity(),
            (LabInvitation("researcher@example.org", "lab-1", Role.REVIEWER),),
            settings(),
        )
        self.assertTrue(resolution.granted)
        self.assertEqual(resolution.lab_id, "lab-1")
        self.assertIs(resolution.role, Role.REVIEWER)

    def test_a_member_cannot_select_a_lab_they_were_not_invited_to(self):
        resolution = resolve_lab_membership(
            self.identity(),
            (LabInvitation("researcher@example.org", "lab-1"),),
            settings(),
            requested_lab_id="lab-2",
        )
        self.assertFalse(resolution.granted)
        self.assertEqual(resolution.reason, "lab_not_authorized")

    def test_a_member_of_two_labs_can_choose_between_them(self):
        invitations = (
            LabInvitation("researcher@example.org", "lab-1"),
            LabInvitation("researcher@example.org", "lab-2", Role.LAB_ADMIN),
        )
        chosen = resolve_lab_membership(
            self.identity(), invitations, settings(), requested_lab_id="lab-2")
        self.assertTrue(chosen.granted)
        self.assertIs(chosen.role, Role.LAB_ADMIN)

    def test_self_service_lab_creation_is_not_implemented_for_the_pilot(self):
        resolution = resolve_lab_membership(
            self.identity(), (),
            settings(allow_self_service_membership=True))
        self.assertFalse(resolution.granted)
        self.assertEqual(resolution.reason, "self_service_not_implemented")

    def test_roles_reuse_the_existing_authorization_model(self):
        for invitation_role in (Role.RESEARCHER, Role.REVIEWER, Role.LAB_ADMIN):
            with self.subTest(role=invitation_role):
                resolution = resolve_lab_membership(
                    self.identity(),
                    (LabInvitation(
                        "researcher@example.org", "lab-1", invitation_role),),
                    settings(),
                )
                self.assertIs(resolution.role, invitation_role)


class SessionCookieTests(unittest.TestCase):
    def test_the_session_cookie_is_http_only_same_site_and_secure(self):
        cookie = issue_session_cookie(
            settings(), session_token=new_session_token())
        header = cookie.header_value()
        self.assertTrue(header.startswith(f"{SESSION_COOKIE_NAME}="))
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertIn("Secure", header)
        self.assertIn("Max-Age=", header)

    def test_secure_is_dropped_only_when_explicitly_configured_for_local_http(self):
        cookie = issue_session_cookie(
            settings(
                redirect_uri="http://localhost:8000/cb",
                require_secure_cookie=False),
            session_token=new_session_token(),
        )
        self.assertNotIn("Secure", cookie.header_value())
        self.assertIn("HttpOnly", cookie.header_value())

    def test_a_weak_session_token_is_refused(self):
        with self.assertRaises(GoogleLoginConfigurationError):
            issue_session_cookie(settings(), session_token="short")

    def test_session_tokens_are_unpredictable(self):
        self.assertEqual(len({new_session_token() for _ in range(50)}), 50)

    def test_no_provider_token_is_ever_handed_to_the_browser(self):
        """The cookie carries our own opaque session id, never Google's tokens."""

        token = new_session_token()
        cookie = issue_session_cookie(settings(), session_token=token)
        self.assertEqual(cookie.value, token)
        self.assertNotIn("id_token", cookie.header_value())
        self.assertNotIn("access_token", cookie.header_value())


if __name__ == "__main__":  # pragma: no cover - manual runs only
    unittest.main()
