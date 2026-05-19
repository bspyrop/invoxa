"""
utils/backfill_line_items.py

Re-processes existing Firestore invoices that have no line items, downloads
each file from Google Drive, runs GPT-4o vision extraction (line items only),
and saves the result to the line_items sub-collection.

Usage:
    python utils/backfill_line_items.py --uid <firebase_uid> [--dry-run]

The script handles Google authentication itself — no Streamlit needed.
It opens an OAuth2 URL in your browser; after you log in and grant access,
paste the "code" value from the redirect URL back into the terminal.
A token is saved to .backfill_token.json so you only need to log in once.

Safe to re-run — skips invoices that already have line_items_count > 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import webbrowser

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

_TOKEN_CACHE = os.path.join(_PROJECT_ROOT, ".backfill_token.json")

_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ---------------------------------------------------------------------------
# Secrets loader
# ---------------------------------------------------------------------------

def _load_secrets(secrets_file: str) -> dict:
    try:
        import tomllib
        with open(secrets_file, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import toml
        return toml.load(secrets_file)
    except ImportError:
        raise RuntimeError(
            "Install 'toml' (pip install toml) or use Python 3.11+ for built-in tomllib."
        )


# ---------------------------------------------------------------------------
# Google OAuth2 — installed-app style, no localhost server needed
# ---------------------------------------------------------------------------

def _build_auth_url(client_id: str, redirect_uri: str) -> str:
    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         " ".join(_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    import httpx
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          code,
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    import httpx
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "refresh_token": refresh_token,
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _get_google_credentials(client_id: str, client_secret: str, redirect_uri: str):
    """Return a google.oauth2.credentials.Credentials object, prompting OAuth2 if needed."""
    from google.oauth2.credentials import Credentials

    # Try cached token first
    if os.path.exists(_TOKEN_CACHE):
        with open(_TOKEN_CACHE) as f:
            cached = json.load(f)
        print("Refreshing cached Google token ...")
        try:
            access_token = _refresh_access_token(
                cached["refresh_token"], client_id, client_secret
            )
            return Credentials(
                token=access_token,
                refresh_token=cached["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=_SCOPES,
            )
        except Exception as exc:
            print(f"Token refresh failed ({exc}), re-authenticating ...")

    # No cached token — do the OAuth2 flow
    auth_url = _build_auth_url(client_id, redirect_uri)
    print("\nOpening browser for Google sign-in ...")
    print(f"\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print(
        "After you sign in, you will be redirected to a URL that looks like:\n"
        f"  {redirect_uri}?code=4%2F0A...&scope=...\n"
        "The page may show an error (that is fine — Streamlit is not running).\n"
        "Copy the value of the 'code' parameter from the URL and paste it here."
    )
    code = input("\nPaste the 'code' value: ").strip()

    # Strip full URL if user pasted the whole redirect URL instead of just the code
    if "code=" in code:
        parsed = urllib.parse.urlparse(code)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get("code", [code])[0]

    print("Exchanging code for tokens ...")
    tokens = _exchange_code(code, client_id, client_secret, redirect_uri)

    refresh_token = tokens.get("refresh_token", "")
    access_token  = tokens["access_token"]

    # Cache for next run
    with open(_TOKEN_CACHE, "w") as f:
        json.dump({"refresh_token": refresh_token}, f)
    print(f"Token cached at {_TOKEN_CACHE} — you won't need to log in again.\n")

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_SCOPES,
    )


# ---------------------------------------------------------------------------
# Firebase init (without st.secrets)
# ---------------------------------------------------------------------------

def _init_firebase(firebase_creds_dict: dict) -> None:
    import firebase_admin
    from firebase_admin import credentials
    try:
        firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(firebase_creds_dict)
        firebase_admin.initialize_app(cred)


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def backfill(uid: str, google_creds, openai_api_key: str, dry_run: bool = False) -> None:
    from firebase_admin import firestore as _fs
    from services.firestore import save_invoice_with_line_items
    from services.google_drive import download_file
    from agent.nodes.extract_data import extract_line_items_only

    db       = _fs.client()
    invoices = list(db.collection("users").document(uid).collection("invoices").stream())

    total, skipped, updated, failed = 0, 0, 0, 0

    for doc in invoices:
        total += 1
        data       = doc.to_dict()
        invoice_id = doc.id

        if (data.get("line_items_count") or 0) > 0:
            print(
                f"  SKIP   {data.get('supplier_name')} {data.get('invoice_date')}"
                f" — already has {data['line_items_count']} items"
            )
            skipped += 1
            continue

        drive_file_id = data.get("drive_file_id")
        if not drive_file_id:
            print(f"  SKIP   {invoice_id} — no drive_file_id")
            skipped += 1
            continue

        print(
            f"  PROCESS {data.get('supplier_name')} {data.get('invoice_date')} ...",
            end=" ", flush=True,
        )
        try:
            file_bytes = download_file(google_creds, drive_file_id)
            mime_type  = data.get("mime_type", "application/pdf")
            line_items = extract_line_items_only(file_bytes, mime_type, api_key=openai_api_key)

            if not dry_run:
                save_invoice_with_line_items(uid, invoice_id, data, line_items)

            print(f"OK — {len(line_items)} items found")
            updated += 1

        except Exception as exc:
            print(f"FAIL — {exc}")
            failed += 1

    print(
        f"\nBackfill complete: {total} total, "
        f"{updated} updated, {skipped} skipped, {failed} failed"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill line items for existing Invoxa invoices (no Streamlit needed)."
    )
    parser.add_argument("--uid", required=True, help="Firebase user UID to backfill")
    parser.add_argument(
        "--secrets-file",
        default=os.path.join(_PROJECT_ROOT, ".streamlit", "secrets.toml"),
        help="Path to secrets.toml (default: .streamlit/secrets.toml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print actions without writing to Firestore",
    )
    args = parser.parse_args()

    # Load secrets
    print(f"Loading secrets from {args.secrets_file} ...")
    secrets = _load_secrets(args.secrets_file)

    openai_api_key = secrets.get("OPENAI_API_KEY", "")
    if not openai_api_key:
        print("ERROR: OPENAI_API_KEY not found in secrets file.", file=sys.stderr)
        sys.exit(1)

    firebase_creds_dict = dict(secrets.get("firebase_admin", {}))
    if not firebase_creds_dict:
        print("ERROR: [firebase_admin] section not found in secrets file.", file=sys.stderr)
        sys.exit(1)

    client_id     = secrets.get("GOOGLE_CLIENT_ID", "")
    client_secret = secrets.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri  = secrets.get("GOOGLE_REDIRECT_URI", "")
    if not client_id or not client_secret or not redirect_uri:
        print("ERROR: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI missing.", file=sys.stderr)
        sys.exit(1)

    print("Initialising Firebase ...")
    _init_firebase(firebase_creds_dict)

    google_creds = _get_google_credentials(client_id, client_secret, redirect_uri)

    if args.dry_run:
        print("--- DRY RUN — no changes will be written ---\n")

    backfill(args.uid, google_creds, openai_api_key, dry_run=args.dry_run)
