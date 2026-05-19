"""
utils/backfill_chroma.py

Index all existing Firestore invoices into ChromaDB (text-embedding-3-small).
Run this after the initial ChromaDB setup or whenever the local index is lost.

Usage:
    python utils/backfill_chroma.py --uid <firebase_uid> [--dry-run] [--reset]

Flags:
    --dry-run   Print what would be indexed without writing to ChromaDB.
    --reset     Delete the existing collection first, then re-index everything.
                Without this flag, already-indexed invoices are skipped.

Safe to re-run without --reset: invoices already in ChromaDB are skipped.
"""

from __future__ import annotations

import argparse
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Secrets loader (mirrors backfill_line_items.py)
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
# Firebase init without Streamlit
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

def backfill(
    uid: str,
    openai_api_key: str,
    dry_run: bool = False,
    reset: bool = False,
) -> None:
    import os
    os.environ.setdefault("OPENAI_API_KEY", openai_api_key)

    from firebase_admin import firestore as _fs
    from services.chroma_service import (
        get_chroma_client,
        get_collection_count,
        index_invoice,
        is_indexed,
    )

    if reset:
        print("--reset: deleting existing ChromaDB collection ...")
        try:
            get_chroma_client().delete_collection(f"invoices_{uid}")
            print("  Collection deleted.")
        except Exception as exc:
            print(f"  Could not delete collection (may not exist): {exc}")

    db       = _fs.client()
    inv_docs = list(db.collection("users").document(uid).collection("invoices").stream())
    total    = len(inv_docs)

    if total == 0:
        print("No invoices found for this user — nothing to index.")
        return

    print(f"Found {total} invoice(s) in Firestore.\n")

    indexed, skipped, failed = 0, 0, 0

    for doc in inv_docs:
        data       = doc.to_dict()
        invoice_id = doc.id
        supplier   = data.get("supplier_name", "?")
        inv_date   = data.get("invoice_date", "?")
        file_id    = data.get("drive_file_id", "")

        if not dry_run and not reset and is_indexed(uid, file_id or invoice_id):
            print(f"  SKIP   {supplier} {inv_date} — already indexed")
            skipped += 1
            continue

        # Load line items from sub-collection
        line_items: list = []
        if (data.get("line_items_count") or 0) > 0 and file_id:
            try:
                items_ref = (
                    db.collection("users")
                    .document(uid)
                    .collection("invoices")
                    .document(invoice_id)
                    .collection("line_items")
                )
                line_items = [d.to_dict() for d in items_ref.stream()]
            except Exception as exc:
                print(f"  WARN   Could not load line items for {supplier} {inv_date}: {exc}")

        print(
            f"  {'DRY-RUN' if dry_run else 'INDEX  '} {supplier} {inv_date}"
            f" — {len(line_items)} line item(s) ...",
            end=" ", flush=True,
        )

        if dry_run:
            print("OK (skipped)")
            indexed += 1
            continue

        try:
            # Ensure drive_file_id key is present (used as invoice_id in ChromaDB)
            invoice = {**data}
            if not invoice.get("drive_file_id") and file_id:
                invoice["drive_file_id"] = file_id

            chunks = index_invoice(uid, invoice, line_items)
            print(f"OK — {chunks} chunk(s) written")
            indexed += 1
        except Exception as exc:
            print(f"FAIL — {exc}")
            failed += 1

    final_count = get_collection_count(uid) if not dry_run else "N/A"
    print(
        f"\nBackfill complete: {total} total, "
        f"{indexed} indexed, {skipped} skipped, {failed} failed"
        f"\nChromaDB collection now has {final_count} chunk(s)."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill existing Firestore invoices into ChromaDB (no Streamlit needed)."
    )
    parser.add_argument("--uid", required=True, help="Firebase user UID to backfill")
    parser.add_argument(
        "--secrets-file",
        default=os.path.join(_PROJECT_ROOT, ".streamlit", "secrets.toml"),
        help="Path to secrets.toml (default: .streamlit/secrets.toml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print actions without writing to ChromaDB",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete the existing collection and re-index everything from scratch",
    )
    args = parser.parse_args()

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

    print("Initialising Firebase ...")
    _init_firebase(firebase_creds_dict)

    if args.dry_run:
        print("--- DRY RUN — no changes will be written ---\n")

    backfill(args.uid, openai_api_key, dry_run=args.dry_run, reset=args.reset)
