"""
Firebase Authentication module.

Handles Google Sign-In via Firebase Auth (pyrebase4 for client-side flow,
firebase-admin for server-side ID token verification).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import firebase_admin
import pyrebase
import streamlit as st
from firebase_admin import auth as admin_auth
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase initialisation (idempotent — safe to call multiple times)
# ---------------------------------------------------------------------------

def _get_admin_app() -> firebase_admin.App:
    """Return the default Firebase Admin app, initialising it on first call."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        cred_dict = dict(st.secrets["firebase_admin"])
        cred = credentials.Certificate(cred_dict)
        return firebase_admin.initialize_app(cred)


def _get_pyrebase_auth():
    """Return a pyrebase Auth instance for client-side sign-in flows."""
    config = {
        "apiKey":            st.secrets["FIREBASE_API_KEY"],
        "authDomain":        st.secrets["FIREBASE_AUTH_DOMAIN"],
        "projectId":         st.secrets["FIREBASE_PROJECT_ID"],
        "storageBucket":     st.secrets["FIREBASE_STORAGE_BUCKET"],
        "messagingSenderId": st.secrets["FIREBASE_MESSAGING_SENDER_ID"],
        "appId":             st.secrets["FIREBASE_APP_ID"],
        "databaseURL":       "",   # not used but pyrebase requires the key
    }
    pb = pyrebase.initialize_app(config)
    return pb.auth()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_id_token(id_token: str) -> Optional[dict]:
    """
    Verify a Firebase ID token and return its decoded claims.

    Args:
        id_token: JWT ID token issued by Firebase Auth.

    Returns:
        Decoded token claims dict, or None if verification fails.
    """
    try:
        _get_admin_app()
        decoded = admin_auth.verify_id_token(id_token)
        return decoded
    except Exception as exc:
        logger.error("ID token verification failed: %s", exc)
        return None


def sign_in_with_email_password(email: str, password: str) -> Optional[dict]:
    """
    Sign in a user with email + password via Firebase Auth REST API.

    Returns the pyrebase user dict on success, None on failure.
    """
    try:
        pb_auth = _get_pyrebase_auth()
        user = pb_auth.sign_in_with_email_and_password(email, password)
        return user
    except Exception as exc:
        logger.error("Email/password sign-in failed: %s", exc)
        return None


def get_google_sign_in_url() -> str:
    """
    Build the Google OAuth2 authorisation URL for Firebase / Google Sign-In.

    The user is redirected here; after consent Google redirects back to
    GOOGLE_REDIRECT_URI with ?code=... which we exchange for tokens.
    """
    client_id    = st.secrets["GOOGLE_CLIENT_ID"]
    redirect_uri = st.secrets["GOOGLE_REDIRECT_URI"]
    scope        = "openid email profile https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/spreadsheets"

    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        f"&scope={scope}"
        "&access_type=offline"
        "&prompt=consent"
    )
    return url


def exchange_code_for_tokens(code: str) -> Optional[dict]:
    """
    Exchange an OAuth2 authorisation code for access + refresh tokens.

    Args:
        code: The authorisation code from Google's redirect.

    Returns:
        Token response dict with keys: access_token, refresh_token,
        id_token, expires_in — or None on error.
    """
    import httpx

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code":          code,
        "client_id":     st.secrets["GOOGLE_CLIENT_ID"],
        "client_secret": st.secrets["GOOGLE_CLIENT_SECRET"],
        "redirect_uri":  st.secrets["GOOGLE_REDIRECT_URI"],
        "grant_type":    "authorization_code",
    }
    try:
        resp = httpx.post(token_url, data=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        return None


def get_google_user_info(access_token: str) -> Optional[dict]:
    """
    Fetch the authenticated user's profile from Google's userinfo endpoint.

    Returns a dict with: sub, email, name, picture — or None on error.
    """
    import httpx

    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch Google user info: %s", exc)
        return None


def create_or_update_user_profile(
    uid: str,
    email: str,
    display_name: str,
    photo_url: str,
    is_new_user: bool = False,
) -> None:
    """
    Create (first login) or update (subsequent logins) a user profile in Firestore.

    Args:
        uid:          Firebase UID.
        email:        User's email address.
        display_name: User's full name.
        photo_url:    URL of the user's Google profile photo.
        is_new_user:  If True, set createdAt; always update lastLogin.
    """
    try:
        _get_admin_app()
        db = firestore.client()
        user_ref = db.collection("users").document(uid)
        now = datetime.now(timezone.utc).isoformat()

        doc = user_ref.get()
        if not doc.exists or is_new_user:
            user_ref.set(
                {
                    "uid":         uid,
                    "email":       email,
                    "displayName": display_name,
                    "photoURL":    photo_url,
                    "createdAt":   now,
                    "lastLogin":   now,
                },
                merge=True,
            )
        else:
            user_ref.update({"lastLogin": now, "displayName": display_name})
    except Exception as exc:
        logger.error("Failed to create/update user profile: %s", exc)


def get_current_user() -> Optional[dict]:
    """
    Return the currently authenticated user from Streamlit session state.

    Returns a dict with keys: uid, email, displayName, photoURL,
    access_token, id_token — or None if not authenticated.
    """
    return st.session_state.get("user")


def sign_out() -> None:
    """Clear authentication data from Streamlit session state."""
    for key in ("user", "id_token", "access_token", "refresh_token", "google_credentials"):
        st.session_state.pop(key, None)


def is_authenticated() -> bool:
    """Return True if a user is currently signed in."""
    return st.session_state.get("user") is not None


def build_google_credentials_from_token(access_token: str, refresh_token: str):
    """
    Build a google.oauth2.credentials.Credentials object from stored tokens.
    Used by Drive and Sheets service wrappers.

    Args:
        access_token:  OAuth2 access token.
        refresh_token: OAuth2 refresh token.

    Returns:
        google.oauth2.credentials.Credentials instance.
    """
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["GOOGLE_CLIENT_ID"],
        client_secret=st.secrets["GOOGLE_CLIENT_SECRET"],
    )


