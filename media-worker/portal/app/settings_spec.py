"""The viral_config keys the Settings page can change: type, limits and help text. Only these keys can be written
from the portal; validation happens here, before anything is sent to n8n."""

# hours (New York) at which the Publisher (03) trigger runs: a slot outside them would never fire
PUBLISHER_TRIGGER_HOURS = [11, 12, 15, 16, 19, 20]
THEMES = [("yellow", "#FFD221", "giallo"), ("red", "#FF4545", "rosso"), ("blue", "#3DA9FF", "blu"),
          ("green", "#3DFF8B", "verde"), ("purple", "#B45CFF", "viola")]

GROUPS = [
    ("pubblicazione", "Pubblicazione Instagram", [
        {"key": "publish_enabled", "kind": "bool", "label": "Pubblicazione automatica",
         "help": "Spenta: il Publisher non pubblica nulla negli slot (resta solo la pubblicazione forzata di prova)."},
        {"key": "publish_max_per_day", "kind": "int", "min": 1, "max": 6, "label": "Reel al giorno",
         "help": "Massimo di Reel pubblicati in un giorno di New York. Su un account giovane meglio salire piano."},
        {"key": "publish_slots_et", "kind": "hours", "choices": PUBLISHER_TRIGGER_HOURS, "label": "Slot orari (New York)",
         "help": "Ore in cui può uscire un Reel. Si possono scegliere solo le ore in cui il workflow 03 parte."},
        {"key": "publish_min_gap_h", "kind": "num", "min": 1, "max": 12, "step": 0.5, "label": "Ore minime tra due Reel"},
        {"key": "publish_ready_max_age_h", "kind": "num", "min": 6, "max": 96, "step": 1, "label": "Soglia STALE (ore)",
         "help": "Un Reel pronto da più ore di così non viene più pubblicato: il trend è passato."},
    ]),
    ("factory", "Factory", [
        {"key": "factory_max_reels_per_day", "kind": "int", "min": 1, "max": 10, "label": "Reel prodotti in 24 ore",
         "help": "Ogni Reel costa una chiamata a Claude e un render sul server."},
        {"key": "factory_ready_buffer_max", "kind": "int", "min": 1, "max": 10, "label": "Reel pronti massimi (buffer)",
         "help": "Con tanti Reel pronti la Factory si ferma: non ha senso prepararne troppi che poi diventano STALE."},
        {"key": "factory_creator_cooldown_h", "kind": "num", "min": 0, "max": 72, "step": 1,
         "label": "Pausa tra due Reel dello stesso streamer (ore)"},
        {"key": "factory_min_views", "kind": "int", "min": 0, "max": 100000, "label": "Views minime della clip Twitch"},
        {"key": "factory_voice_ratio", "kind": "num", "min": 0, "max": 1, "step": 0.1, "label": "Quota di Reel con voce AI",
         "help": "0 = mai, 1 = sempre, 0,5 = metà (il test A/B voce sì / voce no)."},
        {"key": "factory_max_segment_s", "kind": "int", "min": 15, "max": 75, "label": "Durata massima della clip (secondi)"},
    ]),
    ("temi", "Template grafici", [
        {"key": "factory_themes", "kind": "themes", "label": "Temi in rotazione",
         "help": "La Factory alterna questi colori in modo bilanciato (mai lo stesso dei 2 Reel precedenti). Almeno 2."},
        {"key": "factory_force_theme", "kind": "choice", "choices": [""] + [t[0] for t in THEMES],
         "label": "Tema forzato (solo prove)", "help": "Vuoto = rotazione normale."},
    ]),
    ("social", "Altri social", [
        {"key": "fb_enabled", "kind": "bool", "label": "Facebook: ripubblica ogni Reel sulla Pagina",
         "help": "Workflow 07, circa 1h30 dopo lo slot Instagram."},
        {"key": "yt_enabled", "kind": "bool", "label": "YouTube Shorts automatici",
         "help": "Serve anche il workflow 06 attivo (oggi è spento, in attesa dell'audit di YouTube)."},
        {"key": "yt_privacy", "kind": "choice", "choices": ["private", "unlisted", "public"], "label": "Visibilità degli Shorts"},
        {"key": "yt_max_per_day", "kind": "int", "min": 1, "max": 6, "label": "Shorts al giorno"},
    ]),
    ("prove", "Prove (lasciare vuoto)", [
        {"key": "publish_force_post_key", "kind": "text", "label": "Pubblica subito questo post_key (Instagram)"},
        {"key": "factory_force_candidate_key", "kind": "text", "label": "Monta subito questa clip (candidate_key)"},
        {"key": "fb_force_post_key", "kind": "text", "label": "Pubblica subito su Facebook (post_key)"},
        {"key": "yt_force_post_key", "kind": "text", "label": "Carica subito su YouTube (post_key)"},
    ]),
]
FIELDS = {f["key"]: f for _, _, fields in GROUPS for f in fields}


class Invalid(ValueError):
    pass


def column(field: dict) -> str:
    return "value_number" if field["kind"] in ("bool", "int", "num") else "value_string"


def parse(field: dict, form) -> object:
    """The value to save for one field from the submitted form (raises Invalid with an Italian message)."""
    key, kind, label = field["key"], field["kind"], field["label"]
    if kind == "bool":
        return 1 if form.get(key) == "1" else 0
    if kind == "hours":
        hours = sorted({int(h) for h in form.getlist(key) if h.isdigit()})
        if not hours:
            raise Invalid(f"{label}: scegli almeno uno slot")
        if any(h not in field["choices"] for h in hours):
            raise Invalid(f"{label}: solo le ore in cui parte il workflow 03 ({', '.join(map(str, field['choices']))})")
        return ",".join(str(h) for h in hours)
    if kind == "themes":
        names = [t[0] for t in THEMES if t[0] in form.getlist(key)]
        if len(names) < 2:
            raise Invalid(f"{label}: servono almeno 2 temi per la rotazione")
        return ",".join(names)
    raw = str(form.get(key, "")).strip()
    if kind in ("int", "num"):
        try:
            v = float(raw.replace(",", "."))
        except ValueError:
            raise Invalid(f"{label}: «{raw}» non è un numero") from None
        if kind == "int" and v != int(v):
            raise Invalid(f"{label}: serve un numero intero")
        if not field["min"] <= v <= field["max"]:
            raise Invalid(f"{label}: deve essere tra {field['min']:g} e {field['max']:g}")
        return int(v) if kind == "int" else v
    if kind == "choice":
        if raw not in field["choices"]:
            raise Invalid(f"{label}: valore non valido")
        return raw
    if len(raw) > 200 or any(c in raw for c in "<>\"'"):
        raise Invalid(f"{label}: valore non valido")
    return raw


def same(a, b) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a if a is not None else "") == str(b if b is not None else "")
