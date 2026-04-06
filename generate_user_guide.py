#!/usr/bin/env python3
"""
SignalScope User Guide PDF Generator
Generates a comprehensive professional PDF user guide using ReportLab.
"""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, PageBreak, Table, TableStyle,
    HRFlowable, KeepTogether, ListFlowable, ListItem, NextPageTemplate
)
from reportlab.platypus.flowables import Flowable
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ─── Colour Palette ────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#0d2346")
NAVY_MID   = colors.HexColor("#17345f")
NAVY_LIGHT = colors.HexColor("#1e4a7c")
CYAN       = colors.HexColor("#17a8ff")
CYAN_LIGHT = colors.HexColor("#6fceff")
WHITE      = colors.white
BLACK      = colors.black
DARK_TEXT  = colors.HexColor("#1a1a2e")
BODY_TEXT  = colors.HexColor("#1c2333")
ALT_ROW    = colors.HexColor("#eef4fb")
CODE_BG    = colors.HexColor("#f0f3f7")
CODE_BORDER= colors.HexColor("#c8d6e8")
TABLE_HEADER_BG = NAVY
RULE_COLOR = colors.HexColor("#b0c4de")
PAGE_BG    = colors.HexColor("#f8fafd")

OUTPUT_PATH = "/Users/conorewings/Downloads/SignalScope-main-2/SignalScope_User_Guide.pdf"

# (NumberedCanvas removed — page numbers handled in _on_content_page callback)


# ─── Cover page drawing function (used as onFirstPage callback) ────────────────
def draw_cover_page(c, doc):
    """Draw the full-bleed cover page using absolute page coordinates."""
    w, h = A4

    # Background — deep navy
    c.setFillColor(colors.HexColor("#060e1f"))
    c.rect(0, 0, w, h, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#0a1628"))
    c.rect(0, h * 0.45, w, h * 0.55, fill=1, stroke=0)

    # Cyan accent bar at top
    c.setFillColor(CYAN)
    c.rect(0, h - 8, w, 8, fill=1, stroke=0)

    # Horizontal rule
    c.setStrokeColor(colors.HexColor("#1a3a6a"))
    c.setLineWidth(1)
    c.line(40, h * 0.42, w - 40, h * 0.42)

    # Large "S" watermark
    c.saveState()
    c.setFillColor(colors.HexColor("#0e1f3d"))
    c.setFont("Helvetica-Bold", 380)
    c.drawCentredString(w / 2, h * 0.15, "S")
    c.restoreState()

    # Title
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 52)
    c.drawCentredString(w / 2, h * 0.60, "SignalScope")

    # Cyan accent line
    bar_w = 200
    c.setFillColor(CYAN)
    c.rect(w / 2 - bar_w / 2, h * 0.585, bar_w, 3, fill=1, stroke=0)

    # Subtitle
    c.setFillColor(CYAN_LIGHT)
    c.setFont("Helvetica", 22)
    c.drawCentredString(w / 2, h * 0.545, "Comprehensive User Guide")

    # Description
    c.setFillColor(colors.HexColor("#8ab0d0"))
    c.setFont("Helvetica", 12)
    c.drawCentredString(w / 2, h * 0.505, "Broadcast Signal Intelligence Platform")

    # Version
    c.setFillColor(colors.HexColor("#5a7fa0"))
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2, h * 0.46, "Current as of build  SignalScope-3.5.104")

    # Lower rule
    c.setStrokeColor(colors.HexColor("#1a3a6a"))
    c.setLineWidth(0.5)
    c.line(40, h * 0.20, w - 40, h * 0.20)

    # GitHub URL
    c.setFillColor(colors.HexColor("#3a6080"))
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2, h * 0.16, "https://github.com/itconor/SignalScope")

    c.setFillColor(colors.HexColor("#2a4a60"))
    c.setFont("Helvetica", 9)
    c.drawCentredString(w / 2, h * 0.13, "Open Source  •  Flask + RTL-SDR + Python")

    # Bottom bar
    c.setFillColor(NAVY_MID)
    c.rect(0, 0, w, 24, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#3a6080"))
    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, 8, "For broadcast engineers and radio monitoring professionals")


# ─── Build Styles ──────────────────────────────────────────────────────────────
def make_styles():
    base = getSampleStyleSheet()

    styles = {}

    styles['body'] = ParagraphStyle(
        'body', parent=base['Normal'],
        fontSize=10, leading=15,
        textColor=BODY_TEXT,
        spaceBefore=3, spaceAfter=3,
        fontName='Helvetica',
    )

    styles['chapter_title'] = ParagraphStyle(
        'chapter_title',
        fontSize=22, leading=28,
        textColor=WHITE,
        fontName='Helvetica-Bold',
        spaceBefore=0, spaceAfter=6,
        backColor=NAVY,
        borderPad=(8, 14, 8, 14),
    )

    styles['chapter_num'] = ParagraphStyle(
        'chapter_num',
        fontSize=10, leading=14,
        textColor=CYAN_LIGHT,
        fontName='Helvetica',
        spaceBefore=0, spaceAfter=2,
        backColor=NAVY,
        borderPad=(8, 14, 2, 14),
    )

    styles['h1'] = ParagraphStyle(
        'h1',
        fontSize=16, leading=20,
        textColor=NAVY,
        fontName='Helvetica-Bold',
        spaceBefore=14, spaceAfter=5,
        borderPad=0,
    )

    styles['h2'] = ParagraphStyle(
        'h2',
        fontSize=13, leading=17,
        textColor=NAVY_MID,
        fontName='Helvetica-Bold',
        spaceBefore=10, spaceAfter=4,
    )

    styles['h3'] = ParagraphStyle(
        'h3',
        fontSize=11, leading=15,
        textColor=colors.HexColor("#17a8ff"),
        fontName='Helvetica-Bold',
        spaceBefore=7, spaceAfter=3,
    )

    styles['bullet'] = ParagraphStyle(
        'bullet', parent=base['Normal'],
        fontSize=10, leading=14,
        textColor=BODY_TEXT,
        fontName='Helvetica',
        spaceBefore=1, spaceAfter=1,
        leftIndent=16,
        bulletIndent=4,
    )

    styles['bullet2'] = ParagraphStyle(
        'bullet2', parent=base['Normal'],
        fontSize=10, leading=14,
        textColor=BODY_TEXT,
        fontName='Helvetica',
        spaceBefore=1, spaceAfter=1,
        leftIndent=30,
        bulletIndent=18,
    )

    styles['code'] = ParagraphStyle(
        'code',
        fontSize=8.5, leading=12,
        textColor=colors.HexColor("#1a2a3a"),
        fontName='Courier',
        spaceBefore=2, spaceAfter=2,
        leftIndent=8,
    )

    styles['toc_title'] = ParagraphStyle(
        'toc_title',
        fontSize=20, leading=26,
        textColor=NAVY,
        fontName='Helvetica-Bold',
        spaceBefore=0, spaceAfter=12,
    )

    styles['toc_ch'] = ParagraphStyle(
        'toc_ch',
        fontSize=11, leading=17,
        textColor=NAVY,
        fontName='Helvetica-Bold',
        spaceBefore=5, spaceAfter=1,
    )

    styles['toc_sub'] = ParagraphStyle(
        'toc_sub',
        fontSize=9.5, leading=14,
        textColor=colors.HexColor("#3a5a80"),
        fontName='Helvetica',
        spaceBefore=0, spaceAfter=0,
        leftIndent=18,
    )

    styles['caption'] = ParagraphStyle(
        'caption',
        fontSize=8.5, leading=12,
        textColor=colors.HexColor("#5a7a9a"),
        fontName='Helvetica-Oblique',
        spaceBefore=2, spaceAfter=6,
        alignment=TA_CENTER,
    )

    styles['note'] = ParagraphStyle(
        'note',
        fontSize=9.5, leading=14,
        textColor=colors.HexColor("#1a3a5a"),
        fontName='Helvetica-Oblique',
        spaceBefore=4, spaceAfter=4,
        leftIndent=12, rightIndent=12,
        backColor=colors.HexColor("#e8f4ff"),
        borderPad=6,
    )

    return styles


# ─── Helper builders ───────────────────────────────────────────────────────────

def chapter_header(styles, num, title):
    """Returns a list of flowables: chapter number bar + title bar."""
    return [
        Spacer(1, 6),
        Paragraph(f"Chapter {num}", styles['chapter_num']),
        Paragraph(title, styles['chapter_title']),
        Spacer(1, 10),
    ]


def h1(styles, text):
    return [Paragraph(text, styles['h1']), HRFlowable(width="100%", thickness=1, color=RULE_COLOR, spaceAfter=4)]


def h2(styles, text):
    return [Spacer(1, 4), Paragraph(text, styles['h2'])]


def h3(styles, text):
    return [Spacer(1, 3), Paragraph(text, styles['h3'])]


def body(styles, text):
    return Paragraph(text, styles['body'])


def bullet(styles, text, level=1):
    s = styles['bullet'] if level == 1 else styles['bullet2']
    return Paragraph(f"• {text}", s)


def code_block(styles, lines):
    """Returns a table wrapping monospace code lines with a light grey background."""
    if isinstance(lines, str):
        lines = lines.split('\n')
    paras = []
    for line in lines:
        # Escape special chars for ReportLab XML
        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        paras.append(Paragraph(line if line.strip() else '&nbsp;', styles['code']))
    t = Table([[p] for p in paras], colWidths=["100%"])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), CODE_BG),
        ('BOX', (0, 0), (-1, -1), 0.75, CODE_BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (0, 0), 6),
        ('BOTTOMPADDING', (-1, -1), (-1, -1), 6),
        ('ROWPADDING', (0, 0), (-1, -1), 1),
    ]))
    return t


def data_table(styles, headers, rows, col_widths=None):
    """Build a styled table with navy header and alternating rows."""
    header_para = [Paragraph(f"<b>{h}</b>", ParagraphStyle(
        'th', fontSize=9, leading=12, textColor=WHITE, fontName='Helvetica-Bold')) for h in headers]
    data = [header_para]
    for i, row in enumerate(rows):
        data.append([Paragraph(str(cell), ParagraphStyle(
            'td', fontSize=9, leading=13, textColor=BODY_TEXT,
            fontName='Helvetica')) for cell in row])

    if col_widths is None:
        page_w = A4[0] - 40*mm
        col_widths = [page_w / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#b8cfe0")),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]
    for i in range(1, len(rows) + 1):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), ALT_ROW))
    t.setStyle(TableStyle(style_cmds))
    return t


def spacer(h=6):
    return Spacer(1, h)


# ─── TOC ───────────────────────────────────────────────────────────────────────

TOC_ENTRIES = [
    (1, "Introduction"),
    (2, "Installation"),
    (3, "First Run & Setup Wizard"),
    (4, "Dashboard"),
    (5, "Inputs"),
    (6, "FM Scanner"),
    (7, "Logger Plugin"),
    (8, "DAB Scanner Plugin"),
    (9, "Meter Wall Plugin"),
    (10, "Zetta Integration Plugin"),
    (11, "Morning Report Plugin"),
    (12, "Signal Path Latency Plugin"),
    (13, "Web SDR Plugin"),
    (14, "Codec Monitor Plugin"),
    (15, "Push Server Plugin"),
    (16, "PTP Clock Plugin"),
    (17, "Icecast Streaming Plugin"),
    (18, "Listener Plugin"),
    (19, "Producer View Plugin"),
    (20, "AzuraCast Plugin"),
    (21, "Sync Capture Plugin"),
    (22, "SignalScope Player"),
    (23, "Alerting"),
    (24, "Broadcast Chains"),
    (25, "Hub Mode"),
    (26, "AI Anomaly Detection"),
    (27, "Stream Comparator"),
    (28, "Metric History & Analytics"),
    (29, "SLA Tracking"),
    (30, "Security"),
    (31, "Backup & Migration"),
    (32, "Mobile API"),
    (33, "Plugin Development"),
    (34, "Troubleshooting"),
]


def build_toc(styles):
    elems = []
    elems.append(Spacer(1, 20))
    elems.append(Paragraph("Table of Contents", styles['toc_title']))
    elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=10))

    for num, title in TOC_ENTRIES:
        elems.append(Paragraph(f"Chapter {num:02d}  —  {title}", styles['toc_ch']))

    elems.append(PageBreak())
    return elems


