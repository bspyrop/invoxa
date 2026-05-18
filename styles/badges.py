def badge(label: str, variant: str = "default") -> str:
    """
    Returns an HTML badge string for use with st.markdown(..., unsafe_allow_html=True).

    variant options:
      "success"  — Processed, Imported, Confirmed
      "warning"  — Review, Pending, HITL
      "danger"   — Anomaly, Error, Overdue
      "info"     — via Gmail, Manual, Draft
      "default"  — neutral labels
    """
    styles = {
        "success": "background:#E6FAF5;color:#0A4A38;border:0.5px solid #B3EDD9",
        "warning": "background:#FEF3CD;color:#7A4F00;border:0.5px solid #F59E0B",
        "danger":  "background:#FDE8E8;color:#9A1F1F;border:0.5px solid #E55353",
        "info":    "background:#EEF2FF;color:#2B3A6B;border:0.5px solid #CBD5E8",
        "default": "background:#F8F9FC;color:#6B7A99;border:0.5px solid #E2E6EF",
    }
    style = styles.get(variant, styles["default"])
    return (
        f'<span style="{style};font-size:11px;font-weight:500;'
        f'padding:3px 9px;border-radius:20px;white-space:nowrap;">'
        f"{label}</span>"
    )
