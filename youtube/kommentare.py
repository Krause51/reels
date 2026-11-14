#!/usr/bin/env python3
"""Blendet jeden Kommentar unter den Videos des Kanals aus (Status "rejected").

Benötigt die Umgebungsvariablen YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN.
Läuft stündlich über GitHub Actions; verwendet nur die Standardbibliothek.
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

API = "https://www.googleapis.com/youtube/v3"


def anfrage(url, daten=None, token=None, methode=None):
    kopf = {}
    if token:
        kopf["Authorization"] = "Bearer " + token
    body = None
    if daten is not None:
        body = urllib.parse.urlencode(daten).encode()
        kopf["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            inhalt = r.read()
            return json.loads(inhalt) if inhalt else {}
    except urllib.error.HTTPError as e:
        print("Fehler", e.code, url.split("?")[0], e.read().decode()[:500])
        raise


def zugang():
    antwort = anfrage("https://oauth2.googleapis.com/token", {
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    return antwort["access_token"]


def main():
    token = zugang()
    kanal = anfrage(API + "/channels?part=id&mine=true", token=token)["items"][0]["id"]
    zu_ausblenden, seite = [], None
    # sichtbare und zur Prüfung zurückgehaltene Kommentare einsammeln
    for status in ("published", "heldForReview"):
        seite = None
        while True:
            q = {"part": "id", "allThreadsRelatedToChannelId": kanal,
                 "maxResults": 100, "moderationStatus": status}
            if seite:
                q["pageToken"] = seite
            r = anfrage(API + "/commentThreads?" + urllib.parse.urlencode(q), token=token)
            zu_ausblenden += [t["id"] for t in r.get("items", [])]
            seite = r.get("nextPageToken")
            if not seite or len(zu_ausblenden) >= 150:
                break
    zu_ausblenden = list(dict.fromkeys(zu_ausblenden))[:150]  # Tageskontingent schonen
    for i in range(0, len(zu_ausblenden), 50):
        teil = zu_ausblenden[i:i + 50]
        anfrage(API + "/comments/setModerationStatus?" + urllib.parse.urlencode(
            {"id": ",".join(teil), "moderationStatus": "rejected"}), daten={}, token=token, methode="POST")
    print(f"{len(zu_ausblenden)} Kommentar(e) ausgeblendet.")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        print("Fehlendes Geheimnis:", e)
        sys.exit(1)