# ─── Chapter content builders ─────────────────────────────────────────────────

def ch1_introduction(styles):
    elems = []
    elems += chapter_header(styles, 1, "Introduction")
    elems.append(body(styles,
        "SignalScope is a web-based radio monitoring and signal analysis platform for broadcast engineers. "
        "It runs as a Flask web application, accessible via any browser on any device. It supports stand-alone "
        "monitoring nodes and distributed multi-site hub deployments, giving engineering teams a single pane of "
        "glass across an entire broadcast estate."))
    elems.append(spacer(8))

    elems += h1(styles, "Key Capabilities")
    caps = [
        "FM monitoring via RTL-SDR dongles with full RDS decoding (PS, RadioText, stereo, TP, TA, PI)",
        "DAB digital radio monitoring via RTL-SDR and welle-cli",
        "Livewire / AES67 (RTP multicast) monitoring with packet loss and jitter metrics (RFC 3550 EWMA)",
        "HTTP / HTTPS audio stream monitoring — any format decodable by ffmpeg",
        "Local sound device monitoring — microphone, line-in, USB audio, loopback",
        "Real-time level (dBFS), LUFS Momentary / Short-term / Integrated (EBU R128), and silence detection",
        "AI-powered anomaly detection — per-stream ONNX autoencoder trained on 14 audio features",
        "Broadcast chain fault location — identifies the first failed node in a signal path",
        "Hub mode for multi-site aggregation — centralised monitoring of unlimited remote nodes",
        "Codec Monitor — real-time connection tracking for Comrex, Tieline, Prodys, and APT contribution codecs",
        "PTP / GPS-disciplined wall clock for studios, accurate on any browser",
        "Push notification server for iOS and Android via APNs and FCM",
        "SignalScope Player — desktop companion app (Windows & macOS) for logger recordings",
        "Icecast Streaming plugin — re-stream any monitored input to an Icecast2 server",
        "Listener plugin — polished stream player for presenters and producers",
        "Producer View plugin — simplified chain fault display for non-technical on-air staff",
        "AzuraCast plugin — live now-playing integration and silence correlation with AzuraCast web radio",
        "Sync Capture plugin — multi-site simultaneous audio capture for simulcast alignment verification",
        "Extensible plugin system for adding custom functionality alongside the core application",
    ]
    for cap in caps:
        elems.append(bullet(styles, cap))
    elems.append(spacer(8))

    elems += h1(styles, "Architecture Overview")
    elems.append(body(styles,
        "SignalScope is a single Python file (<b>signalscope.py</b>) built on Flask. It can run in three modes:"))
    elems.append(spacer(4))
    modes = [
        ("Standalone", "Monitors local inputs only. All dashboards, alerts, and history are local."),
        ("Hub Client", "Monitors local inputs and reports to a central hub. The hub aggregates status from all clients."),
        ("Hub Server", "Aggregates data from connected client nodes. Provides a unified dashboard, chain monitoring, and hub reports. May also monitor local inputs."),
    ]
    t_data = [["Mode", "Description"]]
    for m, d in modes:
        t_data.append([m, d])
    elems.append(data_table(styles, ["Mode", "Description"],
                            [[m, d] for m, d in modes],
                            col_widths=[80, 390]))
    elems.append(spacer())
    return elems


def ch2_installation(styles):
    elems = []
    elems += chapter_header(styles, 2, "Installation")

    elems += h1(styles, "System Requirements")
    reqs = [
        ("OS", "Linux — Ubuntu 22.04 LTS recommended. Also runs on Raspberry Pi OS (64-bit). macOS supported for development."),
        ("Python", "3.9 or later"),
        ("ffmpeg", "Required for Logger plugin audio recording and clip export"),
        ("welle-cli", "Required for DAB digital radio monitoring"),
        ("rtl-sdr tools", "rtl_sdr, rtl_fm, rtl_power — required for FM, DAB, and Scanner features"),
        ("libportaudio2", "Required for local sound device monitoring"),
        ("RAM", "512 MB minimum; 1 GB recommended for hub deployments with many clients"),
    ]
    elems.append(data_table(styles, ["Requirement", "Notes"],
                            reqs, col_widths=[110, 360]))
    elems.append(spacer(10))

    elems += h1(styles, "Quick Install (Recommended)")
    elems.append(body(styles,
        "The recommended method uses the automated installer script. Run the following command as a regular user "
        "(sudo access is required for system package installation):"))
    elems.append(spacer(4))
    elems.append(code_block(styles, [
        "/bin/bash <(curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh)"
    ]))
    elems.append(spacer(6))
    elems.append(body(styles, "The installer performs the following steps automatically:"))
    steps = [
        "Detects any existing SignalScope installation and offers to update in-place, preserving configuration",
        "Installs system dependencies: rtl-sdr, welle.io, libportaudio2, ffmpeg",
        "Creates a Python virtual environment under the SignalScope directory",
        "Installs Python dependencies (Flask, numpy, scipy, onnxruntime, etc.)",
        "Configures a systemd service for automatic start and restart on failure",
        "Configures a self-healing watchdog (see Watchdog section below)",
        "Optionally configures an NGINX reverse proxy",
        "Optionally obtains a Let's Encrypt TLS certificate via Certbot",
        "Starts the SignalScope service automatically on completion",
    ]
    for s in steps:
        elems.append(bullet(styles, s))
    elems.append(spacer(10))

    elems += h1(styles, "Manual Installation")
    elems.append(code_block(styles, [
        "git clone https://github.com/itconor/SignalScope.git",
        "cd SignalScope",
        "bash install_signalscope.sh",
    ]))
    elems.append(spacer(10))

    elems += h1(styles, "Accessing SignalScope")
    elems.append(data_table(styles, ["Configuration", "URL"],
                            [
                                ["Default (no NGINX)", "http://localhost:5000"],
                                ["NGINX reverse proxy (HTTP)", "http://your-domain.com"],
                                ["NGINX + TLS (Let's Encrypt)", "https://your-domain.com"],
                            ], col_widths=[200, 270]))
    elems.append(spacer(10))

    elems += h1(styles, "Watchdog Service")
    elems.append(body(styles,
        "The installer configures a systemd watchdog service (<b>signalscope-watchdog</b>) that monitors TCP port 5000 "
        "(SignalScope) and ports 443/80 (NGINX if installed). Each service is restarted independently if it stops "
        "responding. The watchdog itself is monitored by systemd and automatically restarted on failure."))
    elems.append(spacer(4))
    elems.append(body(styles, "To view watchdog logs:"))
    elems.append(code_block(styles, ["journalctl -t signalscope-watchdog"]))
    elems.append(spacer(10))

    elems += h1(styles, "Updating")
    elems.append(body(styles,
        "Go to <b>Settings → Maintenance → Apply Update &amp; Restart</b>. This downloads the latest "
        "<i>signalscope.py</i> from GitHub, validates the file, replaces the running file, and restarts "
        "via systemd — all configuration and data is preserved. The update takes approximately 10–30 seconds."))
    return elems


def ch3_setup(styles):
    elems = []
    elems += chapter_header(styles, 3, "First Run & Setup Wizard")
    elems.append(body(styles,
        "The setup wizard runs automatically on the first browser access after installation. It guides you "
        "through the essential configuration steps before the main dashboard is shown."))
    elems.append(spacer(8))

    elems += h1(styles, "Setup Steps")
    steps = [
        ("Step 1: Authentication", "Set an admin username and password. Passwords are hashed using PBKDF2-SHA256 with a random salt. The password is never stored in plaintext."),
        ("Step 2: SDR Configuration", "The wizard scans for connected RTL-SDR dongles and displays their serial numbers. Assign each dongle a role: FM, DAB, Scanner, or None. Roles determine which features can use each dongle."),
        ("Step 3: Hub Configuration", "Choose the operating mode: Standalone (no hub), Hub Client (connect to a central hub), or Hub Server (act as the aggregation point). Enter hub URL and secret key for client/server modes."),
        ("Step 4: Monitoring Settings", "Configure global defaults: silence detection threshold (dBFS), minimum silence duration before alert, alert cooldown period, and notification channels (email, Teams, Pushover, webhook)."),
    ]
    for title, desc in steps:
        elems += h2(styles, title)
        elems.append(body(styles, desc))
        elems.append(spacer(4))

    elems += h1(styles, "After Setup")
    elems.append(body(styles,
        "After completing the wizard, the main dashboard loads automatically. You can revisit and modify "
        "all settings at any time via <b>Settings</b> in the top navigation bar. Settings changes take "
        "effect immediately without restart (except plugin installation and removal, which require a restart)."))
    return elems


def ch4_dashboard(styles):
    elems = []
    elems += chapter_header(styles, 4, "Dashboard")
    elems.append(body(styles,
        "The dashboard is the main view of SignalScope, showing a live card for every monitored stream. "
        "Cards update in real time without page reload."))
    elems.append(spacer(8))

    elems += h1(styles, "Stream Cards")
    elems.append(body(styles, "Each monitored stream is displayed as a card containing the following elements:"))
    card_items = [
        "Live level bar showing current audio level with a dBFS numeric readout",
        "LUFS Momentary, Short-term, and Integrated values (EBU R128 compliant)",
        "RDS Programme Service (PS) name and RadioText (RT) for FM sources",
        "DAB service name and DLS (Dynamic Label Segment) now-playing text for DAB sources",
        "AI status badge: Learning (during the 24-hour training period), OK, or Anomaly",
        "Trend badge when the level deviates from the expected hour-of-day baseline (amber = moderate, red = significant)",
        "24-hour availability timeline bar — click to cycle between 24h, 6h, and 1h views",
        "Alert or warning status strip on the card border (red = alert, amber = warning)",
        "📈 Signal History button — expands an inline chart for any metric",
        "🎧 Listen button — opens a live audio stream in the sticky mini-player at the bottom of the page",
    ]
    for item in card_items:
        elems.append(bullet(styles, item))
    elems.append(spacer(8))

    elems += h1(styles, "Card Organisation")
    elems.append(body(styles,
        "Cards are drag-to-reorder. Alert cards automatically sort to the top of the grid. "
        "You can manually drag cards to any position; the order is persisted in your browser."))
    elems.append(spacer(8))

    elems += h1(styles, "Mini-Player")
    elems.append(body(styles,
        "Clicking 🎧 Listen on any card opens the sticky mini-player at the bottom of the page. "
        "The mini-player shows the stream name and level while playing. Audio uses the browser's Web Audio API "
        "and remains active while you navigate between dashboard tabs. Click the × button to close the player."))
    elems.append(spacer(8))

    elems += h1(styles, "Hub Dashboard — Live Level Meters")
    elems.append(body(styles,
        "The hub dashboard shows a <b>PPM-style bouncing level bar</b> for every stream on every connected "
        "site. These bars update at <b>5 Hz</b> — independently of the 10-second heartbeat cycle — so you "
        "see smooth real-time level animation without waiting for the next full data refresh."))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Each bar has three colour zones matching standard broadcast PPM practice:"))
    elems.append(data_table(styles,
        ["Zone", "Range", "Colour"],
        [
            ["Programme", "–80 dBFS to –20 dBFS", "Green"],
            ["Near-clip warning", "–20 dBFS to –9 dBFS", "Amber"],
            ["Clip zone", "–9 dBFS to 0 dBFS", "Red"],
        ],
        col_widths=[120, 160, 90]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "A <b>peak-hold marker</b> tracks the highest recent level and decays slowly after 2 seconds, "
        "making transient peaks easy to spot at a glance."))
    elems.append(spacer(4))
    elems.append(body(styles,
        "On page load the bars immediately show the last-known levels (restored from hub state on "
        "restart) and begin animating as soon as live data arrives — typically within one to two "
        "seconds of a stream connecting to audio."))
    return elems