# ---------------------------------------------------------------------------
# Streamlit login page renderer
# ---------------------------------------------------------------------------

def render_login_page() -> None:
    """
    Render the full-page login UI.

    Checks for an OAuth2 ?code= query parameter on load; if present,
    exchanges it for tokens and completes the sign-in flow automatically.
    """
    # ---- Handle OAuth2 callback ----
    params = st.query_params
    if "code" in params:
        code = params["code"]
        _handle_oauth_callback(code)
        return

    # ---- Login UI ----
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image(
            "https://fonts.gstatic.com/s/i/productlogos/drive_2020q4/v8/web-64dp/logo_drive_2020q4_color_2x_web_64dp.png",
            width=64,
        )
        st.title("Invoxa")
        st.caption("AI-powered expense invoice management")
        st.markdown("---")
        st.markdown("#### Sign in to continue")

        sign_in_url = get_google_sign_in_url()
        st.markdown(
            f"""
            <a href="{sign_in_url}" target="_self">
                <button style="
                    background-color: #4285F4;
                    color: white;
                    border: none;
                    padding: 12px 24px;
                    font-size: 16px;
                    border-radius: 6px;
                    cursor: pointer;
                    width: 100%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                ">
                    Sign in with Google
                </button>
            </a>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
        st.caption(
            "By signing in you grant Invoxa read/write access to your "
            "Google Drive (Expenses folder) and Google Sheets."
        )


def _handle_oauth_callback(code: str) -> None:
    """
    Complete the OAuth2 sign-in flow after Google redirects back with a code.

    Exchanges the code for tokens, fetches user info, creates the Firestore
    profile, and writes everything to session state.
    """
    with st.spinner("Signing you in…"):
        tokens = exchange_code_for_tokens(code)
        if not tokens:
            st.error("Authentication failed — could not exchange code for tokens. Please try again.")
            st.query_params.clear()
            return

        access_token  = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        id_token      = tokens.get("id_token", "")

        user_info = get_google_user_info(access_token)
        if not user_info:
            st.error("Could not fetch your Google profile. Please try again.")
            st.query_params.clear()
            return

        uid          = user_info.get("sub", "")
        email        = user_info.get("email", "")
        display_name = user_info.get("name", "")
        photo_url    = user_info.get("picture", "")

        # Persist profile in Firestore
        create_or_update_user_profile(uid, email, display_name, photo_url)

        # Write to session state
        st.session_state["user"] = {
            "uid":         uid,
            "email":       email,
            "displayName": display_name,
            "photoURL":    photo_url,
            "access_token":  access_token,
            "refresh_token": refresh_token,
            "id_token":      id_token,
        }
        st.session_state["access_token"]  = access_token
        st.session_state["refresh_token"] = refresh_token
        st.session_state["id_token"]      = id_token

        # Store google credentials for API calls
        st.session_state["google_credentials"] = build_google_credentials_from_token(
            access_token, refresh_token
        )

        # Remove the code from the URL so a refresh doesn't re-trigger
        st.query_params.clear()
        st.rerun()
