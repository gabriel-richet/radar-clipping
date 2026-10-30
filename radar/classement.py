"""Rank the campaigns collected by collect.py by what a clipper really earns there.

Input: campaigns.json. Output: RADAR.md (report in French) + campagnes.csv (every campaign, one line each).
"""
import collections
import csv
import datetime
import json
import os
import re
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://contentrewards.com/c/campaigns/"

FR_NAME = re.compile(r"\bFR\b|fran[cç]ais|\bfrance\b|francophone", re.I)
FR_WORDS = set("le la les des une pour vous vos et est sur avec du que qui dans chaque vues vidéos gagnez clippe".split())
EN_WORDS = set("the and for you your with of to is are this that get paid views clips post".split())


def language(c):
    text = (c["name"] + " " + (c.get("description") or "")).lower()
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", text)
    fr = sum(w in FR_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    if FR_NAME.search(c["name"]) or (fr >= 4 and fr > 2 * en):
        return "fr"
    return "en" if en >= fr else "autre"


def row(c, now):
    m = c["metrics"]
    budget = c["budgetCents"] / 100
    spent = m["budgetSpentCents"] / 100
    clips = m["approvedSubmissionCount"]
    creators = m["creatorCount"]
    views = m["totalViews"]
    start = datetime.datetime.fromisoformat((c.get("launchDate") or c["createdAt"]).replace("Z", "+00:00"))
    age = max((now - start).total_seconds() / 86400, 0.5)
    per_day = spent / age
    left = max(budget - spent, 0)
    rate = c["primaryPayoutCents"] / 100 if c["payoutType"] == "cpm" else None
    pay = (c.get("payouts") or [{}])[0]
    r = {
        "id": c["id"], "nom": c["name"].strip(), "organisateur": c["organizationName"], "langue": language(c),
        "categories": ",".join(k["id"] for k in c["categories"]), "type": c["payoutType"], "taux": rate,
        "plateformes": ",".join(p[:2] for p in c["platforms"]), "candidature": c["requiresApplication"],
        "budget": budget, "restant": left, "age_jours": age, "jours_restants": left / per_day if per_day else None,
        "clippeurs": creators, "clips": clips, "vues": views,
        "vues_par_clip": views / clips if clips else None,
        "gain_par_clip": spent / clips if clips else None,
        "gain_par_clippeur": spent / creators if creators else None,
        # share of the displayed rate really paid once caps, thresholds and unpaid views are counted
        "cpm_reel": spent / views * 1000 if views else None,
        "seuil_vues": pay.get("minPayoutCents", 0) / pay["rateCents"] * 1000 if pay.get("rateCents") and rate else None,
        "plafond": pay.get("maxPayoutCents", 0) / 100 or None,
        "lien": URL + c["id"],
    }
    r.update(interest(r))
    return r


def interest(r):
    """Score out of 100 for a French-speaking clipper, with the reasons behind it.

    Points: budget left per clipper 25, days of budget 15, views per clip 15, share of views really paid 10,
    rate 10, open access 10, payment threshold 5, French 10. A heuristic to sort the list, not a forecast.
    """
    pts, plus, minus = 0.0, [], []
    share = r["restant"] / (r["clippeurs"] + 5)
    pts += 25 * min(share / 500, 1)
    if share >= 300:
        plus.append(f"{f(share)} $ de budget par clippeur")
    elif share < 60:
        minus.append("budget très partagé")

    days = r["jours_restants"]
    if days is None:
        pts += 8
    else:
        pts += 15 * min(days / 30, 1)
        if days >= 30:
            plus.append("budget pour plus d'un mois")
        elif days < 7:
            minus.append("budget presque épuisé" if days < 1 else f"budget épuisé dans {f(days)} jours environ")

    proven = r["clips"] >= 10
    if proven:
        pts += 15 * min(r["vues_par_clip"] / 20000, 1)
        if r["vues_par_clip"] >= 10000:
            plus.append(f"{f(r['vues_par_clip'])} vues par clip en moyenne")
        elif r["vues_par_clip"] < 3000:
            minus.append(f"seulement {f(r['vues_par_clip'])} vues par clip")
    else:
        pts += 5
        minus.append("pas encore de résultats pour juger")

    if proven and r["cpm_reel"] is not None and r["taux"]:
        paid = min(r["cpm_reel"] / r["taux"], 1)
        pts += 10 * min(paid / 0.6, 1)
        if paid < 0.25:
            minus.append(f"seulement {f(paid * 100)} % des vues sont payées")
    else:
        pts += 5

    if r["taux"]:
        pts += 10 * min(r["taux"] / 3, 1)
        if r["taux"] >= 2:
            plus.append(f"{f(r['taux'], 2)} $ pour 1 000 vues")
    else:
        minus.append("pas payée aux vues")

    if r["candidature"]:
        pts += 3
        minus.append("sur candidature")
    else:
        pts += 10

    seuil = r["seuil_vues"]
    if seuil is None:
        pts += 3
    elif seuil <= 2000:
        pts += 5
    elif seuil <= 5000:
        pts += 3
    else:
        minus.append(f"rien n'est payé sous {f(seuil)} vues par clip")
    if r["plafond"] and r["plafond"] <= 50:
        minus.append(f"plafonnée à {f(r['plafond'])} $ par clip")

    if r["langue"] == "fr":
        pts += 10
        plus.append("en français, peu de concurrence")
    else:
        minus.append("pas en français")

    score = round(pts)
    if r["restant"] < 300 or (days is not None and days < 3):
        verdict = "à éviter"
    elif score >= 65:
        verdict = "à cibler"
    elif score >= 45:
        verdict = "à surveiller"
    else:
        verdict = "à éviter"
    return {"score": score, "verdict": verdict, "pour": plus[:3], "contre": minus[:3]}


def f(v, nd=0, suffix=""):
    if v is None:
        return "–"
    s = f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s + suffix


def table(rows, n=None):
    out = ["| Campagne | $/1K | Restant | Clippeurs | Vues/clip | $/clip | $/clippeur | Jours restants | Accès |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:n]:
        out.append(f"| [{r['nom'][:52].replace('|', '/')}]({r['lien']}) | {f(r['taux'], 2)} | {f(r['restant'])} $ | "
                   f"{r['clippeurs']} | {f(r['vues_par_clip'])} | {f(r['gain_par_clip'], 2)} | "
                   f"{f(r['gain_par_clippeur'])} | {f(r['jours_restants'])} | "
                   f"{'candidature' if r['candidature'] else 'libre'} |")
    return out


def tot(rs, k):
    return sum(r[k] or 0 for r in rs)


def spent(rs):
    return sum(r["budget"] - r["restant"] for r in rs)



def main():
    data = json.load(open(os.path.join(HERE, "campaigns.json"), encoding="utf-8"))
    now = datetime.datetime.fromisoformat(data["collected_at"])
    rows = [row(c, now) for c in data["campaigns"]]

    with open(os.path.join(HERE, "campagnes.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})

    cpm = [r for r in rows if r["taux"]]
    # a campaign is "proven" when enough clips were paid to trust its averages, and "open" when money is left for a while
    proven = [r for r in cpm if r["clips"] >= 20 and r["clippeurs"] >= 5]
    open_ = [r for r in proven if r["restant"] >= 500 and (r["jours_restants"] or 0) >= 5]
    fr = sorted([r for r in rows if r["langue"] == "fr" and r["restant"] >= 100], key=lambda r: -r["restant"])
    new = sorted([r for r in cpm if r["age_jours"] <= 7 and r["restant"] >= 1000 and r["clippeurs"] < 15],
                 key=lambda r: -r["restant"])

    cats = collections.defaultdict(list)
    for r in rows:
        for k in r["categories"].split(","):
            cats[k or "(sans catégorie)"].append(r)

    rates = sorted(r["taux"] for r in cpm)
    L = [f"# Radar des campagnes — {now.astimezone().strftime('%d/%m/%Y %H:%M')}", "",
         f"{len(rows)} campagnes publiques sur Content Rewards, {f(tot(rows, 'restant'))} $ encore à distribuer.", "",
         "## Les chiffres réels du marché", "",
         f"- Taux affiché médian : **{f(statistics.median(rates), 2)} $ / 1 000 vues** "
         f"(9 campagnes sur 10 sont à {f(rates[int(len(rates) * .9)], 2)} $ ou moins).",
         f"- Taux réellement payé : **{f(spent(rows) / tot(rows, 'vues') * 1000, 2)} $ / 1 000 vues** "
         "(plafonds, seuils minimum et vues non payées compris).",
         f"- Un clip approuvé fait en moyenne {f(tot(rows, 'vues') / tot(rows, 'clips'))} vues et rapporte "
         f"{f(spent(rows) / tot(rows, 'clips'), 2)} $.",
         f"- Gain médian d'un clippeur sur une campagne : **{f(statistics.median(r['gain_par_clippeur'] for r in proven))} $** "
         "(les moyennes sont tirées vers le haut par quelques gros comptes).", "",
         "## Campagnes en français", "",
         "Triées par budget restant. C'est là que la concurrence est la plus faible.", ""]
    L += table(fr)
    L += ["", "## Campagnes à cibler", "",
          "Note sur 100 : budget par clippeur, durée du budget, vues par clip, part des vues payée, taux, accès, "
          "seuil de paiement, langue. C'est un tri, pas une prévision de gains.", "",
          "| Campagne | Note | Avis | Pour | Contre |", "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["score"])[:20]:
        L.append(f"| [{r['nom'][:52].replace('|', '/')}]({r['lien']}) | {r['score']} | {r['verdict']} | "
                 f"{' ; '.join(r['pour']) or '–'} | {' ; '.join(r['contre']) or '–'} |")
    L += ["", "## Meilleures campagnes ouvertes (toutes langues)", "",
          "Au moins 20 clips payés, 500 $ restants et 5 jours de budget. Triées par gain moyen par clippeur.", ""]
    L += table(sorted(open_, key=lambda r: -r["gain_par_clippeur"]), 25)
    L += ["", "## Nouvelles campagnes peu disputées", "",
          "Lancées depuis moins de 7 jours, au moins 1 000 $ restants, moins de 15 clippeurs.", ""]
    L += table(new, 20)
    L += ["", "## Niches", "",
          "Une campagne compte dans chacune de ses catégories. Triées par gain médian par clippeur "
          "(campagnes avec 20 clips payés ou plus).", "",
          "| Niche | Campagnes | $/1K médian | Restant | Clippeurs | Vues/clip | $/clip | $/clippeur médian | $/1K réel |",
          "|---|---|---|---|---|---|---|---|---|"]
    stats = []
    for k, rs in cats.items():
        pv = [r["gain_par_clippeur"] for r in rs if r in proven]
        if len(rs) < 5 or len(pv) < 3:
            continue
        stats.append((statistics.median(pv), k, rs))
    for med, k, rs in sorted(stats, reverse=True):
        rs_rates = [r["taux"] for r in rs if r["taux"]]
        L.append(f"| {k} | {len(rs)} | {f(statistics.median(rs_rates), 2)} | {f(tot(rs, 'restant'))} $ | "
                 f"{tot(rs, 'clippeurs')} | {f(tot(rs, 'vues') / max(tot(rs, 'clips'), 1))} | "
                 f"{f(spent(rs) / max(tot(rs, 'clips'), 1), 2)} | {f(med)} | "
                 f"{f(spent(rs) / max(tot(rs, 'vues'), 1) * 1000, 2)} |")
    L += ["", "## Comment lire", "",
          "- **$/clippeur** : budget dépensé ÷ nombre de clippeurs. C'est ce qu'un participant a touché en moyenne.",
          "- **Jours restants** : budget restant ÷ rythme de dépense depuis le lancement.",
          "- Les campagnes « candidature » demandent d'être accepté par l'organisateur.",
          "- Toutes les campagnes sont dans `campagnes.csv` (ouvrable dans Excel).",
          "- Mise à jour : `python collect.py` puis `python classement.py`."]
    with open(os.path.join(HERE, "RADAR.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(len(rows), len(fr), len(open_), len(new))


if __name__ == "__main__":
    main()
