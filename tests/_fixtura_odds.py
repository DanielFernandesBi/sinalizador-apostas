"""Fixture compartilhada: um evento no formato The Odds API (v4)."""


def bookmaker(key, home, away, *, ts="2026-07-20T18:31:00Z", ts_casa="2026-07-20T18:30:05Z"):
    return {
        "key": key,
        "title": key.title(),
        "last_update": ts_casa,
        "markets": [
            {"key": "h2h", "last_update": ts, "outcomes": [
                {"name": home, "price": 2.10},
                {"name": away, "price": 3.40},
                {"name": "Draw", "price": 3.30},
            ]},
            {"key": "totals", "last_update": ts, "outcomes": [
                {"name": "Over", "price": 1.90, "point": 2.5},
                {"name": "Under", "price": 1.95, "point": 2.5},
            ]},
            {"key": "spreads", "last_update": ts, "outcomes": [
                {"name": home, "price": 1.95, "point": -0.5},
                {"name": away, "price": 1.95, "point": 0.5},
            ]},
        ],
    }


def evento(id_="ev1", home="Arsenal", away="Chelsea", casas=("pinnacle",)):
    return {
        "id": id_,
        "sport_key": "soccer_epl",
        "sport_title": "EPL",
        "commence_time": "2026-07-20T19:00:00Z",
        "home_team": home,
        "away_team": away,
        "bookmakers": [bookmaker(k, home, away) for k in casas],
    }