def ch5_inputs(styles):
    elems = []
    elems += chapter_header(styles, 5, "Inputs")

    elems += h1(styles, "Source Types & Address Formats")
    elems.append(data_table(styles,
        ["Source Type", "Address Format", "Example"],
        [
            ["FM via RTL-SDR", "fm://<freq_MHz>", "fm://96.3"],
            ["FM with specific dongle", "fm://<freq>?serial=<serial>&ppm=<offset>", "fm://96.3?serial=00000001&ppm=-2"],
            ["DAB service", "dab://<ServiceName>?channel=<CH>", "dab://Cool FM?channel=12D"],
            ["Livewire / AES67 RTP", "rtp://<multicast>:<port>", "rtp://239.192.10.1:5004"],
            ["HTTP / HTTPS stream", "Full URL", "http://relay.example.com:8000/stream"],
            ["Local sound device", "sound://<device_index>", "sound://2"],
        ],
        col_widths=[120, 160, 190]))
    elems.append(spacer(10))

    elems += h1(styles, "SDR Dongle Roles")
    elems.append(body(styles,
        "Each RTL-SDR dongle must be assigned a role in <b>Settings → SDR Devices</b> before it can be used. "
        "Only dongles with a compatible role appear in the dropdown when adding an input."))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Role", "Used By"],
        [
            ["FM", "FM monitoring inputs"],
            ["DAB", "DAB monitoring inputs"],
            ["Scanner", "FM Scanner and Web SDR on-demand tuning"],
            ["None", "Unassigned — not available to any feature"],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(10))

    elems += h1(styles, "Adding FM Sources")
    steps = [
        "Settings → Inputs → + Add Input → FM",
        "Enter the frequency in MHz (e.g. 96.3)",
        "Select the dongle from the dropdown — must have role FM",
        "Optionally set the PPM calibration offset for your dongle",
        "Save — monitoring starts within seconds",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(8))

    elems += h1(styles, "Adding DAB Sources")
    steps = [
        "Settings → Inputs → + Add Input → DAB",
        "Select the DAB channel (e.g. 12D = 229.072 MHz)",
        "Click 🔍 Scan Mux — welle-cli scans the multiplex and enumerates all available services",
        "Select one or more services from the list",
        "Click ➕ Add Selected Services — each service is added with its broadcast name and correct dab:// address",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(4))
    elems.append(body(styles,
        "<i>Note: Multiple DAB services on the same multiplex share a single welle-cli process, "
        "saving CPU and dongle resources.</i>"))
    elems.append(spacer(8))

    elems += h1(styles, "Adding Livewire / AES67")
    elems.append(body(styles,
        "Enter the multicast RTP address and port (e.g. <b>rtp://239.192.10.1:5004</b>). SignalScope joins "
        "the multicast group using the OS network stack. Packet loss and jitter are measured using "
        "RFC 3550 EWMA and reported in the stream card and metrics history."))
    elems.append(spacer(8))

    elems += h1(styles, "Adding HTTP Streams")
    elems.append(body(styles,
        "Enter the full stream URL. HTTP and HTTPS are both supported. Any audio format decodable by "
        "ffmpeg is accepted, including MP3, AAC, Ogg/Opus, HLS, and Icecast streams."))
    elems.append(spacer(8))

    elems += h1(styles, "Adding Local Sound Devices")
    elems.append(body(styles,
        "Select <b>Local Sound Device</b> from the source type dropdown. The dropdown is populated from "
        "the OS audio device list via PortAudio. Select the desired microphone, line-in, USB audio "
        "interface, or loopback device. The device index is stored as <b>sound://&lt;index&gt;</b> and "
        "resolved to the device name on start; if the device index changes after a reboot, update the "
        "input address in Settings."))
    return elems


