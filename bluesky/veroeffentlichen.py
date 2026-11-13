#!/usr/bin/env python3
"""Veröffentlicht fällige Reels aus plan.json auf Bluesky.

Läuft als GitHub-Ablauf alle 30 Minuten. Ein Reel gilt als fällig, wenn sein
Termin erreicht ist und höchstens 12 Stunden zurückliegt. Jedes veröffentlichte
Reel wird in bluesky/gepostet.json festgehalten und nie doppelt gesendet.
Nur Standardbibliothek, keine Abhängigkeiten.
"""
import json, os, sys, time, re, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

HANDLE = os.environ.get("BSKY_HANDLE", "bastianaue.com")
PASSWORT = os.environ.get("BSKY_APP_PASSWORD", "")
FENSTER = timedelta(hours=12)
HIER = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(HIER, "..", "plan.json")
LOG = os.path.join(HIER, "gepostet.json")
NUR_TEST = os.environ.get("NUR_TEST") == "1"


def anfrage(url, daten=None, kopf=None, methode=None, roh=False, zeit=120):
    kopf = dict(kopf or {})
    if daten is not None and not isinstance(daten, (bytes, bytearray)):
        daten = json.dumps(daten).encode()
        kopf.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=daten, headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=zeit) as r:
            inhalt = r.read()
    except urllib.error.HTTPError as e:
        inhalt = e.read()
        try:
            j = json.loads(inhalt)
        except Exception:
            j = {"raw": inhalt[:300].decode(errors="replace")}
        j["_status"] = e.code
        return j
    if roh:
        return inhalt
    return json.loads(inhalt) if inhalt else {}


def jetzt():
    return datetime.now(timezone.utc)


def facetten(text):
    erg = []
    for m in re.finditer(r"bastianaue\.com", text):
        anf = len(text[: m.start()].encode("utf-8"))
        end = anf + len(m.group(0).encode("utf-8"))
        erg.append({"index": {"byteStart": anf, "byteEnd": end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://bastianaue.com"}]})
    return erg


def anmelden():
    s = anfrage("https://bsky.social/xrpc/com.atproto.server.createSession",
                {"identifier": HANDLE, "password": PASSWORT})
    if "accessJwt" not in s:
        sys.exit("Anmeldung bei Bluesky fehlgeschlagen: %s" % s)
    pds = "https://bsky.social"
    for d in (s.get("didDoc") or {}).get("service", []):
        if d.get("type") == "AtprotoPersonalDataServer":
            pds = d["serviceEndpoint"].rstrip("/")
    return s["accessJwt"], s["did"], pds


def video_hochladen(jwt, did, pds, daten, name):
    host = urllib.parse.urlparse(pds).hostname
    exp = int(time.time()) + 1800
    sa = anfrage(pds + "/xrpc/com.atproto.server.getServiceAuth?" + urllib.parse.urlencode(
        {"aud": "did:web:" + host, "lxm": "com.atproto.repo.uploadBlob", "exp": exp}),
        kopf={"Authorization": "Bearer " + jwt})
    if "token" in sa:
        r = anfrage("https://video.bsky.app/xrpc/app.bsky.video.uploadVideo?" + urllib.parse.urlencode(
            {"did": did, "name": name}), daten,
            {"Authorization": "Bearer " + sa["token"], "Content-Type": "video/mp4"}, "POST", zeit=300)
        job = (r.get("jobStatus") or r).get("jobId") or r.get("jobId")
        if job:
            for _ in range(60):
                st = anfrage("https://video.bsky.app/xrpc/app.bsky.video.getJobStatus?jobId=" + job)
                js = st.get("jobStatus", {})
                if js.get("blob"):
                    return js["blob"]
                if js.get("state") == "JOB_STATE_FAILED":
                    print("Videodienst meldet Fehler:", js, file=sys.stderr)
                    break
                time.sleep(5)
        else:
            print("Videodienst ohne Auftrag:", r, file=sys.stderr)
    # Rückfall: direkt als Blob beim eigenen Server hochladen
    r = anfrage(pds + "/xrpc/com.atproto.repo.uploadBlob", daten,
                {"Authorization": "Bearer " + jwt, "Content-Type": "video/mp4"}, "POST", zeit=300)
    if "blob" not in r:
        raise RuntimeError("Video-Upload fehlgeschlagen: %s" % r)
    return r["blob"]


def schon_auf_bluesky(did, text, termin):
    f = anfrage("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?" + urllib.parse.urlencode(
        {"actor": did, "limit": 40, "filter": "posts_no_replies"}))
    for it in f.get("feed", []):
        rec = it.get("post", {}).get("record", {})
        if (rec.get("text") or "").strip() != text.strip():
            continue
        try:
            am = datetime.fromisoformat(rec.get("createdAt", "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs(am - termin) <= FENSTER:
            return it["post"]["uri"]
    return None


def main():
    plan = json.load(open(PLAN, encoding="utf-8"))
    log = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {}
    t = jetzt()
    faellig = []
    for e in plan:
        termin = datetime.fromisoformat(e["termin"])
        name = e["video"].rsplit("/", 1)[-1]
        if name in log:
            continue
        if termin <= t and t - termin <= FENSTER:
            faellig.append((termin, name, e))
    faellig.sort(key=lambda x: x[0])
    if NUR_TEST:
        print("Fällig:", [n for _, n, _ in faellig])
    if not faellig:
        print("Nichts fällig.")
        return
    if not PASSWORT:
        sys.exit("Kein App-Passwort hinterlegt (Geheimnis BSKY_APP_PASSWORD).")
    jwt, did, pds = anmelden()
    for termin, name, e in faellig:
        text = e["kurz"]
        vorhanden = schon_auf_bluesky(did, text, termin)
        if vorhanden:
            log[name] = {"uri": vorhanden, "am": t.isoformat(), "hinweis": "bereits vorhanden"}
            print(name, "war bereits auf Bluesky.")
            continue
        daten = anfrage(e["video"], roh=True, zeit=300)
        blob = video_hochladen(jwt, did, pds, daten, name)
        rec = {"$type": "app.bsky.feed.post", "text": text,
               "createdAt": jetzt().isoformat().replace("+00:00", "Z"), "langs": ["en"],
               "embed": {"$type": "app.bsky.embed.video", "video": blob,
                         "aspectRatio": {"width": 1080, "height": 1920}}}
        fc = facetten(text)
        if fc:
            rec["facets"] = fc
        r = anfrage(pds + "/xrpc/com.atproto.repo.createRecord",
                    {"repo": did, "collection": "app.bsky.feed.post", "record": rec},
                    {"Authorization": "Bearer " + jwt})
        if "uri" not in r:
            raise RuntimeError("Beitrag fehlgeschlagen für %s: %s" % (name, r))
        log[name] = {"uri": r["uri"], "am": jetzt().isoformat()}
        print(name, "veröffentlicht:", r["uri"])
        json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
