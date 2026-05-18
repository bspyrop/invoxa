import streamlit as st

_CSS = """<style>
/* ── Page chrome ── */
[data-testid="stHeader"] {
    border-bottom: 0.5px solid #E2E6EF;
}
footer {
    display: none !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #1B2A4A !important;
    border-right: none;
}
[data-testid="stSidebar"] * {
    color: #8FA3C7 !important;
}

/* ── Sidebar collapse / reopen toggle ── */
/* Style only — let Streamlit control display/visibility */
[data-testid="collapsedControl"] {
    background-color: #1B2A4A !important;
    border-radius: 0 6px 6px 0 !important;
}
[data-testid="collapsedControl"] button,
[data-testid="collapsedControl"] svg {
    color: #FFFFFF !important;
    fill: #FFFFFF !important;
    stroke: #FFFFFF !important;
}
[data-testid="stSidebarCollapseButton"] button {
    color: #8FA3C7 !important;
}
[data-testid="stSidebar"] .st-emotion-cache-1cypcdb,
[data-testid="stSidebar"] a {
    color: #8FA3C7 !important;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 14px;
}
[data-testid="stSidebar"] a:hover {
    background: #243656 !important;
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] [aria-current="page"] {
    background: #243656 !important;
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-baseweb="radio"] {
    padding: 3px 10px;
    border-radius: 6px;
    cursor: pointer;
}
[data-testid="stSidebar"] [data-baseweb="radio"]:has(input:checked) {
    background: #243656;
}
[data-testid="stSidebar"] [data-baseweb="radio"]:hover {
    background: rgba(36, 54, 86, 0.6);
}

/* ── HR divider ── */
hr {
    border: none !important;
    border-top: 0.5px solid #E2E6EF !important;
    margin: 12px 0 20px !important;
}

/* ── Typography ── */
h1, h2, h3 {
    color: #1B2A4A !important;
    font-weight: 600;
    letter-spacing: -0.3px;
}
h1 { font-size: 22px; }
h2 { font-size: 18px; }
h3 { font-size: 15px; }
p, label, span, div {
    font-size: 14px;
    color: #1B2A4A;
    line-height: 1.6;
}

/* ── Buttons (Streamlit 1.32+) ── */
/* The broad "p, span, div { color: #1B2A4A }" rule overrides inherited color
   on child nodes, so we must target child elements explicitly. */
[data-testid="stBaseButton-primary"] {
    background: #1B2A4A !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 7px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 18px !important;
    transition: background 0.15s;
}
[data-testid="stBaseButton-primary"] p,
[data-testid="stBaseButton-primary"] span,
[data-testid="stBaseButton-primary"] div {
    color: #FFFFFF !important;
}
[data-testid="stBaseButton-primary"]:hover {
    background: #243656 !important;
}
/* Secondary / outline */
[data-testid="stBaseButton-secondary"] {
    background: #FFFFFF !important;
    color: #1B2A4A !important;
    border: 1px solid #CBD5E8 !important;
    border-radius: 7px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 18px !important;
}
[data-testid="stBaseButton-secondary"] p,
[data-testid="stBaseButton-secondary"] span,
[data-testid="stBaseButton-secondary"] div {
    color: #1B2A4A !important;
}
[data-testid="stBaseButton-secondary"]:hover {
    background: #F8F9FC !important;
    border-color: #1B2A4A !important;
}
/* ── Inputs ── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background: #FFFFFF !important;
    border: 0.5px solid #E2E6EF !important;
    border-radius: 7px !important;
    color: #1B2A4A !important;
    font-size: 14px !important;
    padding: 9px 12px !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
    border-color: #2DD4A7 !important;
    box-shadow: 0 0 0 3px rgba(45, 212, 167, 0.15) !important;
    outline: none !important;
}

/* ── Selectbox (Streamlit uses baseweb custom dropdown, not native <select>) ── */
[data-testid="stSelectbox"] [data-baseweb="select"] {
    background: #FFFFFF !important;
    border-radius: 7px !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child {
    background: #FFFFFF !important;
    border: 0.5px solid #E2E6EF !important;
    border-radius: 7px !important;
    color: #1B2A4A !important;
    font-size: 14px !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child:focus-within {
    border-color: #2DD4A7 !important;
    box-shadow: 0 0 0 3px rgba(45, 212, 167, 0.15) !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] {
    background: #FFFFFF;
    border: 1.5px dashed #E2E6EF;
    border-radius: 10px;
    padding: 24px;
    transition: border-color 0.15s;
}
[data-testid="stFileUploader"]:hover {
    border-color: #2DD4A7;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background: #FFFFFF !important;
    border: 0.5px solid #E2E6EF !important;
    border-radius: 10px !important;
    padding: 16px 20px !important;
}
[data-testid="stMetricLabel"] {
    font-size: 12px !important;
    color: #6B7A99 !important;
    font-weight: 400 !important;
}
[data-testid="stMetricValue"] {
    font-size: 24px !important;
    color: #1B2A4A !important;
    font-weight: 600 !important;
}
[data-testid="stMetricDelta"] {
    font-size: 12px !important;
}
[data-testid="stMetricDelta"][data-direction="up"] {
    color: #0E9E73 !important;
}
[data-testid="stMetricDelta"][data-direction="down"] {
    color: #E55353 !important;
}

/* ── Dataframe / table ── */
[data-testid="stDataFrame"] {
    border: 0.5px solid #E2E6EF !important;
    border-radius: 10px !important;
    overflow: hidden;
}
[data-testid="stDataFrame"] th {
    background: #F8F9FC !important;
    color: #6B7A99 !important;
    font-size: 11px !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-bottom: 0.5px solid #E2E6EF !important;
}
[data-testid="stDataFrame"] td {
    color: #1B2A4A !important;
    font-size: 13px !important;
    border-bottom: 0.5px solid #F8F9FC !important;
}
[data-testid="stDataFrame"] tr:hover td {
    background: #F8F9FC !important;
}

/* ── Tabs ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 0.5px solid #E2E6EF;
    gap: 0;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent !important;
    color: #6B7A99 !important;
    font-size: 13px !important;
    font-weight: 400 !important;
    padding: 8px 20px !important;
    border-bottom: 2px solid transparent !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    color: #1B2A4A !important;
    font-weight: 500 !important;
    border-bottom: 2px solid #1B2A4A !important;
}

/* ── Alerts / status boxes ── */
[data-testid="stAlert"][data-type="success"] {
    background: #E6FAF5 !important;
    border: 0.5px solid #B3EDD9 !important;
    border-radius: 8px !important;
    color: #0A4A38 !important;
}
[data-testid="stAlert"][data-type="warning"] {
    background: #FEF3CD !important;
    border: 0.5px solid #F59E0B !important;
    border-radius: 8px !important;
}
[data-testid="stAlert"][data-type="error"] {
    background: #FDE8E8 !important;
    border: 0.5px solid #E55353 !important;
    border-radius: 8px !important;
}
[data-testid="stAlert"][data-type="info"] {
    background: #EEF2FF !important;
    border: 0.5px solid #CBD5E8 !important;
    border-radius: 8px !important;
    color: #1B2A4A !important;
}

/* ── Status widget ── */
[data-testid="stStatus"],
[data-testid="stStatusWidget"] {
    border: 0.5px solid #E2E6EF !important;
    border-radius: 10px !important;
}

/* ── Spinner ── */
[data-testid="stSpinner"] > div {
    border-top-color: #2DD4A7 !important;
}

/* ── Progress bar ── */
[data-testid="stProgress"] > div > div {
    background: #2DD4A7 !important;
}

/* ── Expanders (card style) ── */
[data-testid="stExpander"] {
    background: #FFFFFF;
    border: 0.5px solid #E2E6EF !important;
    border-radius: 10px !important;
    margin-bottom: 8px;
}
[data-testid="stExpander"] summary {
    font-weight: 500;
    color: #1B2A4A !important;
    font-size: 14px !important;
}

/* ── Sidebar logo area ── */
.sidebar-logo {
    font-size: 26px;
    font-weight: 800;
    color: #FFFFFF;
    letter-spacing: -0.5px;
    padding: 8px 0 4px;
    display: block;
}
.sidebar-tagline {
    font-size: 11px;
    color: #6B84AD;
    letter-spacing: 0.02em;
    padding: 0 0 20px;
    display: block;
    line-height: 1.4;
}
.sidebar-section-label {
    font-size: 10px;
    font-weight: 500;
    color: #4A5E82;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 16px 0 6px;
    display: block;
}

/* ── Icon helpers ── */
.invoxa-icon {
    color: #1B2A4A !important;
    font-size: 16px;
}
.invoxa-icon-chip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: #F8F9FC;
    border: 0.5px solid #E2E6EF;
    border-radius: 7px;
    color: #1B2A4A;
    font-size: 15px;
}
</style>"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
