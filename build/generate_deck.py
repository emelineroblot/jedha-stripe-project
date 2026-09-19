"""
Génère le support de soutenance (5 slides, 5 minutes) : docs/deck/stripe-architecture-soutenance.pptx
puis le PDF et un PNG par slide via PowerPoint (COM, Windows) pour le contrôle visuel.

Trame et notes orateur : docs/soutenance.md.   Usage : python build/generate_deck.py [--no-export]
"""

import subprocess
import sys
import time
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "deck"
OUT.mkdir(parents=True, exist_ok=True)

# Palette : violet Stripe dominant, navy profond, gris clair, accents cyan / vert / orange
PURPLE = RGBColor(0x63, 0x5B, 0xFF)
NAVY = RGBColor(0x0A, 0x25, 0x40)
INK = RGBColor(0x1F, 0x2A, 0x44)
MUTED = RGBColor(0x6B, 0x7C, 0x93)
LIGHT = RGBColor(0xF6, 0xF9, 0xFC)
LINE = RGBColor(0xD6, 0xDE, 0xE8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CYAN = RGBColor(0x00, 0xB4, 0xD8)
GREEN = RGBColor(0x15, 0xBE, 0x53)
ORANGE = RGBColor(0xF5, 0x8A, 0x07)
RED = RGBColor(0xE5, 0x48, 0x48)
NAVY_CARD = RGBColor(0x14, 0x33, 0x55)
MONO = "Consolas"
FONT = "Calibri"

W, H = 13.33, 7.5
AUTHOR = "Emeline ROBLOT — Data Engineer"
DATE = "Septembre 2026"


# ───────────────────────── helpers ─────────────────────────

def new_prs():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    return prs


def blank(prs, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    rect(s, 0, 0, W, H, NAVY if dark else WHITE)
    return s


def rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, line=None, radius=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    return s


def tb(slide, text, x, y, w, h, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT,
       anchor=MSO_ANCHOR.TOP, italic=False, font=FONT, margin=0.05):
    t = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = t.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        runs = line if isinstance(line, list) else [(line, {})]
        for chunk in runs:
            txt, opt = chunk if isinstance(chunk, tuple) else (chunk, {})
            r = p.add_run()
            r.text = txt
            r.font.name = opt.get("font", font)
            r.font.size = Pt(opt.get("size", size))
            r.font.bold = opt.get("bold", bold)
            r.font.italic = opt.get("italic", italic)
            r.font.color.rgb = opt.get("color", color)
    return t


def bullets(slide, items, x, y, w, h, size=13, color=INK, gap=4, bullet_color=PURPLE):
    t = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = t.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_before = Pt(gap)
        head, body = item if isinstance(item, tuple) else (None, item)
        r = p.add_run()
        r.text = "▸ "
        r.font.size, r.font.color.rgb, r.font.name = Pt(size), bullet_color, FONT
        if head:
            r = p.add_run()
            r.text = head + " "
            r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(size), True, color, FONT
        r = p.add_run()
        r.text = body
        r.font.size, r.font.color.rgb, r.font.name = Pt(size), color, FONT
    return t


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.5, dashed=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(tail)
    if dashed:
        dash = ln.makeelement(qn("a:prstDash"), {"val": "dash"})
        ln.insert(0, dash)
    return c


def node(slide, x, y, w, h, title, sub=None, fill=WHITE, title_color=INK, sub_color=MUTED,
         line=LINE, title_size=13, sub_size=10):
    rect(slide, x, y, w, h, fill, MSO_SHAPE.ROUNDED_RECTANGLE, line=line, radius=0.12)
    if sub:
        tb(slide, title, x + 0.08, y + 0.08, w - 0.16, 0.34, size=title_size, bold=True,
           color=title_color, align=PP_ALIGN.CENTER)
        tb(slide, sub, x + 0.08, y + 0.4, w - 0.16, h - 0.45, size=sub_size, color=sub_color,
           align=PP_ALIGN.CENTER)
    else:
        tb(slide, title, x + 0.08, y, w - 0.16, h, size=title_size, bold=True, color=title_color,
           align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def header(slide, title, kicker=None, dark=False):
    if kicker:
        tb(slide, kicker.upper(), 0.6, 0.35, 12, 0.3, size=11, bold=True, color=PURPLE if not dark else CYAN)
    tb(slide, title, 0.6, 0.6, 12.1, 0.9, size=32, bold=True, color=WHITE if dark else NAVY)


def footer(slide, n, total, dark=False):
    c = RGBColor(0x9F, 0xB3, 0xC8) if dark else MUTED
    tb(slide, f"Stripe — architecture de données · {AUTHOR} · {DATE}", 0.6, 7.05, 10, 0.3, size=9, color=c)
    tb(slide, f"{n} / {total}", 11.7, 7.05, 1.0, 0.3, size=9, color=c, align=PP_ALIGN.RIGHT)


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def stat(slide, x, y, w, value, label, dark=True, color=CYAN):
    tb(slide, value, x, y, w, 0.75, size=36, bold=True, color=color, align=PP_ALIGN.CENTER)
    tb(slide, label, x, y + 0.75, w, 0.6, size=11, color=RGBColor(0xC9, 0xD6, 0xE5) if dark else MUTED,
       align=PP_ALIGN.CENTER)


TOTAL = 5


# ───────────────────────── slide 1 — le problème, la réponse ─────────────────────────

def slide_1(prs):
    s = blank(prs, dark=True)
    tb(s, "STRIPE · BUSINESS CASE DATA ENGINEERING", 0.6, 0.55, 12, 0.35, size=12, bold=True, color=CYAN)
    tb(s, "Une architecture de données OLTP · OLAP · NoSQL", 0.6, 0.95, 12.1, 1.0, size=38, bold=True, color=WHITE)
    tb(s, "Trois besoins qu'aucune base ne satisfait seule — trois systèmes spécialisés, un bus d'événements, une seule source de vérité.",
       0.6, 1.95, 11.5, 0.7, size=16, color=RGBColor(0xC9, 0xD6, 0xE5))

    cards = [
        ("Encaisser", "des millions de paiements par jour sans jamais en perdre un", "OLTP · PostgreSQL", CYAN),
        ("Analyser", "des années d'historique en secondes, sur toutes les dimensions", "OLAP · star schema", GREEN),
        ("Exploiter", "logs, sessions et features de fraude dont le schéma change chaque semaine", "NoSQL · MongoDB", ORANGE),
    ]
    x = 0.6
    for title, body, tag, color in cards:
        rect(s, x, 3.0, 3.9, 2.55, NAVY_CARD, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
        tb(s, title, x + 0.25, 3.2, 3.4, 0.5, size=22, bold=True, color=color)
        tb(s, body, x + 0.25, 3.75, 3.4, 1.1, size=13, color=WHITE)
        tb(s, tag, x + 0.25, 4.95, 3.4, 0.4, size=11, bold=True, color=color)
        x += 4.1

    tb(s, [[("Données synthétiques, volumes de démo. ", {"bold": True, "color": CYAN}),
            ("Tout ce qui suit est exécuté — en local et en production sur AWS.", {"color": RGBColor(0xC9, 0xD6, 0xE5)})]],
       0.6, 5.9, 12, 0.5, size=13)
    tb(s, f"{AUTHOR} · {DATE}", 0.6, 6.5, 12, 0.4, size=12, color=RGBColor(0x9F, 0xB3, 0xC8))
    notes(s, "40 s. Stripe a trois besoins qu'aucune base ne satisfait seule : encaisser des millions de paiements "
             "par jour sans jamais en perdre un, analyser des années d'historique en secondes, et exploiter des logs, "
             "des sessions et des features de fraude dont le schéma change toutes les semaines. Ma réponse : trois "
             "systèmes spécialisés, un bus d'événements entre eux, et une règle — chaque donnée a une seule source de "
             "vérité et n'est jamais ressaisie. Tout ce que je vais montrer est exécutable ; les données sont "
             "synthétiques, et les volumes sont ceux d'une démo.")
    return s


# ───────────────────────── slide 2 — l'architecture ─────────────────────────

def slide_2(prs):
    s = blank(prs)
    header(s, "Chaque système fait ce qu'il fait le mieux — Kafka les relie", "Architecture")

    # Diagramme
    LB = RGBColor(0xC9, 0xD6, 0xE5)
    node(s, 0.6, 2.25, 1.7, 0.9, "API paiements", "charges · refunds · disputes", fill=LIGHT)
    node(s, 2.9, 2.1, 2.3, 1.2, "PostgreSQL 16 — OLTP", "13 tables 3NF · partitions · ACID\nidempotency_key · standby sync",
         fill=RGBColor(0xE6, 0xF7, 0xFB), line=CYAN, title_color=NAVY)
    node(s, 5.8, 2.1, 1.9, 1.2, "Debezium → Kafka", "CDC sur le WAL · < 500 ms\nPII exclues à la source",
         fill=RGBColor(0xEE, 0xEC, 0xFF), line=PURPLE, title_color=NAVY)
    node(s, 8.5, 1.6, 2.5, 0.9, "Scoring fraude", "Redis + règles + XGBoost · < 200 ms",
         fill=RGBColor(0xFF, 0xF1, 0xE0), line=ORANGE, title_color=NAVY)
    node(s, 8.5, 2.8, 2.5, 0.9, "MongoDB 7 — NoSQL", "logs time-series · sessions · ml_features",
         fill=RGBColor(0xFF, 0xF1, 0xE0), line=ORANGE, title_color=NAVY)
    node(s, 8.5, 4.0, 2.5, 0.9, "S3 → dbt → Redshift", "star schema · SCD2 · MV · < 5 min",
         fill=RGBColor(0xE8, 0xF8, 0xEE), line=GREEN, title_color=NAVY)
    node(s, 11.3, 4.0, 1.45, 0.9, "BI · rapports\n· ML", fill=LIGHT, title_size=11)
    node(s, 5.8, 4.1, 1.9, 0.75, "Airflow", "batch · dbt · droits RGPD", fill=LIGHT)

    arrow(s, 2.3, 2.7, 2.9, 2.7)
    arrow(s, 5.2, 2.7, 5.8, 2.7)
    arrow(s, 7.7, 2.5, 8.5, 2.05)
    arrow(s, 7.7, 2.7, 8.5, 3.25)
    arrow(s, 7.7, 2.95, 8.5, 4.45)
    arrow(s, 11.0, 4.45, 11.3, 4.45)
    arrow(s, 8.5, 3.4, 7.7, 3.1, color=ORANGE, dashed=True)       # change streams → Kafka
    arrow(s, 9.75, 2.5, 9.75, 2.8, color=ORANGE)                   # scoring → Mongo
    arrow(s, 7.7, 4.45, 8.5, 4.6, dashed=True)                     # Airflow → OLAP
    arrow(s, 8.5, 1.85, 5.0, 2.1, color=ORANGE, dashed=True)       # scoring → OLTP
    tb(s, "← fraud_indicators (scoring → OLTP)", 2.9, 1.7, 3.2, 0.3, size=9, color=MUTED, italic=True)
    tb(s, "change streams → Kafka", 8.55, 3.72, 2.4, 0.28, size=9, color=MUTED, italic=True)

    # Bas : trois principes
    y = 5.35
    for i, (t, b, c) in enumerate([
        ("Les faits d'argent en SQL", "Source de vérité unique, ACID, RPO 0 / RTO < 60 s.", CYAN),
        ("Le contexte en documents", "Schéma flexible mais validé ($jsonSchema), TTL, sharding.", ORANGE),
        ("L'analyse en colonnes", "Grain = 1 transaction, dimensions SCD Type 2, 3 niveaux de pré-agrégation.", GREEN),
    ]):
        x = 0.6 + i * 4.1
        rect(s, x, y, 3.9, 1.45, LIGHT, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
        tb(s, t, x + 0.2, y + 0.12, 3.5, 0.4, size=14, bold=True, color=c)
        tb(s, b, x + 0.2, y + 0.55, 3.5, 0.85, size=12, color=INK)

    footer(s, 2, TOTAL)
    notes(s, "1 min 10. PostgreSQL pour l'argent : 13 tables en 3NF, partitionnées par mois, ACID, clé d'idempotence, "
             "standby synchrone — RPO 0, RTO sous la minute. Le chargeback n'est pas un statut, c'est une entité. "
             "Redshift pour l'analyse : star schema Kimball, grain = une transaction, SCD Type 2, DISTKEY sur le merchant, "
             "dimensions répliquées sur chaque nœud. MongoDB pour le contexte : logs en time-series avec TTL, sessions "
             "avec events embarqués, un document de features et de prédiction par transaction ; schéma validé. Kafka au "
             "milieu : Debezium lit le WAL, publie en moins de 500 ms, PII exclues à la source. dbt transforme, Airflow "
             "orchestre. Phrase : les faits d'argent en SQL, le contexte en documents, l'analyse en colonnes — et jamais "
             "une donnée écrite deux fois à la main.")
    return s


# ───────────────────────── slide 3 — le fil rouge ─────────────────────────

def slide_3(prs):
    s = blank(prs)
    header(s, "La vie d'une transaction : sept étapes, une garantie à chacune", "Fil rouge")

    steps = [
        ("< 10 ms", "POST /charges", "Écriture OLTP, ACID\nidempotency_key : jamais deux débits", CYAN),
        ("< 500 ms", "CDC → Kafka", "Debezium lit le WAL\nordre par merchant", PURPLE),
        ("115 ms", "Décision fraude", "Redis + règles + XGBoost\nbudget 200 ms, fallback règles", ORANGE),
        ("< 1 s", "MongoDB", "features, prédiction,\ntop 3 SHAP · fraud_indicators → OLTP", ORANGE),
        ("< 5 min", "Star schema", "S3 → dbt → fact_transactions\nMERGE idempotent", GREEN),
        ("J+45", "Chargeback", "disputes : la vérité terrain\n→ label dans ml_features", RED),
        ("J+52", "Réentraînement", "dataset mature · shadow →\ncanary → production", GREEN),
    ]
    n = len(steps)
    x0, span = 0.6, 12.1
    step_w = span / n
    y_line = 3.05
    rect(s, x0 + 0.3, y_line - 0.02, span - 0.6, 0.04, LINE)
    for i, (lat, title, body, color) in enumerate(steps):
        cx = x0 + step_w * i + step_w / 2
        rect(s, cx - 0.26, y_line - 0.26, 0.52, 0.52, color, MSO_SHAPE.OVAL)
        tb(s, str(i + 1), cx - 0.26, y_line - 0.26, 0.52, 0.52, size=14, bold=True, color=WHITE,
           align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        tb(s, lat, cx - step_w / 2, 1.85, step_w, 0.5, size=20, bold=True, color=color, align=PP_ALIGN.CENTER)
        tb(s, title, cx - step_w / 2, 3.45, step_w, 0.4, size=13, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
        tb(s, body, cx - step_w / 2 + 0.05, 3.85, step_w - 0.1, 1.2, size=10, color=MUTED, align=PP_ALIGN.CENTER)

    rect(s, 0.6, 5.35, 12.1, 1.45, LIGHT, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
    bullets(s, [
        ("Chaque saut est idempotent :", "clé unique, upsert, MERGE, manifeste COPY. Kafka est at-least-once ; l'idempotence en fait un exactly-once effectif."),
        ("Si Kafka tombe,", "l'OLTP continue (CDC asynchrone). Si l'entrepôt tombe, S3 accumule et rattrape. Si le scoring tombe, les règles dures décident : on ne bloque jamais un paiement parce qu'un modèle est lent."),
        ("Le chargeback à J+45 est la vérité terrain de la fraude :", "une architecture qui ne sait pas le rattacher à la transaction d'origine ne peut pas améliorer son modèle."),
    ], 0.8, 5.45, 11.7, 1.3, size=12)

    footer(s, 3, TOTAL)
    notes(s, "1 min 30. Un merchant appelle POST /charges. En moins de 10 ms, la transaction est écrite dans PostgreSQL — "
             "ACID, clé d'idempotence : un rejeu réseau ne débitera jamais deux fois. En moins de 500 ms, Debezium l'a lue "
             "dans le WAL et publiée sur Kafka, ordonnée par merchant. Le service de scoring la consomme : features en ligne "
             "dans Redis, règles dures, XGBoost — décision en 115 ms nominal, 200 ms de budget, et si le service est lent, les "
             "règles seules décident. La prédiction et ses trois facteurs SHAP sont écrits dans MongoDB, l'indicateur de fraude "
             "dans PostgreSQL. Cinq minutes plus tard, dbt a fait la ligne du star schema — en MERGE, donc idempotent. Et "
             "quarante-cinq jours plus tard, un chargeback arrive : il devient le label de cette transaction, et le modèle "
             "réapprend dessus la semaine suivante.")
    return s


# ───────────────────────── slide 4 — conformité et ML par construction ─────────────────────────

def slide_4(prs):
    s = blank(prs)
    header(s, "La conformité est dans le code, le ML a une boucle de feedback", "Par construction")

    # Gauche : 4 extraits de code
    tb(s, "Conformité — quatre lignes qui valent un chapitre", 0.6, 1.65, 6.2, 0.4, size=15, bold=True, color=NAVY)
    snippets = [
        ("Audit immuable (PostgreSQL)", "CREATE TRIGGER trg_audit_immutable\n  BEFORE UPDATE OR DELETE ON audit_logs …\n  RAISE EXCEPTION 'append-only'"),
        ("PII exclues du CDC (Debezium)", "\"column.exclude.list\":\n  \"customers.email, transactions.ip_address\""),
        ("IP tronquée pour les analystes (vue)", "host(set_masklen(ip_address::cidr, 24))\n  -- 185.12.44.21 → 185.12.44.0"),
        ("Contrôle PII bloquant (dbt / DAG)", "dim_customer.email_hash:\n  not_accepted_values: ['%@%']   # DAG rouge"),
    ]
    y = 2.1
    for title, code in snippets:
        tb(s, title, 0.6, y, 6.2, 0.3, size=11, bold=True, color=PURPLE)
        rect(s, 0.6, y + 0.3, 6.2, 0.7, RGBColor(0x0F, 0x1B, 0x2D), MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.06)
        tb(s, code.split("\n"), 0.72, y + 0.34, 6.0, 0.66, size=9.5, color=RGBColor(0xD7, 0xE3, 0xF0), font=MONO)
        y += 1.07
    tb(s, "Aucun numéro de carte nulle part (tokenisation) · droit à l'oubli = un DAG qui détache l'identité sans supprimer les faits comptables (10 ans).",
       0.6, 6.45, 6.2, 0.55, size=10, color=MUTED, italic=True)

    # Droite : boucle de feedback
    tb(s, "ML — la boucle qui apprend des chargebacks", 7.3, 1.65, 5.5, 0.4, size=15, bold=True, color=NAVY)
    loop = [
        ("Règles dures", "sanctions, listes noires\n< 5 ms", CYAN),
        ("Modèle XGBoost", "AUC-PR 0,43 vs 0,09\n115 ms", ORANGE),
        ("Politique d'action", "allow · 3DS · review · block\nseuils sans réentraîner", PURPLE),
        ("Revue analystes", "zone grise → labels\n(biais de survie)", GREEN),
        ("Chargeback J+20…90", "vérité terrain\n→ label.is_fraud", RED),
        ("Réentraînement", "dataset mature · shadow →\ncanary · 3 dérives", GREEN),
    ]
    bx, by, bw, bh = 7.3, 2.15, 2.6, 1.05
    pos = [(bx, by), (bx + 2.9, by), (bx + 2.9, by + 1.35), (bx + 2.9, by + 2.7), (bx, by + 2.7), (bx, by + 1.35)]
    for (t, b, c), (x, y) in zip(loop, pos):
        rect(s, x, y, bw, bh, LIGHT, MSO_SHAPE.ROUNDED_RECTANGLE, line=c, radius=0.1)
        tb(s, t, x + 0.1, y + 0.08, bw - 0.2, 0.32, size=12, bold=True, color=c, align=PP_ALIGN.CENTER)
        tb(s, b, x + 0.1, y + 0.4, bw - 0.2, 0.62, size=9.5, color=INK, align=PP_ALIGN.CENTER)
    arrow(s, bx + bw, by + bh / 2, bx + 2.9, by + bh / 2)                      # 1 → 2
    arrow(s, bx + 2.9 + bw / 2, by + bh, bx + 2.9 + bw / 2, by + 1.35)          # 2 → 3
    arrow(s, bx + 2.9 + bw / 2, by + 1.35 + bh, bx + 2.9 + bw / 2, by + 2.7)    # 3 → 4
    arrow(s, bx + 2.9, by + 2.7 + bh / 2, bx + bw, by + 2.7 + bh / 2)           # 4 → 5
    arrow(s, bx + bw / 2, by + 2.7, bx + bw / 2, by + 1.35 + bh)                # 5 → 6
    arrow(s, bx + bw / 2, by + 1.35, bx + bw / 2, by + bh)                      # 6 → 1
    tb(s, "Une transaction bloquée n'est jamais contestée : sans revue humaine, le modèle ne réapprendrait que ses propres erreurs.",
       7.3, 6.15, 5.5, 0.7, size=10, color=MUTED, italic=True)

    footer(s, 4, TOTAL)
    notes(s, "1 min. Conformité : aucun numéro de carte nulle part — tokenisation, le vault est hors du périmètre. L'email et "
             "l'IP ne quittent jamais PostgreSQL : exclus du CDC par configuration, hachés ou tronqués dans l'entrepôt, chiffrés "
             "au champ dans Mongo. La table d'audit refuse UPDATE et DELETE par trigger. Un test casse le pipeline si un email "
             "en clair arrive dans les marts. Le droit à l'oubli est un DAG : il détache l'identité sans supprimer les faits "
             "financiers. ML : le modèle ne décide pas seul — règles dures avant, revue humaine dans la zone grise. Les analystes "
             "produisent des labels, parce qu'une transaction bloquée n'est jamais contestée. Déploiement shadow, canary, "
             "production ; trois dérives surveillées. Phrase : un rapport de conformité, ici, c'est la sortie d'un contrôle "
             "automatisé — pas un document qu'on rédige.")
    return s


# ───────────────────────── slide 5 — en production ─────────────────────────

def slide_5(prs):
    s = blank(prs, dark=True)
    header(s, "En production sur AWS — le pipeline tourne chaque nuit", "Déploiement", dark=True)

    # Gauche : infrastructure déployée
    tb(s, "Infrastructure (Terraform, 19 ressources, eu-north-1)", 0.6, 1.65, 6.4, 0.4, size=14, bold=True, color=CYAN)
    node(s, 0.6, 2.15, 1.6, 0.8, "terraform apply", "GitHub → cloud-init", fill=NAVY_CARD, line=CYAN, title_color=WHITE,
         sub_color=RGBColor(0xC9, 0xD6, 0xE5), title_size=11, sub_size=9)
    node(s, 2.7, 2.15, 2.3, 1.55, "EC2 m7i-flex.large", "Airflow 2.10 (LocalExecutor)\nMongoDB 7 replica set\ndocker compose",
         fill=NAVY_CARD, line=ORANGE, title_color=WHITE, sub_color=RGBColor(0xC9, 0xD6, 0xE5), title_size=12, sub_size=9.5)
    node(s, 5.5, 2.15, 1.5, 0.72, "RDS PostgreSQL 16", "stripe_oltp · stripe_olap", fill=NAVY_CARD, line=CYAN,
         title_color=WHITE, sub_color=RGBColor(0xC9, 0xD6, 0xE5), title_size=10.5, sub_size=8.5)
    node(s, 5.5, 3.0, 1.5, 0.7, "S3 chiffré", "runs/<date>/ data · results · ml", fill=NAVY_CARD, line=GREEN,
         title_color=WHITE, sub_color=RGBColor(0xC9, 0xD6, 0xE5), title_size=10.5, sub_size=8.5)
    arrow(s, 2.2, 2.55, 2.7, 2.55, color=CYAN)
    arrow(s, 5.0, 2.5, 5.5, 2.5, color=CYAN)
    arrow(s, 5.0, 3.35, 5.5, 3.35, color=GREEN)
    tb(s, "Rôle d'instance pour S3 — aucune clé AWS · security groups sur une seule IP · secrets générés par Terraform, hors dépôt",
       0.6, 3.85, 6.4, 0.55, size=10, color=RGBColor(0xC9, 0xD6, 0xE5), italic=True)

    tb(s, "DAG stripe_pipeline — 14 tâches, ≈ 25 s", 0.6, 4.45, 6.4, 0.4, size=14, bold=True, color=CYAN)
    chain = ["générer", "OLAP (dbt)", "OLTP", "NoSQL", "10 contrôles", "30 requêtes", "modèle", "S3"]
    cx = 0.6
    cw = 0.72
    for i, c in enumerate(chain):
        rect(s, cx, 4.95, cw, 0.5, GREEN if i != 4 else ORANGE, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.2)
        tb(s, c, cx, 4.95, cw, 0.5, size=8.5, bold=True, color=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, margin=0.01)
        if i < len(chain) - 1:
            arrow(s, cx + cw, 5.2, cx + cw + 0.1, 5.2, color=RGBColor(0x9F, 0xB3, 0xC8), width=1)
        cx += cw + 0.1
    tb(s, "Contrôles inter-systèmes bloquants : fact OLAP = transactions OLTP = ml_features Mongo · aucun email en clair · une version SCD2 courante par merchant.",
       0.6, 5.55, 6.4, 0.6, size=10, color=RGBColor(0xC9, 0xD6, 0xE5))

    # Droite : chiffres + écart assumé
    stat(s, 7.5, 1.75, 2.6, "25 s", "run complet, chaque nuit à 02:00 UTC")
    stat(s, 10.2, 1.75, 2.6, "10 / 10", "contrôles de cohérence verts")
    stat(s, 7.5, 3.25, 2.6, "30", "requêtes exécutées, résultats archivés", color=GREEN)
    stat(s, 10.2, 3.25, 2.6, "0,43", "AUC-PR du modèle de fraude (baseline 0,09)", color=GREEN)
    rect(s, 7.5, 4.85, 5.3, 1.6, NAVY_CARD, MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
    tb(s, "Écart assumé", 7.7, 4.95, 5.0, 0.35, size=12, bold=True, color=ORANGE)
    tb(s, "L'entrepôt de production est PostgreSQL sur RDS, pas Redshift : même star schema, même vue matérialisée, mêmes 10 requêtes. Le passage à Redshift est un changement de DDL et de connexion, pas de conception.",
       7.7, 5.3, 5.0, 1.1, size=11, color=WHITE)

    tb(s, "github.com/emelineroblot/jedha-stripe-project · vidéo du pipeline en production", 7.5, 6.55, 5.3, 0.35,
       size=10, color=RGBColor(0x9F, 0xB3, 0xC8), align=PP_ALIGN.RIGHT)
    footer(s, 5, TOTAL, dark=True)
    notes(s, "40 s. Le pipeline tourne en production sur AWS : Terraform déploie RDS PostgreSQL, une instance EC2 avec "
             "Airflow et MongoDB, et S3 ; le DAG enchaîne génération, chargement des trois systèmes, dix contrôles de cohérence "
             "inter-systèmes, les trente requêtes, le modèle et l'archivage — en vingt-cinq secondes, chaque nuit. La vidéo le "
             "montre. Le dépôt contient les schémas et leurs DDL, les trente requêtes exécutées, le connecteur Debezium, les "
             "modèles dbt et un XGBoost à 0,43 d'AUC-PR contre 0,09 de baseline. L'entrepôt de production est PostgreSQL et non "
             "Redshift — même modèle, mêmes requêtes ; c'est un changement de connexion, pas de conception. Je suis à votre "
             "disposition. — Puis se taire.")
    return s


# ───────────────────────── export PDF + PNG via PowerPoint (COM) ─────────────────────────

def export(path: Path):
    import ctypes
    import ctypes.wintypes as wt
    import pythoncom
    import win32com.client as win32

    user32 = ctypes.windll.user32

    def dismiss(pid, seconds=3.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            def cb(h, _):
                p = wt.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(p))
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(h, cls, 64)
                if p.value == pid and cls.value == "#32770" and user32.IsWindowVisible(h):
                    found.append(h)
                return True

            user32.EnumWindows(cb, 0)
            for h in found:
                user32.PostMessageW(h, 0x0010, 0, 0)
            time.sleep(0.5)

    pythoncom.CoInitialize()
    app = win32.DispatchEx("PowerPoint.Application")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process POWERPNT).Id"],
                         capture_output=True, text=True).stdout.split()
    pid = max(int(x) for x in out if x.isdigit())
    try:
        pres = app.Presentations.Open(str(path), True, False, False)
        dismiss(pid)
        pres.SaveAs(str(path.with_suffix(".pdf")), 32)
        png_dir = OUT / "png"
        png_dir.mkdir(exist_ok=True)
        for i, sl in enumerate(pres.Slides, start=1):
            sl.Export(str(png_dir / f"slide-{i}.png"), "PNG", 1920, 1080)
        pres.Close()
        print(f"{path.name}: {len(list(Presentation(str(path)).slides))} slides → PDF + PNG")
    finally:
        app.Quit()


if __name__ == "__main__":
    prs = new_prs()
    for build in (slide_1, slide_2, slide_3, slide_4, slide_5):
        build(prs)
    out = OUT / "stripe-architecture-soutenance.pptx"
    prs.save(out)
    print(f"écrit : {out}")
    if "--no-export" not in sys.argv:
        export(out)