def ch6_scanner(styles):
    elems = []
    elems += chapter_header(styles, 6, "FM Scanner")
    elems.append(body(styles,
        "The FM Scanner provides on-demand FM reception in the browser, with live RDS decoding, "
        "tuning history, presets, and band scan. Navigate to <b>Hub → 📻 Scanner</b> or visit <b>/hub/scanner</b>. "
        "At least one dongle with role <b>Scanner</b> must be configured."))
    elems.append(spacer(8))

    elems += h1(styles, "Using the Scanner")
    steps = [
        "Select a site from the dropdown — only sites with a Scanner dongle are shown",
        "Enter an FM frequency in MHz in the frequency field",
        "Click ▶ Start — audio streams to the browser after approximately 1–2 seconds",
        "While streaming: type a new frequency and click Tune to retune without stopping",
        "Click ⏹ Stop to disconnect and release the dongle",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(4))
    elems.append(body(styles, "Press <b>Spacebar</b> at any time to pause or resume audio."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    elems.append(data_table(styles,
        ["Feature", "Description"],
        [
            ["Live RDS", "PS name, RadioText, stereo flag, TP (Traffic Programme), TA (Traffic Announcement), PI (Programme Identification)"],
            ["Tuning History", "Recently tuned frequencies shown as clickable buttons. Click to retune from idle or while streaming."],
            ["Presets", "Save frequencies with a label. Click any preset to tune immediately."],
            ["Band Scan", "📡 Scan Band runs rtl_power over the FM band and displays strong stations as clickable peak markers. Dongle must be free (stop streaming first)."],
            ["Keyboard Shortcut", "Spacebar toggles audio play/pause without clicking the button."],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(10))

    elems += h1(styles, "Audio Pipeline")
    elems.append(body(styles,
        "The audio path is: <b>RTL-SDR → rtl_fm → Python resampling (scipy) → PCM 48 kHz 16-bit → "
        "hub relay slot → browser Web Audio API</b>. End-to-end latency is typically 1–2 seconds "
        "from RF signal to browser speaker. The client batches multiple PCM chunks per HTTP POST "
        "when the WAN round-trip time exceeds the block duration (0.1 s), maintaining real-time "
        "throughput over slow connections."))
    return elems


def ch7_logger(styles):
    elems = []
    elems += chapter_header(styles, 7, "Logger Plugin")
    elems.append(body(styles,
        "The Logger plugin provides continuous 24/7 compliance recording of any monitored stream. "
        "Recordings are stored as clock-aligned 5-minute segments with inline silence detection, "
        "now-playing metadata integration, and a comprehensive browser-based timeline for playback "
        "and clip export. Install from <b>Settings → Plugins → Check GitHub for plugins</b>. "
        "Requires <b>ffmpeg</b> to be installed on the host machine."))
    elems.append(spacer(8))

    elems += h1(styles, "Installation & First Use")
    steps = [
        "Settings → Plugins → Check GitHub for plugins",
        "Find Logger → click ⬇ Install",
        "Restart SignalScope when prompted",
        "Navigate to Logger → Settings tab",
        "Enable recording for each stream and configure format and retention",
        "Save — recording starts immediately, no further restart needed",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(8))

    elems += h1(styles, "Recording Formats")
    elems.append(body(styles,
        "Each stream has its own format setting. Existing recordings are unaffected when changing format; "
        "only new segments use the new setting."))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Format", "Extension", "Encoder", "Notes"],
        [
            ["MP3 (default)", ".mp3", "built-in", "Universal compatibility"],
            ["AAC", ".aac", "aac (ADTS)", "~0.5× storage vs MP3 at equivalent quality"],
            ["Opus", ".ogg", "libopus (OGG)", "~0.25× storage; Chrome/Firefox/Edge/Safari 16.4+"],
        ],
        col_widths=[90, 70, 110, 200]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Files are stored at: <b>logger_recordings/{stream-slug}/{YYYY-MM-DD}/HH-MM.{ext}</b>"))
    elems.append(spacer(10))

    elems += h1(styles, "Quality Tiers & Retention")
    elems.append(data_table(styles,
        ["Setting", "Default", "Description"],
        [
            ["Format", "MP3", "Recording format: MP3 / AAC / Opus"],
            ["HQ Bitrate", "128k", "Bitrate for new recordings"],
            ["LQ Bitrate", "48k", "Bitrate after quality downgrade"],
            ["LQ after (days)", "30", "Re-encode to lower quality after N days"],
            ["Delete after (days)", "90", "Permanently delete recordings after N days"],
        ],
        col_widths=[130, 70, 270]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "A background maintenance thread runs hourly, performing re-encodes and deletions according to "
        "the configured schedule."))
    elems.append(spacer(10))

    elems += h1(styles, "The Timeline View")
    elems.append(body(styles,
        "The Timeline tab shows a full-day overview of a single stream. The layout from top to bottom:"))
    elems.append(spacer(6))

    elems += h2(styles, "Audio Overview Bar")
    elems.append(body(styles,
        "A green waveform minimap showing the full 24-hour day. Click anywhere to jump to that time "
        "and load the nearest segment."))

    elems += h2(styles, "Metadata Bands")
    elems.append(body(styles, "Three thin colour-coded bands between the overview bar and the hour grid:"))
    elems.append(bullet(styles, "<b>Show band (purple):</b> show names from now-playing metadata. Consecutive events with the same show name are merged into one block."))
    elems.append(bullet(styles, "<b>Mic band (green):</b> on-air periods logged via the Mic REST API. Bright green = currently live."))
    elems.append(bullet(styles, "<b>Track band (amber):</b> individual song positions at exact timestamps. Hover for track title and time."))

    elems += h2(styles, "Zoom & Navigation Controls")
    elems.append(data_table(styles,
        ["Control", "Action"],
        [
            ["1× / 2× / 4× / 8× zoom", "Zoom the timeline horizontally. At 8× individual 5-minute blocks are large enough to read song and show labels."],
            ["↕ Expand", "Doubles all band heights for easier reading."],
            ["Click and drag", "Pan the timeline left and right at any zoom level."],
            ["Spacebar", "Toggle playback without scrolling the page."],
            ["Right-click", "Set export mark-in / mark-out directly from the timeline position."],
        ],
        col_widths=[130, 340]))

    elems += h2(styles, "Hour Grid")
    elems.append(body(styles,
        "288 colour-coded 5-minute blocks arranged in an hourly grid:"))
    elems.append(data_table(styles,
        ["Colour", "Meaning"],
        [
            ["Green", "Recorded, audio present"],
            ["Amber", "Recorded, partial silence detected"],
            ["Red", "Recorded, mostly or fully silent"],
            ["Dark / Grey", "No recording for this period"],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(6))
    elems.append(body(styles, "Click any block to load and play that 5-minute segment."))
    elems.append(spacer(10))

    elems += h1(styles, "Clip Export")
    steps = [
        "Play a segment and locate the start of the section you want to export",
        "Click Mark In (or right-click the timeline at the start position)",
        "Navigate to the end of the section and click Mark Out (or right-click again)",
        "Select the export format from the dropdown",
        "Click ⬇ Export Clip — ffmpeg extracts and downloads the clip",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(6))
    elems.append(data_table(styles,
        ["Format", "Extension", "Bitrate", "Notes"],
        [
            ["MP3", ".mp3", "Original", "Instant stream copy if recordings are already MP3"],
            ["AAC", ".m4a", "128 kbps", "~half size, universal support"],
            ["Opus", ".webm", "96 kbps", "Smallest; Chrome/Firefox/Edge/Safari 16.4+"],
        ],
        col_widths=[70, 70, 80, 250]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Clips can span multiple 5-minute segments up to 2 hours. The filename includes stream name, "
        "date, start time, and duration."))
    elems.append(spacer(10))

    elems += h1(styles, "Now-Playing Metadata Integration")
    elems.append(body(styles,
        "Logger polls a now-playing API every 30 seconds to populate show names and track data. "
        "Configure per-stream in Logger Settings:"))
    elems.append(bullet(styles, "<b>Planet Radio API:</b> select station from dropdown — URL auto-filled"))
    elems.append(bullet(styles, "<b>Triton Digital:</b> enter the Triton JSON endpoint URL"))
    elems.append(bullet(styles, "<b>Custom JSON API:</b> enter any URL; Logger extracts title, artist, and show fields"))
    elems.append(bullet(styles, "<b>Fallback:</b> DLS (DAB) or RDS RadioText (FM) used automatically if no API is configured"))
    elems.append(spacer(10))

    elems += h1(styles, "Mic On-Air REST API")
    elems.append(body(styles,
        "Record mic-on/off events from broadcast automation systems, physical hardware controllers, or "
        "any HTTP-capable device. Events appear immediately as green spans on the mic band."))
    elems.append(spacer(4))
    elems.append(body(styles, "<b>Endpoint:</b> POST /api/logger/mic"))
    elems.append(body(styles, "<b>Authentication:</b> Bearer token (set Mic API Key in Logger Settings) or logged-in session"))
    elems.append(spacer(4))
    elems.append(code_block(styles, [
        "{",
        '  "stream": "cool-fm",',
        '  "state":  "on",',
        '  "label":  "Studio A",',
        '  "ts":     1711234567    // optional Unix timestamp, defaults to server time',
        "}",
    ]))
    elems.append(spacer(10))

    elems += h1(styles, "Hub Mode — Centralised Playback")
    elems.append(body(styles,
        "When SignalScope is running as a hub, the Logger aggregates recordings from all connected client "
        "nodes for centralised playback. No files are copied — audio streams on demand through the hub "
        "relay pipeline in real time."))
    elems.append(spacer(6))
    elems.append(body(styles,
        "Select any site from the site dropdown in the Logger header to browse and play that site's "
        "recordings without logging into the client node individually. The hub issues a <b>stream_file</b> "
        "command to the client, which opens the requested segment with ffmpeg and pushes raw audio bytes "
        "through the relay slot to your browser or desktop player."))
    elems.append(spacer(6))
    elems.append(body(styles,
        "<b>Seeking:</b> when you click a specific time on the day bar or use the skip controls, the hub "
        "passes a <b>seek_s</b> value to the client. ffmpeg performs a fast key-frame seek before "
        "streaming, so playback starts at the requested wall-clock position within 1–2 seconds."))
    elems.append(spacer(10))

    elems += h1(styles, "Multi-Node Shared Recordings (Sidecar JSON)")
    elems.append(body(styles,
        "Multiple Logger instances on different nodes can share a common recording storage directory "
        "(NFS, SMB, or any shared filesystem). Each instance writes a per-day metadata sidecar file "
        "(<b>meta_{owner}.json</b>) alongside the audio segments. The Logger UI merges all sidecar "
        "files at load time, so the timeline shows show names, mic spans, and track bands from every "
        "contributing node without any network coordination or locking."))
    elems.append(spacer(10))

    elems += h1(styles, "SignalScope Player Integration")
    elems.append(body(styles,
        "SignalScope Player is a desktop companion application (see Chapter 17) that connects directly "
        "to a hub's Logger to browse and play recordings offline. In Hub mode the player authenticates "
        "with the hub URL and an API token, lists available sites and dates, and streams segments through "
        "the same hub relay pipeline used by the browser player. Skip controls and exact-time seeking "
        "work identically to the browser interface."))
    return elems


def ch8_dab(styles):
    elems = []
    elems += chapter_header(styles, 8, "DAB Scanner Plugin")
    elems.append(body(styles,
        "The DAB Scanner plugin enables on-demand scanning of DAB Band III channels to discover services, "
        "and streams any discovered service as MP3 audio directly to the browser. Install from "
        "<b>Settings → Plugins</b>. Requires <b>welle-cli</b> and <b>ffmpeg</b> on the client machine. "
        "Navigate to <b>/hub/dab</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Scans all Band III DAB channels (5A–13F) to discover available ensembles and their services",
        "Streams any selected service as MP3 audio via welle-cli and ffmpeg",
        "Displays DLS (Dynamic Label Segment) scrolling text in real time",
        "Channel list shows signal quality indicators (SNR, FIC quality)",
        "Automatically stops scanning when a channel is selected for playback",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch9_meterwall(styles):
    elems = []
    elems += chapter_header(styles, 9, "Meter Wall Plugin")
    elems.append(body(styles,
        "The Meter Wall plugin provides a full-screen audio level display for all monitored streams "
        "across all connected hub sites. Install from <b>Settings → Plugins</b>. Navigate to <b>/hub/meterwall</b>. "
        "Designed for a dedicated monitor in a transmission control room or master control room."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "PPM-style level bars with peak hold and natural decay for every stream",
        "LUFS Integrated readout per stream",
        "Alert flash on silence detection or clip event",
        "RDS Programme Service name and DLS now-playing text shown per stream",
        "Site grouping — streams organised by connected hub site",
        "Configurable grid density (compact / standard / large)",
        "Auto-hiding fullscreen kiosk mode — press F or click the fullscreen icon",
        "No controls required during normal operation — suitable for unattended display",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch10_zetta(styles):
    elems = []
    elems += chapter_header(styles, 10, "Zetta Integration Plugin")
    elems.append(body(styles,
        "The Zetta Integration plugin connects SignalScope to RCS Zetta broadcast automation systems, "
        "providing live now-playing data and commercial block detection. Install from <b>Settings → Plugins</b>. "
        "Navigate to <b>/hub/zetta</b>. No additional Python packages are required."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Polls the Zetta SOAP service to show live now-playing data: title, artist, cart number, and category",
        "Detects commercial/spot blocks in real time with a visual indicator on the Zetta dashboard",
        "Provides /api/zetta/status JSON endpoint for broadcast chain integration and external systems",
        "WSDL discovery tool to help locate your Zetta SOAP endpoint URL automatically",
        "Raw SOAP debug console for testing queries and troubleshooting integration issues",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Configuration")
    elems.append(body(styles,
        "Enter your Zetta server URL (usually http://your-zetta-server/ZettaWS.asmx) and "
        "credentials in the plugin Settings page. Use the WSDL discovery button if unsure of "
        "the exact URL. Test the connection with the Test button before saving."))
    return elems


def ch11_morning(styles):
    elems = []
    elems += chapter_header(styles, 11, "Morning Report Plugin")
    elems.append(body(styles,
        "The Morning Report plugin auto-generates a daily engineering briefing covering the previous "
        "calendar day. Install from <b>Settings → Plugins</b>. Hub only — navigate to <b>/hub/morning_report</b>. "
        "The report is generated at 06:00 by default (configurable in plugin settings)."))
    elems.append(spacer(8))

    elems += h1(styles, "Report Contents")
    features = [
        "<b>At-a-glance summary:</b> total faults, number of affected chains, longest single outage duration",
        "<b>Per-chain health table:</b> fault count and total downtime per broadcast chain",
        "<b>Hourly fault heatmap:</b> cyan-tinted grid showing when during the day problems occurred",
        "<b>Auto-detected patterns:</b> fault clustering, above-average fault days, recurring issues, clean streaks",
        "<b>Stream quality summary:</b> level and availability data across all monitored inputs",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Storage & Access")
    elems.append(body(styles,
        "Reports are stored locally and remain accessible for past dates. Navigate to Morning Report "
        "and select a date from the date picker to view any historical report."))
    return elems


def ch12_latency(styles):
    elems = []
    elems += chapter_header(styles, 12, "Signal Path Latency Plugin")
    elems.append(body(styles,
        "The Signal Path Latency plugin tracks broadcast chain processing delay over time, helping "
        "engineers detect equipment changes, processing substitutions, or STL latency drift before "
        "they cause on-air issues. Install from <b>Settings → Plugins</b>. Hub only. Navigate to <b>/hub/latency</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Polls all connected sites for comparator delay measurements every 30 seconds",
        "Stores 90 days of latency history in a local SQLite database",
        "Computes rolling baselines per comparator pair (14-day rolling average)",
        "Displays per-comparator SVG sparklines showing the latency trend over the last 24 hours",
        "Status badges per comparator: Stable / Drifting / Alert",
        "Configurable alert thresholds per comparator pair — alert when drift exceeds N milliseconds from baseline",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch13_websdr(styles):
    elems = []
    elems += chapter_header(styles, 13, "Web SDR Plugin")
    elems.append(body(styles,
        "The Web SDR plugin provides a browser-based software defined radio with waterfall display "
        "and live audio demodulation. Install from <b>Settings → Plugins</b>. Navigate to <b>/hub/sdr</b>. "
        "Requires at least one RTL-SDR dongle with role <b>Scanner</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Scrolling waterfall display with colour-coded signal intensity (colour = power level)",
        "Click anywhere on the waterfall to tune to that frequency immediately",
        "Demodulation modes: WFM (wide FM broadcast), NFM (narrow FM / PMR), AM",
        "Live audio streamed to the browser using the same relay infrastructure as the FM Scanner",
        "Frequency readout and signal level indicator",
        "Persistent frequency presets",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch14_codec(styles):
    elems = []
    elems += chapter_header(styles, 14, "Codec Monitor Plugin")
    elems.append(body(styles,
        "The Codec Monitor plugin provides real-time connection tracking for broadcast contribution "
        "codecs. It polls each configured device and fires alerts when a codec drops or reconnects, "
        "giving operations teams immediate visibility of STL and remote contribution link status. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. Requires <b>pysnmp</b> "
        "for SNMP-based devices; HTTP-scraping devices need no additional packages."))
    elems.append(spacer(8))

    elems += h1(styles, "Supported Devices")
    elems.append(data_table(styles,
        ["Manufacturer", "Models", "Protocol"],
        [
            ["Comrex", "ACCESS NX, BRIC-Link II, BRIC-Link III", "HTTP status page scraping"],
            ["Tieline", "Gateway, ViA", "HTTP status page scraping"],
            ["Prodys", "Quantum ST", "HTTP status page scraping"],
            ["APT / WorldCast", "Quantum", "SNMP (requires pysnmp)"],
        ],
        col_widths=[110, 200, 160]))
    elems.append(spacer(8))

    elems += h1(styles, "Configuration")
    elems.append(body(styles,
        "Navigate to <b>Codec Monitor</b> in the nav bar and click <b>Add Codec</b>. For each device provide:"))
    elems.append(bullet(styles, "<b>Label:</b> friendly display name (e.g. 'Studio A Comrex')"))
    elems.append(bullet(styles, "<b>Host / IP:</b> IP address or hostname of the device"))
    elems.append(bullet(styles, "<b>Type:</b> select from supported device list"))
    elems.append(bullet(styles, "<b>Poll interval:</b> how often to query the device (default 30 s)"))
    elems.append(bullet(styles, "<b>Alert on disconnect:</b> enable to fire CODEC_FAULT when link drops"))
    elems.append(spacer(8))

    elems += h1(styles, "Connection States")
    elems.append(data_table(styles,
        ["State", "Meaning"],
        [
            ["Connected", "Device is reachable and reports an active connection"],
            ["Idle", "Device is reachable but no active connection (line idle)"],
            ["Offline", "Device is unreachable or HTTP/SNMP request timed out"],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(8))

    elems += h1(styles, "Alerts")
    elems.append(body(styles,
        "A <b>CODEC_FAULT</b> alert fires when a codec transitions from Connected → Idle or Offline. "
        "A <b>CODEC_RECOVERY</b> alert fires when it returns to Connected. Both are sent to all "
        "configured notification channels and appear in the Reports alert history."))
    elems.append(spacer(8))

    elems += h1(styles, "Mobile API")
    elems.append(body(styles,
        "The endpoint <b>GET /api/mobile/codecs/status</b> returns all monitored devices with their "
        "current state, label, host, device type, and ISO-8601 last-seen timestamp. Requires a valid "
        "mobile API Bearer token."))
    return elems


def ch15_push(styles):
    elems = []
    elems += chapter_header(styles, 15, "Push Server Plugin")
    elems.append(body(styles,
        "The Push Server plugin turns a SignalScope hub into a centralised push notification server "
        "for iOS and Android. It holds APNs and FCM credentials and delivers notifications on behalf "
        "of any SignalScope installation that points its Push Server URL here — eliminating the need "
        "to configure credentials on every client node. Hub-only. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Credentials")
    elems.append(data_table(styles,
        ["Platform", "Credential", "Notes"],
        [
            ["iOS (APNs)", ".p8 key file", "Download from Apple Developer portal. Also requires Team ID, Key ID, Bundle ID."],
            ["Android (FCM)", "Service account JSON", "Download from Firebase Console → Project Settings → Service Accounts."],
        ],
        col_widths=[90, 110, 270]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Upload credentials in <b>Plugins → Push Server → Settings</b>. One-click migration copies "
        "existing credentials from the local Settings page to the Push Server."))
    elems.append(spacer(8))

    elems += h1(styles, "Connecting Client Nodes")
    elems.append(body(styles,
        "On each client node navigate to <b>Settings → Notifications → Push Notifications</b> and "
        "enter the hub's URL as the Push Server URL. The client will send all push notifications via "
        "the hub's Push Server endpoint instead of calling APNs/FCM directly."))
    elems.append(spacer(8))

    elems += h1(styles, "Delivery Flow")
    steps = [
        "iOS/Android app registers its device token with the local SignalScope instance",
        "Client node relays the token registration to the Push Server hub",
        "When an alert fires on any client, the notification is sent to the Push Server",
        "Push Server signs the APNs JWT (valid 60 minutes, auto-renewed) and POSTs to APNs or FCM",
        "Device receives the push notification within seconds",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    return elems


def ch16_ptpclock(styles):
    elems = []
    elems += chapter_header(styles, 16, "PTP Clock Plugin")
    elems.append(body(styles,
        "The PTP Clock plugin provides a full-screen GPS-accurate wall clock for broadcast studios. "
        "Time is served from the GPS or PTP-disciplined server clock — any browser connected to the "
        "server displays the same accurate time regardless of local clock drift. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. No additional packages required."))
    elems.append(spacer(8))

    elems += h1(styles, "Display Modes")
    elems.append(data_table(styles,
        ["Mode", "Description"],
        [
            ["Digital", "Large HH:MM:SS display with tenths-of-a-second. Shows PTP sync status, offset, and jitter below the clock."],
            ["Analog", "Studio-style analog clock face with sweep second hand. Suitable for on-air studio display."],
        ],
        col_widths=[80, 390]))
    elems.append(spacer(8))

    elems += h1(styles, "Usage")
    elems.append(body(styles,
        "Navigate to <b>PTP Clock</b> in the nav bar. Switch between digital and analog modes with the "
        "toggle button. Append <b>?brand=MyStation</b> to the URL to display a custom branding label "
        "below the clock — useful for studio monitor displays."))
    elems.append(spacer(4))
    elems.append(body(styles,
        "The clock page is suitable for fullscreen display on a dedicated browser tab or kiosk display. "
        "Press F11 (or use the browser's fullscreen mode) for a distraction-free studio clock."))
    elems.append(spacer(8))

    elems += h1(styles, "PTP Sync Status")
    elems.append(body(styles,
        "When the host machine is synchronised via PTP (IEEE 1588) or GPS the clock page shows "
        "a sync status badge, current offset from the grandmaster clock, and jitter (standard deviation "
        "of recent offset samples). A green badge indicates sync within ±1 ms of the grandmaster."))
    return elems


def ch17_icecast(styles):
    elems = []
    elems += chapter_header(styles, 17, "Icecast Streaming Plugin")
    elems.append(body(styles,
        "The Icecast Streaming plugin re-streams any monitored input to an Icecast2 server, turning "
        "SignalScope nodes into live internet radio relay points. It taps the same PCM buffer used by "
        "the Logger plugin, so recording and streaming run simultaneously without conflict. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. Requires <b>ffmpeg</b> "
        "and an Icecast2 server installed separately."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Re-stream any monitored input type: FM/RTL-SDR, DAB, ALSA, RTP, HTTP",
        "Per-stream stereo toggle: HTTP inputs preserve native stereo; all others upmix mono to dual-mono",
        "Hub overview shows live listener counts and stream status from all connected sites",
        "Create and manage streams on any client node directly from the hub interface",
        "Multiple streams can share a single Icecast2 server on different mount points",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Configuration")
    elems.append(body(styles,
        "Navigate to <b>Icecast Streaming</b> in the nav bar and click <b>Add Stream</b>. For each stream provide:"))
    elems.append(bullet(styles, "<b>Input stream:</b> select from monitored inputs on this node"))
    elems.append(bullet(styles, "<b>Icecast server URL:</b> e.g. http://localhost:8000"))
    elems.append(bullet(styles, "<b>Mount point:</b> e.g. /coolFM"))
    elems.append(bullet(styles, "<b>Source password:</b> the Icecast source password"))
    elems.append(bullet(styles, "<b>Bitrate:</b> output MP3 bitrate in kbps (default 128)"))
    elems.append(bullet(styles, "<b>Stereo:</b> enable for stereo inputs"))
    elems.append(spacer(8))

    elems += h1(styles, "Hub Overview")
    elems.append(body(styles,
        "On a hub deployment, the Icecast Streaming page shows a unified overview of all streams across "
        "all connected sites. Each stream card shows the mount point, current listener count, bitrate, "
        "and Start/Stop controls. Streams can be managed on remote client nodes from the hub without "
        "logging into individual nodes."))
    return elems


def ch18_listener(styles):
    elems = []
    elems += chapter_header(styles, 18, "Listener Plugin")
    elems.append(body(styles,
        "The Listener plugin provides a polished, consumer-grade audio player for presenters and producers. "
        "It shows all streams the authenticated user has access to as station cards with live level meters, "
        "one-tap playback, and auto-reconnect. Hub-only. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. No additional packages required."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Station cards with stream name, site, and live PPM-style level meter",
        "One-tap audio playback — tap any card to start listening immediately",
        "Stereo badge on stereo streams; STEREO also shown in the now-playing bar",
        "Animated equaliser bars while audio is playing",
        "Volume control slider in the now-playing bar",
        "Auto-reconnects silently if the stream drops",
        "Mobile-friendly layout — designed for tablets and phones as well as desktop",
        "Users only see streams they have permission to access (respects the plugin role user system)",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Access")
    elems.append(body(styles,
        "Navigate to <b>Listener</b> in the nav bar, or direct non-technical users to the URL directly. "
        "Users assigned the <b>Producer</b> role are directed to the Producer View on login; Listener "
        "is available to all other authenticated users at <b>/hub/listener</b>."))
    return elems


def ch19_presenter(styles):
    elems = []
    elems += chapter_header(styles, 19, "Producer View Plugin")
    elems.append(body(styles,
        "The Producer View plugin provides a simplified, plain-English hub interface for producers and "
        "presenters — technical detail is replaced with at-a-glance status and a human-readable fault "
        "history. Hub-only. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. No additional packages required."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Station status cards with green / amber / red status indicator and live level bar",
        "All-clear banner displayed prominently when everything is running normally",
        "Plain-English fault history — e.g. 'Audio lost for 4 minutes, recovered at 14:32'",
        "Audio replay buttons for fault clips — producers can hear exactly what went to air",
        "Chain-filtered and site-filtered: users only see chains and sites they have permission to view",
        "Producer-role users are directed here automatically on login",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Producer Role")
    elems.append(body(styles,
        "Assign the <b>Producer</b> role to a user in <b>Settings → Users</b>. Producer-role users "
        "are redirected to the Producer View on every login — they never see the full engineering dashboard. "
        "Requires SignalScope 3.4.85 or later with the plugin-role user system enabled."))
    return elems


def ch20_azuracast(styles):
    elems = []
    elems += chapter_header(styles, 20, "AzuraCast Plugin")
    elems.append(body(styles,
        "The AzuraCast plugin connects SignalScope to AzuraCast web radio installations, providing live "
        "now-playing data, listener counts, and station health monitoring with automatic fault alerting. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. No additional packages required."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Station cards show current track, artist, album art, progress bar, next track, live/AutoDJ status, and listener count",
        "AZURACAST_FAULT alert fires when a station goes offline or becomes unreachable",
        "AZURACAST_RECOVERY alert fires when a station comes back online",
        "AZURACAST_SILENCE alert fires when a station is broadcasting but its linked SignalScope input is silent",
        "Hub overview aggregates all AzuraCast stations from all connected sites in one page",
        "Supports multiple AzuraCast servers; each site can manage its own server list",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Configuration")
    elems.append(body(styles,
        "Navigate to <b>AzuraCast</b> in the nav bar and click <b>Add Server</b>. Enter:"))
    elems.append(bullet(styles, "<b>Server URL:</b> your AzuraCast installation URL (e.g. https://radio.example.com)"))
    elems.append(bullet(styles, "<b>API Key:</b> an AzuraCast read-only API key (generate in AzuraCast → Admin → API Keys)"))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Once added, stations are discovered automatically via the AzuraCast Now Playing API. "
        "To enable silence correlation, link each AzuraCast station to a SignalScope monitored input "
        "in the station settings panel — the plugin will cross-reference audio levels with broadcast status."))
    return elems


def ch21_synccap(styles):
    elems = []
    elems += chapter_header(styles, 21, "Sync Capture Plugin")
    elems.append(body(styles,
        "The Sync Capture plugin performs multi-site simultaneous audio capture across all connected "
        "SignalScope nodes. It is ideal for checking simulcast alignment, verifying network contribution "
        "quality, or making reference recordings across a transmitter network. Hub-only. "
        "Install from <b>Settings → Plugins → Check GitHub for plugins</b>. No additional packages required."))
    elems.append(spacer(8))

    elems += h1(styles, "How It Works")
    steps = [
        "Select any combination of inputs from any connected sites in the Sync Capture page",
        "Set a capture duration (5–300 seconds)",
        "Press Capture — the hub broadcasts a timestamped command to all selected sites simultaneously",
        "Each client grabs the last N seconds from its rolling audio buffer at the agreed wall-clock moment",
        "Clips are uploaded to the hub and presented together with inline audio players",
        "Listen to all captures side by side to compare alignment, contribution quality, or identify faults",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(8))

    elems += h1(styles, "Analysis Tools")
    elems.append(data_table(styles,
        ["Tool", "Description"],
        [
            ["⇌ Align", "Computes cross-correlation between clips to find the sub-sample timing offset of each site relative to the reference. Displays lag in milliseconds and correlation quality."],
            ["EBU R128 LUFS", "True peak and integrated loudness per clip for level comparison across sites."],
            ["Octave-band spectrum", "Per-clip octave-band energy plot to identify frequency-domain differences between sites."],
            ["Stereo L/R analysis", "Level and phase for left and right channels independently on stereo clips."],
            ["RDS/DLS snapshot", "Metadata captured at the moment of the clip for FM and DAB inputs."],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(8))

    elems += h1(styles, "DAW Session Export")
    elems.append(body(styles,
        "Click <b>💾 DAW Session</b> on any completed capture to download a ZIP containing:"))
    elems.append(bullet(styles, "All WAV clips in a <b>clips/</b> subdirectory"))
    elems.append(bullet(styles, "A <b>REAPER .rpp</b> project file — one track per site, clips positioned at timeline 0"))
    elems.append(bullet(styles, "An <b>Adobe Audition .sesx</b> session file — one track per site, clips positioned at timeline 0"))
    elems.append(spacer(4))
    elems.append(body(styles,
        "If <b>⇌ Align</b> has been run before export, the alignment offsets are baked into the session "
        "files as source in-points — open the project in REAPER or Audition and the clips will be "
        "perfectly time-aligned automatically."))
    elems.append(spacer(8))

    elems += h1(styles, "BWF Export")
    elems.append(body(styles,
        "Individual clips can be downloaded as Broadcast Wave Format (BWF) files with embedded "
        "origination timestamp metadata. The BWF timestamp corresponds to the wall-clock capture time "
        "on the originating site, enabling forensic time-stamping of recorded material."))
    return elems


def ch22_player(styles):
    elems = []
    elems += chapter_header(styles, 22, "SignalScope Player")
    elems.append(body(styles,
        "SignalScope Player is a standalone desktop application for Windows and macOS that provides "
        "offline access to logger recordings. It connects to a SignalScope hub's Logger API, browses "
        "recordings by site and date, and streams audio on demand — without needing a browser."))
    elems.append(spacer(8))

    elems += h1(styles, "Connection Modes")
    elems.append(data_table(styles,
        ["Mode", "Description"],
        [
            ["Hub", "Connect to a remote SignalScope hub. Enter the hub URL and a mobile API token. "
                    "The player lists all sites and dates available through the hub's Logger. Audio "
                    "streams through the hub relay pipeline in real time."],
            ["Direct", "Point directly at a logger recordings folder on a shared drive or local disk. "
                       "Audio is read and played directly from the filesystem — no network relay required."],
        ],
        col_widths=[60, 410]))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Site and stream selector — browse all available sites and slugs",
        "Date picker — navigate to any recorded date",
        "Day bar — full-day minimap with colour-coded 5-minute blocks; click to jump to any time",
        "Track band — amber song markers on the day bar showing exact start and end times",
        "Segment-level playback with transport controls (play, pause, stop)",
        "Skip controls — ±30 s and ±60 s buttons that cross segment boundaries automatically",
        "Exact-time seeking — clicking the day bar seeks within the segment, not just to its start",
        "Volume control and mute",
        "Now-playing display — stream name, date, and wall-clock position",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Hub Mode Connection")
    steps = [
        "In SignalScope web UI: Settings → Mobile API → Generate token",
        "Copy the hub URL (e.g. https://hub.example.com) and the API token",
        "In SignalScope Player: Settings → Mode → Hub, paste URL and token",
        "Click Connect — the player authenticates and loads available sites",
        "Select site, stream, and date — recordings list populates automatically",
        "Click any segment or day bar position to start playback",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(8))

    elems += h1(styles, "Direct Mode")
    elems.append(body(styles,
        "In Direct mode the player reads audio files from a local or network path. Point it at "
        "the <b>logger_recordings/</b> directory or any path structured as "
        "<b>{slug}/{YYYY-MM-DD}/HH-MM.{ext}</b>. No hub connection or API token is needed. "
        "Sidecar metadata files (<b>meta_*.json</b>) are loaded automatically for track bands and "
        "show names if present."))
    elems.append(spacer(8))

    elems += h1(styles, "Download & Installation")
    elems.append(data_table(styles,
        ["Platform", "Download", "Notes"],
        [
            ["Windows", "SignalScopePlayer.exe (GitHub Releases)", "Double-click to run. No installer needed."],
            ["macOS", "SignalScopePlayer-macOS.zip (GitHub Releases)", "Unzip, drag SignalScopePlayer.app to Applications. Right-click → Open on first launch to bypass Gatekeeper."],
        ],
        col_widths=[70, 175, 225]))
    elems.append(spacer(4))
    elems.append(body(styles,
        "Built with Python and PyQt6. Requires no Python installation on the host machine — "
        "all dependencies are bundled in the executable."))
    return elems


def ch23_alerting(styles):
    elems = []
    elems += chapter_header(styles, 23, "Alerting")
    elems.append(body(styles,
        "SignalScope generates alerts for a wide range of signal quality and fault conditions. "
        "All alerts are logged to the alert history and, depending on configuration, sent via one "
        "or more notification channels."))
    elems.append(spacer(8))

    elems += h1(styles, "Level Alert Types")
    elems.append(data_table(styles,
        ["Alert", "Condition"],
        [
            ["SILENCE", "Level below configured silence floor for the minimum silence duration"],
            ["CLIP", "Level at or above clip threshold (default -1.0 dBFS)"],
            ["HISS", "High-frequency noise floor above configured threshold"],
            ["LUFS_TP", "True peak exceeds dBTP threshold (default -1.0 dBTP)"],
            ["LUFS_I", "30-second integrated loudness outside EBU R128 target (-23 LUFS ±3 LU)"],
            ["GLITCH", "Brief dropout — onset rate, recovery rate, and dip depth all evaluated to distinguish fades from genuine glitches"],
        ],
        col_widths=[90, 380]))
    elems.append(spacer(8))

    elems += h1(styles, "Composite Fault Alerts")
    elems.append(body(styles,
        "When silence is detected on an FM, DAB, or RTP source, SignalScope automatically diagnoses "
        "the likely fault location using carrier presence, RDS lock, and packet loss data:"))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Alert", "Source", "Meaning"],
        [
            ["STUDIO_FAULT", "FM", "Silence + carrier present + RDS lock present → fault upstream of transmitter"],
            ["STL_FAULT", "FM", "Silence + carrier present + RDS absent → STL or studio-to-transmitter link fault"],
            ["TX_DOWN", "FM", "Silence + weak/absent carrier + no RDS → transmitter or antenna fault"],
            ["DAB_AUDIO_FAULT", "DAB", "Silence + mux locked + SNR ≥ 5 dB → audio fault within the DAB service"],
            ["DAB_SERVICE_MISSING", "DAB", "Ensemble locked but service absent from multiplex"],
            ["RTP_FAULT", "RTP", "Silence + ≥10% packet loss → Livewire/AES67 network fault"],
        ],
        col_widths=[115, 55, 300]))
    elems.append(spacer(8))

    elems += h1(styles, "Metadata Mismatch Alerts")
    elems.append(data_table(styles,
        ["Alert", "Condition"],
        [
            ["FM_RDS_MISMATCH", "Received RDS PS name differs from the expected (pinned) name"],
            ["DAB_SERVICE_MISMATCH", "Received DAB service name differs from expected"],
        ],
        col_widths=[140, 330]))
    elems.append(spacer(8))

    elems += h1(styles, "AI & Chain Alerts")
    elems.append(data_table(styles,
        ["Alert", "Condition"],
        [
            ["AI_ANOMALY", "Autoencoder reconstruction error exceeds trained baseline"],
            ["CMP_ALERT", "Post-processing stream is silent while pre-processing stream has audio"],
            ["CHAIN_FAULT", "First down node identified in a broadcast chain"],
            ["CHAIN_RECOVERED", "Broadcast chain returns to fully OK state"],
            ["CHAIN_FLAPPING", "Chain has faulted and recovered 3 or more times within 10 minutes"],
        ],
        col_widths=[120, 350]))
    elems.append(spacer(8))

    elems += h1(styles, "Codec Alerts")
    elems.append(data_table(styles,
        ["Alert", "Condition"],
        [
            ["CODEC_FAULT", "A monitored contribution codec (Comrex, Tieline, Prodys, APT) transitions from connected to idle or offline"],
            ["CODEC_RECOVERY", "A previously faulted codec reconnects and returns to connected state"],
        ],
        col_widths=[120, 350]))
    elems.append(spacer(8))

    elems += h1(styles, "Setting Expected Names")
    elems.append(body(styles,
        "Click <b>📌 Set</b> on any FM card to pin the current RDS PS name as the expected name. "
        "✓ = match, ⚠ = mismatch. Click <b>📌 Update</b> to re-pin if the station rebrands. "
        "The same mechanism applies to DAB service names."))
    elems.append(spacer(8))

    elems += h1(styles, "Notification Channels")
    elems.append(body(styles, "Configure via <b>Settings → Notifications</b>. A test button is available for each channel."))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Channel", "Notes"],
        [
            ["Email (SMTP)", "Standard SMTP with TLS. Supports authenticated and unauthenticated relays."],
            ["MS Teams", "Adaptive Card format with colour-coded severity, or plain text incoming webhook."],
            ["Pushover", "Mobile push notifications with priority levels (normal, high, emergency)."],
            ["Webhook", "Generic HTTP POST with JSON payload — integrate with any third-party system."],
        ],
        col_widths=[120, 350]))
    elems.append(spacer(8))

    elems += h1(styles, "Alert Cooldown & Escalation")
    elems.append(body(styles,
        "A 60-second cooldown prevents duplicate notifications per alert type per stream. The alert "
        "history is always written regardless of cooldown. Per-stream escalation timeout (minutes): "
        "if an alert is unacknowledged after N minutes, all channels re-fire. Set to 0 to disable escalation."))
    return elems


def ch24_chains(styles):
    elems = []
    elems += chapter_header(styles, 24, "Broadcast Chains")
    elems.append(body(styles,
        "Broadcast Chains model the physical signal path as an ordered sequence of monitoring points. "
        "The hub identifies the first failed node and fires a named fault alert with specific fault "
        "location information, enabling rapid fault diagnosis."))
    elems.append(spacer(8))

    elems += h1(styles, "Creating a Chain")
    steps = [
        "Hub → Broadcast Chains → + New Chain",
        "Enter a chain name (e.g. 'Cool FM Distribution')",
        "Click + Add Node for each monitoring point in source-to-destination order",
        "For each node: select Site (this node or any connected remote site), select Stream, optionally add a Label and Machine tag",
        "Click 💾 Save Chain",
    ]
    for i, s in enumerate(steps, 1):
        elems.append(bullet(styles, f"{i}. {s}"))
    elems.append(spacer(8))

    elems += h1(styles, "Node Stacking")
    elems.append(body(styles,
        "Place multiple streams at the same position in a chain for parallel monitoring:"))
    elems.append(bullet(styles, "<b>Fault if ALL silent:</b> all streams must fail before this position is considered faulted. Use for redundant receivers."))
    elems.append(bullet(styles, "<b>ANY down = fault:</b> any single stream failing triggers a fault at this position. Use when every path is required."))
    elems.append(spacer(8))

    elems += h1(styles, "Ad Break Handling")
    elems.append(body(styles,
        "Ad break handling suppresses false fault alerts during commercial breaks. Two modes:"))
    elems.append(bullet(styles, "<b>With mix-in point:</b> mark one node as the ad mix-in point (where the ad server feeds in). While that node carries audio, upstream silence is held for the configured fault confirmation delay before alerting."))
    elems.append(bullet(styles, "<b>Without mix-in point:</b> when a fault-if-ALL-silent stack goes silent but a downstream node has audio, SignalScope detects an ad break automatically. Shows AD BREAK (amber) instead of FAULT (red). If audio returns within the window, no alert is sent and no SLA downtime is recorded."))
    elems.append(spacer(8))

    elems += h1(styles, "Maintenance Bypass")
    elems.append(body(styles,
        "Mark any node as <b>In Maintenance</b> to exclude it from fault detection for a set duration. "
        "A maintenance badge is shown on the chain view and the node is skipped during evaluation. "
        "Chain SLA is not affected during maintenance windows."))
    elems.append(spacer(8))

    elems += h1(styles, "Chain Health Score (0–100)")
    elems.append(data_table(styles,
        ["Component", "Weight"],
        [
            ["30-day SLA uptime", "0–70 points"],
            ["Fault frequency (last 7 days)", "0–20 points"],
            ["Stability (flapping penalty)", "0–10 points"],
            ["Trending-down nodes", "-5 per node (maximum -15)"],
            ["RTP packet loss", "0 to -10 points"],
        ],
        col_widths=[240, 230]))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Score", "Label"],
        [
            ["≥ 90", "Healthy"],
            ["75–89", "Watch"],
            ["50–74", "Degraded"],
            ["< 50", "Poor"],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(8))

    elems += h1(styles, "Fault History & Audio Replay")
    elems.append(body(styles,
        "Each chain logs the last 50 fault and recovery events. At fault time, audio clips are saved "
        "for every node in the chain. Click <b>🎬 Replay</b> to open the inline replay timeline:"))
    elems.append(bullet(styles, "Clips are colour-coded by signal-path position"))
    elems.append(bullet(styles, "Fault point = red, last-good node = green, recovery positions = amber/cyan/purple"))
    elems.append(bullet(styles, "▶ Play All plays clips sequentially with the active node highlighted"))
    elems.append(spacer(8))

    elems += h1(styles, "Historical Chain View")
    elems.append(body(styles,
        "Click <b>📅 View History</b> to reconstruct the chain's appearance at any past date and time "
        "using stored metric history. Useful for post-incident review and SLA reporting."))
    elems.append(spacer(8))

    elems += h1(styles, "A/B Group Monitoring")
    elems.append(body(styles,
        "Hub → Broadcast Chains → <b>+ New A/B Group</b>. Tracks two chains as A (active) and "
        "B (standby). Raises an alert if the active chain faults while the standby chain is also degraded."))
    return elems


def ch25_hub(styles):
    elems = []
    elems += chapter_header(styles, 25, "Hub Mode")
    elems.append(body(styles,
        "Hub mode enables multi-site aggregation. A central hub node collects data from all connected "
        "client nodes, providing a unified view of the entire broadcast estate."))
    elems.append(spacer(8))

    elems += h1(styles, "Setup")
    elems.append(body(styles, "<b>Hub server:</b> Settings → Hub → Enable hub mode. Set a hub secret key."))
    elems.append(body(styles, "<b>Clients:</b> Settings → Hub → enter the hub URL and the same secret key."))
    elems.append(spacer(8))

    elems += h1(styles, "Site Approval")
    elems.append(body(styles,
        "New client connections appear in the hub's <b>Pending Approval</b> queue. The hub admin approves "
        "each site explicitly. Approved sites persist indefinitely and are not removed for going offline."))
    elems.append(spacer(8))

    elems += h1(styles, "Remote Management")
    elems.append(body(styles,
        "From the hub dashboard you can, without logging into individual nodes:"))
    features = [
        "Start and stop monitoring on any client",
        "Add and remove input sources (including DAB bulk-add)",
        "View hub reports covering all sites",
        "Browse Logger recordings from any connected site",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    elems.append(spacer(8))

    elems += h1(styles, "Hub Notification Delegation")
    elems.append(body(styles,
        "Configure each client to suppress its own notifications and delegate to the hub. The hub applies "
        "per-site forwarding rules and deduplication by event UUID, preventing duplicate alerts when "
        "both hub and client notification channels are configured."))
    elems.append(spacer(8))

    elems += h1(styles, "Wall Mode")
    elems.append(body(styles,
        "Navigate to <b>/hub?wall=1</b> or click <b>🖥 Wall Mode</b>. Wall mode provides a heads-up "
        "display optimised for a large screen:"))
    elems.append(bullet(styles, "Header bar: live clock, summary pills (alert/warn/offline counts), exit button"))
    elems.append(bullet(styles, "Connected Sites strip: one pill per site with status dot and alert count"))
    elems.append(bullet(styles, "Broadcast Chains panel: each chain as a card with signal path left-to-right. Stacks show compact rows (status dot · name · level bar · dB). Positions labelled P1, P2, P3..."))
    elems.append(bullet(styles, "Stream Status grid: one card per stream across all sites"))
    elems.append(spacer(4))
    elems.append(body(styles, "Wall mode auto-reloads every 60 seconds."))
    elems.append(spacer(8))

    elems += h1(styles, "Hub Architecture & Security")
    elems.append(body(styles,
        "Each client monitors local RF/IP sources and reports via HMAC-signed, AES-256-GCM encrypted "
        "heartbeats every ~10 seconds. In addition, clients push slim live metric frames (level, peak, "
        "silence state) to the hub at <b>5 Hz</b> so that level bars and broadcast chain evaluation "
        "update in sub-second time. The hub issues commands back on heartbeat ACKs (listen_requests, "
        "commands). Clients cannot be directly called by the hub (NAT traversal is not required — clients "
        "always initiate outbound connections to the hub)."))
    return elems


def ch26_ai(styles):
    elems = []
    elems += chapter_header(styles, 26, "AI Anomaly Detection")
    elems.append(body(styles,
        "Each stream has its own ONNX autoencoder model trained on 14 audio features. The AI engine "
        "learns the normal behaviour of each stream and alerts when the signal deviates significantly "
        "from the learned pattern."))
    elems.append(spacer(8))

    elems += h1(styles, "How It Works")
    elems.append(data_table(styles,
        ["Phase", "Duration", "Behaviour"],
        [
            ["Learning", "First 24 hours from stream creation", "Model trains on incoming feature vectors. No anomaly alerts are generated during this phase."],
            ["Detection", "After training completes", "Reconstruction error compared to learned baseline. 3 consecutive anomalous windows trigger AI_ALERT or AI_WARN."],
            ["Adaptive baseline", "Ongoing", "Model tracks slow long-term changes via exponential moving average. Gradual changes (e.g. seasonal noise floor shift) do not trigger re-alerts."],
        ],
        col_widths=[80, 100, 290]))
    elems.append(spacer(8))

    elems += h1(styles, "Feedback-Driven Retraining")
    elems.append(body(styles,
        "In the Reports page, provide feedback on AI anomaly events:"))
    elems.append(bullet(styles, "<b>👍 (false alarm):</b> marks the event as normal behaviour"))
    elems.append(bullet(styles, "<b>👎 (confirmed fault):</b> marks as a genuine anomaly"))
    elems.append(spacer(4))
    elems.append(body(styles,
        "5 false-alarm labels trigger automatic retraining using the full original 24-hour corpus plus "
        "all corrected samples. This allows the model to adjust to known-good patterns that were "
        "initially treated as anomalies."))
    return elems


def ch27_comparator(styles):
    elems = []
    elems += chapter_header(styles, 27, "Stream Comparator")
    elems.append(body(styles,
        "The Stream Comparator pairs two streams to measure processing delay and detect signal faults "
        "caused by processing chain issues. Configure in <b>Settings → Comparators</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Features")
    features = [
        "Cross-correlates PRE and POST streams to measure processing delay in milliseconds",
        "CMP_ALERT fires when the post-processing stream goes silent while the pre-processing stream has audio",
        "Gain drift alerts when the level difference between PRE and POST exceeds the configured threshold",
        "Delay measurements are available as metrics in Signal History and the Latency plugin",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch28_metrics(styles):
    elems = []
    elems += chapter_header(styles, 28, "Metric History & Analytics")

    elems += h1(styles, "Stored Metrics")
    elems.append(body(styles,
        "SignalScope writes metrics to <b>metrics_history.db</b> once per minute, retained for 90 days."))
    elems.append(spacer(4))
    elems.append(data_table(styles,
        ["Metric", "Source", "Description"],
        [
            ["level_dbfs", "All", "Audio level dBFS"],
            ["lufs_m, lufs_s, lufs_i", "All", "LUFS Momentary / Short-term / Integrated (EBU R128)"],
            ["silence_flag", "All", "1.0 = currently silent"],
            ["clip_count", "All", "Clipping events per snapshot"],
            ["fm_signal_dbm", "FM", "RF carrier strength dBm"],
            ["fm_snr_db", "FM", "Signal-to-noise ratio dB"],
            ["fm_stereo", "FM", "1.0 = stereo pilot present"],
            ["fm_rds_ok", "FM", "1.0 = RDS lock confirmed"],
            ["dab_snr", "DAB", "DAB signal SNR dB"],
            ["dab_ok", "DAB", "1.0 = service present in ensemble"],
            ["dab_sig", "DAB", "DAB signal level dBm"],
            ["dab_bitrate", "DAB", "Service bitrate kbps"],
            ["rtp_loss_pct", "RTP", "Packet loss percentage"],
            ["rtp_jitter_ms", "RTP", "Jitter milliseconds (RFC 3550 EWMA)"],
            ["ptp_offset_us", "PTP", "PTP clock offset microseconds"],
            ["ptp_jitter_us", "PTP", "PTP jitter microseconds"],
            ["chain_status", "Chains", "1.0 = OK, 0.0 = faulted"],
            ["health_pct", "Hub", "Heartbeat success rate percentage"],
            ["latency_ms", "Hub", "Round-trip latency milliseconds"],
        ],
        col_widths=[120, 60, 290]))
    elems.append(spacer(10))

    elems += h1(styles, "Signal History Charts")
    elems.append(body(styles,
        "Click <b>📈 Signal History</b> on any stream card. Select the time range (1h / 6h / 24h) and "
        "the metric to display. Charts are rendered inline using SVG."))
    elems.append(spacer(8))

    elems += h1(styles, "Availability Timeline")
    elems.append(body(styles, "The colour-coded bar below each stream card:"))
    elems.append(data_table(styles,
        ["Colour", "Meaning"],
        [
            ["Green", "Signal present"],
            ["Red", "Silence / audio floor"],
            ["Amber", "DAB service missing"],
            ["Dark", "No data recorded for this period"],
        ],
        col_widths=[100, 370]))
    elems.append(spacer(4))
    elems.append(body(styles, "Click the bar to cycle between 24h, 6h, and 1h views."))
    elems.append(spacer(8))

    elems += h1(styles, "Trend Analysis")
    elems.append(body(styles,
        "SignalScope maintains a 14-day rolling hour-of-day baseline and a 28-day rolling day-of-week "
        "baseline for each stream. When the current level deviates more than ±1.5σ from the expected "
        "baseline, a trend badge is shown on the stream card. The badge escalates from amber to red "
        "after 10 or more minutes of sustained deviation."))
    return elems


def ch29_sla(styles):
    elems = []
    elems += chapter_header(styles, 29, "SLA Tracking")
    elems.append(body(styles,
        "SignalScope calculates monthly per-stream uptime percentages, providing SLA reporting for "
        "broadcast monitoring obligations."))
    elems.append(spacer(8))

    elems += h1(styles, "How SLA Is Calculated")
    elems.append(body(styles,
        "SLA is calculated as the percentage of time a stream was not in a silence/fault state, "
        "expressed as a monthly figure. The following periods are <b>excluded</b> from SLA downtime calculations:"))
    elems.append(bullet(styles, "Ad break countdown windows (when ad break detection is active)"))
    elems.append(bullet(styles, "Maintenance bypass periods"))
    elems.append(spacer(8))

    elems += h1(styles, "Accessing SLA Data")
    elems.append(bullet(styles, "Dashboard: availability percentage shown on each stream card"))
    elems.append(bullet(styles, "Hub Reports: SLA summary table covering all sites and streams"))
    elems.append(bullet(styles, "Raw data: stored in <b>sla_data.json</b> in the application directory"))
    return elems


def ch30_security(styles):
    elems = []
    elems += chapter_header(styles, 30, "Security")
    elems.append(data_table(styles,
        ["Feature", "Details"],
        [
            ["Authentication", "PBKDF2-SHA256 password hashing with random salt. Session timeouts. Login rate limiting."],
            ["CSRF Protection", "All state-changing routes (POST, PUT, DELETE) require a valid CSRF token in the X-CSRFToken header."],
            ["Hub Communications", "HMAC-SHA256 request signing, AES-256-GCM payload encryption, 30-second replay window, 60 RPM rate limit per client site."],
            ["Path Traversal Prevention", "All file-serving routes validate paths against the application directory before serving any file."],
            ["SDR API", "DAB channel parameter validated against an allowlist of valid Band III channels. PPM offset validated as a signed integer within ±1000."],
            ["Plugin Install", "Install URL must originate from the official GitHub repository. Downloaded file must contain SIGNALSCOPE_PLUGIN string before being written to disk."],
            ["Content Security Policy", "Strict CSP applied to all pages. Inline scripts use nonces generated per-request. Dynamic event handlers use data-* attributes and delegated listeners."],
        ],
        col_widths=[130, 340]))
    return elems


def ch31_backup(styles):
    elems = []
    elems += chapter_header(styles, 31, "Backup & Migration")
    elems.append(body(styles,
        "Use <b>Settings → Maintenance → Backup &amp; Restore</b> to download a timestamped ZIP "
        "containing the complete application state."))
    elems.append(spacer(8))

    elems += h1(styles, "Backup Contents")
    elems.append(data_table(styles,
        ["File", "Contents"],
        [
            ["lwai_config.json", "All configuration including stream settings, notification channels, hub config, comparators, chains"],
            ["ai_models/", "ONNX autoencoder models, learned baselines, feedback state, 24-hour training corpora"],
            ["metrics_history.db", "SQLite signal history database (90 days of metrics)"],
            ["sla_data.json", "SLA uptime records"],
            ["alert_log.json", "Full alert event history"],
            ["hub_state.json", "Hub site registrations and approval state"],
        ],
        col_widths=[140, 330]))
    elems.append(spacer(8))

    elems += h1(styles, "Restore & Migration")
    elems.append(bullet(styles, "<b>Restore:</b> Settings → Maintenance → Restore from Backup. Upload the ZIP file."))
    elems.append(bullet(styles, "<b>Migrate to new machine:</b> Install SignalScope on the new machine, then restore from backup. All streams, models, and history are transferred."))
    return elems


def ch32_mobile(styles):
    elems = []
    elems += chapter_header(styles, 32, "Mobile API")
    elems.append(body(styles,
        "All Mobile API endpoints require authentication via a Bearer token, X-API-Key header, or "
        "?token= query parameter. Generate tokens in <b>Settings → Mobile API</b>."))
    elems.append(spacer(8))

    elems += h1(styles, "Key Endpoints")
    elems.append(data_table(styles,
        ["Endpoint", "Method", "Description"],
        [
            ["/api/mobile/status", "GET", "All streams with live level, LUFS, RDS, DAB, and alert status"],
            ["/api/mobile/faults", "GET", "Active fault chains with location and duration"],
            ["/api/mobile/reports/events", "GET", "Alert history with pagination"],
            ["/api/mobile/metrics/history", "GET", "Time-series metric data for charting"],
            ["/api/mobile/hub/overview", "GET", "Hub sites summary — status, alert counts, heartbeat age"],
            ["/api/mobile/register_token", "POST", "Register an APNs or FCM push notification token"],
            ["/api/mobile/logger/sites", "GET", "List sites with logger recordings available on the hub"],
            ["/api/mobile/logger/dates", "GET", "List recorded dates for a given site and stream slug"],
            ["/api/mobile/logger/segments", "GET", "List 5-minute segment metadata for a given date"],
            ["/api/mobile/logger/prepare_play", "POST", "Request a hub relay stream URL for a segment (with optional seek_s)"],
            ["/api/mobile/codecs/status", "GET", "All monitored codec devices with connection state and last-seen time"],
        ],
        col_widths=[175, 55, 240]))
    elems.append(spacer(8))

    elems += h1(styles, "iOS App Features")
    features = [
        "Dashboard with live stream cards",
        "Active Faults list with push notification on new fault",
        "Reports page with alert history and filtering",
        "Hub Overview across all connected sites",
        "Signal History with Swift Charts visualisation",
        "Audio playback via AVPlayer — listen to any stream from the iOS app",
    ]
    for f in features:
        elems.append(bullet(styles, f))
    return elems


def ch33_plugins(styles):
    elems = []
    elems += chapter_header(styles, 33, "Plugin Development")
    elems.append(body(styles,
        "SignalScope's plugin system allows you to extend the application with custom pages and "
        "functionality. Plugins are single Python files placed in the <b>plugins/</b> subdirectory "
        "that are automatically discovered and loaded at startup."))
    elems.append(spacer(4))
    elems.append(body(styles,
        "<i>Note: older releases stored plugins alongside signalscope.py in the root directory. "
        "On first run after upgrading, SignalScope automatically migrates any root-level plugin files "
        "and their associated config files into the plugins/ subdirectory.</i>"))
    elems.append(spacer(8))

    elems += h1(styles, "Minimal Plugin Skeleton")
    elems.append(code_block(styles, [
        "# plugins/myplugin.py",
        "",
        "SIGNALSCOPE_PLUGIN = {",
        '    "id":    "myplugin",       # unique slug, matches filename stem',
        '    "label": "My Plugin",      # nav bar label',
        '    "url":   "/hub/myplugin",  # nav bar href',
        '    "icon":  "\\U0001f527",         # optional emoji',
        "}",
        "",
        "def register(app, ctx):",
        '    """Called once at startup. Register Flask routes here."""',
        "    login_required  = ctx[\"login_required\"]",
        "    csrf_protect    = ctx[\"csrf_protect\"]",
        "    monitor         = ctx[\"monitor\"]",
        "    hub_server      = ctx[\"hub_server\"]",
        "    listen_registry = ctx[\"listen_registry\"]",
        "    BUILD           = ctx[\"BUILD\"]",
        "",
        "    @app.get(\"/hub/myplugin\")",
        "    @login_required",
        "    def myplugin_page():",
        "        return \"<h1>My Plugin</h1>\"",
    ]))
    elems.append(spacer(10))

    elems += h1(styles, "Context Keys (ctx)")
    elems.append(data_table(styles,
        ["Key", "Type", "Description"],
        [
            ["app", "Flask", "The Flask application instance"],
            ["monitor", "AppMonitor", "Access monitor.app_cfg (config dataclass), monitor.log(), etc."],
            ["hub_server", "HubServer | None", "Hub state: _sites, _scanner_sessions, etc. None on client-only nodes."],
            ["listen_registry", "ListenSlotRegistry", "Create and get audio relay slots"],
            ["login_required", "decorator", "Apply to routes that require an authenticated browser session"],
            ["mobile_api_required", "decorator", "Apply to /api/mobile/... routes — accepts Bearer token auth from the iOS app. Always obtain as: ctx.get(\"mobile_api_required\", ctx[\"login_required\"])"],
            ["csrf_protect", "decorator", "Apply to POST routes to validate CSRF token"],
            ["BUILD", "str", "Current build string e.g. 'SignalScope-3.5.104'"],
        ],
        col_widths=[120, 110, 240]))
    elems.append(spacer(8))

    elems += h1(styles, "Hub-Only Plugins")
    elems.append(body(styles,
        "Plugins that only make sense in hub or both mode can set <b>\"hub_only\": True</b> in their "
        "SIGNALSCOPE_PLUGIN dict. The nav bar item is suppressed automatically when running in "
        "client-only mode."))
    elems.append(spacer(8))

    elems += h1(styles, "Adding to the Public Registry")
    elems.append(body(styles,
        "Add an entry to <b>plugins.json</b> at the repository root and open a pull request:"))
    elems.append(spacer(4))
    elems.append(code_block(styles, [
        "{",
        '  "id":           "myplugin",',
        '  "name":         "My Plugin",',
        '  "file":         "myplugin.py",',
        '  "icon":         "\\U0001f527",',
        '  "description":  "What it does.",',
        '  "version":      "1.0.0",',
        '  "requirements": "numpy scipy",',
        '  "url":          "https://raw.githubusercontent.com/itconor/SignalScope/main/plugins/myplugin.py"',
        "}",
    ]))
    elems.append(spacer(6))
    elems.append(body(styles,
        "Users will see the plugin listed in <b>Settings → Plugins → Check GitHub for plugins</b> and "
        "can install it with a single click. See the repository's CLAUDE.md for full plugin documentation "
        "including audio relay integration, hub-client command patterns, SDR IQ capture, and the browser "
        "audio pump JavaScript."))
    return elems


def ch34_troubleshooting(styles):
    elems = []
    elems += chapter_header(styles, 34, "Troubleshooting")

    issues = [
        (
            "FM Scanner shows no sites",
            "Ensure at least one dongle is configured with role Scanner in Settings → SDR Devices. "
            "Wait one heartbeat cycle (~10 seconds) after changing dongle roles for the hub to update.",
        ),
        (
            "Logger not recording",
            None,
            [
                "Check ffmpeg is installed: ffmpeg -version",
                "Ensure the stream is enabled for recording in Logger → Settings",
                "Check the base recordings directory is writable by the signalscope user",
                "View recent logs: journalctl -u signalscope -n 100",
            ]
        ),
        (
            "Logger recording in wrong format",
            "The format setting only affects new 5-minute segments. Existing recordings keep their original "
            "format. Both old and new formats will play back correctly in the timeline player.",
        ),
        (
            "DAB scanner won't start",
            "Ensure welle-cli is installed and on the PATH: which welle-cli. Verify the RTL-SDR dongle is "
            "not already in use by another process (e.g. a monitoring input).",
        ),
        (
            "No audio in FM Scanner",
            None,
            [
                "Check the browser allows audio autoplay for the SignalScope domain",
                "Click anywhere on the page to unlock the Audio Context (required by browsers on first interaction)",
                "Ensure the Scanner-role dongle is free — it cannot be shared with monitoring inputs",
            ]
        ),
        (
            "Hub won't accept client connections",
            None,
            [
                "Verify the hub URL (include port if not 443/80): https://your-hub.example.com",
                "Verify the secret key matches on both hub and client",
                "Approve the pending site in hub Settings → Hub → Pending Approval",
                "Check firewall allows inbound TCP on port 5000 (or 443 with NGINX)",
            ]
        ),
        (
            "NGINX 413 error on audio uploads",
            "SignalScope automatically compresses WAV clips to MP3 before upload to stay within limits. "
            "If errors persist, add the following to your nginx server block:\n"
            "client_max_body_size 50M;",
        ),
        (
            "Plugin not appearing after install",
            "A restart is required after installing or removing a plugin. Go to Settings → Maintenance → "
            "Restart SignalScope, or run: sudo systemctl restart signalscope",
        ),
        (
            "AI showing 'Learning' for extended period",
            "The learning phase runs for 24 hours of monitoring data from stream creation. "
            "If the stream was offline during this period, the clock does not advance. "
            "Ensure the stream is connected and receiving audio continuously.",
        ),
        (
            "RDS PS name not updating",
            "The RDS reader requires 3 of the last 12 matching PS samples (minimum 6 characters) before "
            "updating the displayed name. This stabilisation prevents display glitches from noisy RDS. "
            "Stereo, TP, TA, and PI fields update immediately without stabilisation.",
        ),
    ]

    for issue in issues:
        title = issue[0]
        desc = issue[1] if len(issue) > 1 else None
        sub_bullets = issue[2] if len(issue) > 2 else None
        elems += h2(styles, title)
        if desc:
            # Handle newline in desc for code-like content
            if '\n' in desc:
                parts = desc.split('\n')
                elems.append(body(styles, parts[0]))
                elems.append(spacer(3))
                elems.append(code_block(styles, parts[1:]))
            else:
                elems.append(body(styles, desc))
        if sub_bullets:
            for b_text in sub_bullets:
                elems.append(bullet(styles, b_text))
        elems.append(spacer(6))

    return elems


# ─── Custom DocTemplate with cover + content templates ────────────────────────

def _on_cover_page(canv, doc):
    draw_cover_page(canv, doc)


def _on_content_page(canv, doc):
    """Footer with page numbers on content pages."""
    page_num = canv.getPageNumber()
    if page_num <= 2:
        return
    w, h = A4
    y = 14 * mm
    canv.saveState()
    canv.setFillColor(NAVY_MID)
    canv.setFont("Helvetica", 8)
    canv.drawCentredString(w / 2, y, f"SignalScope User Guide  \u2022  Page {page_num - 2}")
    canv.setStrokeColor(RULE_COLOR)
    canv.setLineWidth(0.5)
    canv.line(20*mm, y + 4*mm, w - 20*mm, y + 4*mm)
    canv.restoreState()


# ─── Main ──────────────────────────────────────────────────────────────────────

def build_pdf():
    w, h = A4
    lm, rm, tm, bm = 20*mm, 20*mm, 22*mm, 28*mm

    # Cover frame spans the full page (no margins needed — draw_cover_page
    # draws in absolute coords using the onPage callback)
    cover_frame = Frame(0, 0, w, h, leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0, id='cover')

    # Content frame with normal margins
    content_frame = Frame(lm, bm, w - lm - rm, h - tm - bm, id='normal')

    cover_template   = PageTemplate(id='Cover',   frames=[cover_frame],   onPage=_on_cover_page)
    content_template = PageTemplate(id='Content', frames=[content_frame], onPage=_on_content_page)

    doc = BaseDocTemplate(
        OUTPUT_PATH,
        pagesize=A4,
        pageTemplates=[cover_template, content_template],
        title="SignalScope User Guide",
        author="SignalScope",
        subject="Comprehensive User Guide — SignalScope-3.5.104",
    )

    styles = make_styles()
    story = []

    # ── Page 1: Cover (blank flowable; actual drawing done by onPage callback) ─
    story.append(NextPageTemplate('Content'))
    story.append(PageBreak())

    # ── Page 2: Table of Contents ───────────────────────────────────────────────
    story += build_toc(styles)

    # ── Chapters ────────────────────────────────────────────────────────────────
    chapters = [
        ch1_introduction,
        ch2_installation,
        ch3_setup,
        ch4_dashboard,
        ch5_inputs,
        ch6_scanner,
        ch7_logger,
        ch8_dab,
        ch9_meterwall,
        ch10_zetta,
        ch11_morning,
        ch12_latency,
        ch13_websdr,
        ch14_codec,
        ch15_push,
        ch16_ptpclock,
        ch17_icecast,
        ch18_listener,
        ch19_presenter,
        ch20_azuracast,
        ch21_synccap,
        ch22_player,
        ch23_alerting,
        ch24_chains,
        ch25_hub,
        ch26_ai,
        ch27_comparator,
        ch28_metrics,
        ch29_sla,
        ch30_security,
        ch31_backup,
        ch32_mobile,
        ch33_plugins,
        ch34_troubleshooting,
    ]

    for i, ch_fn in enumerate(chapters):
        story += ch_fn(styles)
        if i < len(chapters) - 1:
            story.append(PageBreak())

    doc.build(story)
    print(f"PDF generated: {OUTPUT_PATH}")
    size = os.path.getsize(OUTPUT_PATH)
    print(f"File size: {size / 1024:.1f} KB")


if __name__ == "__main__":
    build_pdf()
