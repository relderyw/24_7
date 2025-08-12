import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ---------------- CONFIGURAÇÃO ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "6569266928:AAHm7pOJVsd3WKzJEgdVDez4ZYdCAlRoYO8")
CHAT_ID = os.getenv("CHAT_ID", "-1001981134607")
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

LOOP_INTERVAL = 30
MAX_SEND_WORKERS = 4
SENT_FILE = "sent.json"
RELATORIO_FILE = "relatorio.json"
_sent_lock = Lock()

# Carrega mensagens enviadas
def load_sent_messages():
    if not os.path.exists(SENT_FILE):
        return set()
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        print("⚠️ Erro ao carregar sent.json:", e)
        return set()

def save_sent_messages(sent_set):
    tmp = SENT_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(sent_set), f, ensure_ascii=False, indent=2)
        os.replace(tmp, SENT_FILE)
    except Exception as e:
        print("⚠️ Erro ao salvar sent.json:", e)

sent_messages = load_sent_messages()

# ------------- Fuso Horário ---------------
try:
    from zoneinfo import ZoneInfo
    SP_TZ = ZoneInfo("America/Manaus")
except ImportError:
    try:
        import pytz
        SP_TZ = pytz.timezone("America/Manaus")
    except ImportError:
        SP_TZ = None
except Exception:
    SP_TZ = None

def format_datetime_for_display(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if SP_TZ:
            dt = dt.astimezone(SP_TZ)
        else:
            dt = dt.astimezone()
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return iso_str

# Formata timedelta como MM:SS
def format_timedelta(td):
    total_seconds = int(td.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"

# ------------- Lógica de Estatísticas Aprimorada -------------

RECENT_WEIGHTS = [0.4, 0.3, 0.2, 0.07, 0.03]  # Peso decrescente

def parse_score(score, scoreHT=None):
    def safe_int(value, default=0):
        if value is None: return default
        try: return int(value)
        except: return default
    homeFT = safe_int(score.get("home", 0)) if score else 0
    awayFT = safe_int(score.get("away", 0)) if score else 0
    homeHT = safe_int(scoreHT.get("home", homeFT // 2)) if scoreHT else homeFT // 2
    awayHT = safe_int(scoreHT.get("away", awayFT // 2)) if scoreHT else awayFT // 2
    return {
        "homeGoals": homeFT, "awayGoals": awayFT,
        "totalGoals": homeFT + awayFT,
        "homeGoalsHT": homeHT, "awayGoalsHT": awayHT,
        "totalGoalsHT": homeHT + awayHT
    }

def get_recent_games(playerName, allGames, limit=5):
    games = [g for g in allGames if (g.get("home", {}).get("name") == playerName or g.get("away", {}).get("name") == playerName)]
    return sorted(games, key=lambda x: x.get("startTime", ""), reverse=True)[:limit]

def get_player_stats(playerName, allGames):
    games = get_recent_games(playerName, allGames, 5)
    if not games:
        return {key: 0 for key in [
            "winRate", "avgGoals", "over15HT", "over25HT", "over35HT", "bttsHT",
            "over15FT", "over25FT", "over35FT", "over45FT", "over55FT", "over65FT", "bttsFT"
        ]}

    wins = 0
    goalsScored = 0
    total_goals = 0
    counters = {k: 0 for k in [
        "over15HT", "over25HT", "over35HT", "bttsHT",
        "over15FT", "over25FT", "over35FT", "over45FT", "over55FT", "over65FT", "bttsFT"
    ]}

    for idx, g in enumerate(games):
        score = parse_score(g.get("score"), g.get("scoreHT"))
        home = g.get("home", {}).get("name")
        isHome = (home == playerName)
        scored = score["homeGoals"] if isHome else score["awayGoals"]
        conceded = score["awayGoals"] if isHome else score["homeGoals"]
        if scored > conceded:
            wins += RECENT_WEIGHTS[idx]
        goalsScored += scored * RECENT_WEIGHTS[idx]
        total_goals += score["totalGoals"] * RECENT_WEIGHTS[idx]

        w = RECENT_WEIGHTS[idx]
        if score["totalGoalsHT"] > 1.5: counters["over15HT"] += w
        if score["totalGoalsHT"] > 2.5: counters["over25HT"] += w
        if score["totalGoalsHT"] > 3.5: counters["over35HT"] += w
        if score["homeGoalsHT"] > 0 and score["awayGoalsHT"] > 0: counters["bttsHT"] += w
        if score["totalGoals"] > 1.5: counters["over15FT"] += w
        if score["totalGoals"] > 2.5: counters["over25FT"] += w
        if score["totalGoals"] > 3.5: counters["over35FT"] += w
        if score["totalGoals"] > 4.5: counters["over45FT"] += w
        if score["totalGoals"] > 5.5: counters["over55FT"] += w
        if score["totalGoals"] > 6.5: counters["over65FT"] += w
        if score["homeGoals"] > 0 and score["awayGoals"] > 0: counters["bttsFT"] += w

    avg_goals = round(goalsScored / sum(RECENT_WEIGHTS[:len(games)]), 1)
    total_weight = sum(RECENT_WEIGHTS[:len(games)])

    return {
        "winRate": round((wins / total_weight) * 100),
        "avgGoals": avg_goals,
        "avgGoalsFT": round(total_goals / total_weight, 1),
        **{k: round(v / total_weight * 100) for k, v in counters.items()}
    }

def get_league_stats(leagueName, allGames):
    games = [g for g in allGames if g.get("competition", {}).get("name") == leagueName]
    if len(games) < 5:
        return None

    counters = {k: 0 for k in [
        "over15HT", "over25HT", "over35HT", "bttsHT",
        "over15FT", "over25FT", "over35FT", "over45FT", "over55FT", "bttsFT"
    ]}
    total_goals_ht = 0
    total_goals_ft = 0

    for g in games[:10]:
        score = parse_score(g.get("score"), g.get("scoreHT"))
        total_goals_ht += score["totalGoalsHT"]
        total_goals_ft += score["totalGoals"]

        if score["totalGoalsHT"] > 1.5: counters["over15HT"] += 1
        if score["totalGoalsHT"] > 2.5: counters["over25HT"] += 1
        if score["totalGoalsHT"] > 3.5: counters["over35HT"] += 1
        if score["homeGoalsHT"] > 0 and score["awayGoalsHT"] > 0: counters["bttsHT"] += 1
        if score["totalGoals"] > 1.5: counters["over15FT"] += 1
        if score["totalGoals"] > 2.5: counters["over25FT"] += 1
        if score["totalGoals"] > 3.5: counters["over35FT"] += 1
        if score["totalGoals"] > 4.5: counters["over45FT"] += 1
        if score["totalGoals"] > 5.5: counters["over55FT"] += 1
        if score["homeGoals"] > 0 and score["awayGoals"] > 0: counters["bttsFT"] += 1

    total = len(games[:10])
    return {
        **{k: round(v / total * 100) for k, v in counters.items()},
        "avgGoalsHT": round(total_goals_ht / total, 2),
        "avgGoalsFT": round(total_goals_ft / total, 2),
        "gameCount": total
    }

# --- Mapeamento de Liga -> Previsão para Backtest ---
PREVISOES = {
    "Esoccer Battle - 8 mins play": {"tipo": "over", "gols": 4.5, "threshold": 5},
    "Esoccer H2H GG League - 8 mins play": {"tipo": "over", "gols": 3.5, "threshold": 4},
    "Esoccer Battle Volta - 6 mins play": {"tipo": "over", "gols": 5.5, "threshold": 6},
    "Esoccer GT Leagues – 12 mins play": {"tipo": "over", "gols": 4.5, "threshold": 5},
    "Esoccer Adriatic League - 10 mins play": {"tipo": "over", "gols": 4.5, "threshold": 5},
}

def get_short_league_name(leagueName):
    shortNames = {
        "Esoccer Battle - 8 mins play": "Battle 8m",
        "Esoccer Battle Volta - 6 mins play": "Battle Volta 6m",
        "Esoccer GT Leagues – 12 mins play": "GT Leagues 12m",
        "Esoccer H2H GG League - 8 mins play": "H2H GG 8m",
        "Esoccer Adriatic League - 10 mins play": "Adriatic 10m"
    }
    return shortNames.get(leagueName, leagueName)

def meets_league_criteria(leagueName, player1Stats, player2Stats, leagueStats):
    avg_diff = abs(player1Stats["avgGoals"] - player2Stats["avgGoals"])
    if avg_diff > 2.5:
        return False

    form = max(player1Stats["winRate"], player2Stats["winRate"])
    if form < 40:
        return False

    form_over35ht = max(player1Stats["over35HT"], player2Stats["over35HT"])
    if form_over35ht < 60:
        return False

    if leagueName == "Esoccer Battle - 8 mins play":
        return (
            leagueStats["over15HT"] >= 90 and
            leagueStats["over25HT"] >= 90 and
            leagueStats["bttsHT"] >= 90 and
            leagueStats["over45FT"] >= 85 and
            leagueStats["bttsFT"] >= 90 and
            leagueStats["avgGoalsHT"] > 2.0
        )

    if leagueName == "Esoccer H2H GG League - 8 mins play":
        return (
            leagueStats["over15HT"] >= 90 and
            leagueStats["over25HT"] >= 90 and
            leagueStats["bttsHT"] >= 80 and
            leagueStats["over35FT"] >= 85 and
            leagueStats["bttsFT"] >= 85 and
            leagueStats["avgGoalsFT"] > 3.5
        )

    if leagueName == "Esoccer Battle Volta - 6 mins play":
        return (
            leagueStats["over25HT"] >= 90 and
            leagueStats["over35HT"] >= 85 and
            leagueStats["bttsHT"] >= 90 and
            leagueStats["over55FT"] >= 85 and
            leagueStats["bttsFT"] >= 90 and
            leagueStats["avgGoalsFT"] > 5.0
        )

    if leagueName == "Esoccer GT Leagues – 12 mins play":
        return (
            leagueStats["over35HT"] >= 90 and
            leagueStats["bttsHT"] >= 90 and
            leagueStats["over45FT"] >= 90 and
            leagueStats["bttsFT"] >= 90 and
            leagueStats["avgGoalsHT"] > 2.5
        )

    if leagueName == "Esoccer Adriatic League - 10 mins play":
        return (
            leagueStats["over35HT"] >= 90 and
            leagueStats["bttsHT"] >= 90 and
            leagueStats["over45FT"] >= 90 and
            leagueStats["over55FT"] >= 80 and
            leagueStats["bttsFT"] >= 90 and
            leagueStats["avgGoalsFT"] > 4.5
        )

    return False

def get_player_strategy(home, away, p1, p2):
    avg1, avg2 = p1["avgGoals"], p2["avgGoals"]
    if avg1 > avg2 and avg1 >= 3.0 and avg2 <= 1.2 and p1["winRate"] > p2["winRate"]:
        return f"+2.5 GOLS {home}"
    if avg2 > avg1 and avg2 >= 3.0 and avg1 <= 1.2 and p2["winRate"] > p1["winRate"]:
        return f"+2.5 GOLS {away}"
    return None

def send_telegram_message(game, league_stats, p1, p2, strategy):
    try:
        home = game.get("home", {}).get("name", "N/A")
        away = game.get("away", {}).get("name", "N/A")
        league = game.get("competition", {}).get("name", "N/A")
        shortLeague = get_short_league_name(league)
        startTime = game.get("startTime", "")
        match_key = f"{home}_vs_{away}_{league}_{startTime}"
        if not _mark_sent_and_persist(match_key):
            return False

        date_display = format_datetime_for_display(startTime)
        text = (
            f"🏆 <b>{shortLeague}</b> 🚨 Alerta de Jogo!\n\n"
            f"📅 <b>{date_display}</b>\n\n"
            f"🎯 <b>Estratégia Recomendada:</b> <code>{strategy}</code> 🔥\n\n"
            f"🎮 <b>{home}</b> ({p1['winRate']}% win) vs <b>{away}</b> ({p2['winRate']}% win)\n"
            f"⚽ Avg Gols: <code>{p1['avgGoals']} ⚔️ {p2['avgGoals']}</code>\n\n"
            f"📈 <b>Estatísticas da Liga</b> ({league_stats['gameCount']} jogos):\n"
            f"• HT: <b>{league_stats['avgGoalsHT']} gols</b> | FT: <b>{league_stats['avgGoalsFT']} gols</b>\n"
            f"• Over 2.5 HT: <b>{league_stats['over25HT']}%</b> | BTTS HT: <b>{league_stats['bttsHT']}%</b>\n\n"
            f"• Over 4.5 FT: <b>{league_stats['over45FT']}%</b> | BTTS FT: <b>{league_stats['bttsFT']}%</b>\n\n"
            f"🤖 <i>Monitorado: 👑 ʀᴡ ᴛɪᴘs - ғɪғᴀ 🎮</i>"
        )

        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        resp = requests.post(TELEGRAM_URL, json=payload, timeout=10)
        if resp.ok:
            print("✅ Mensagem enviada:", match_key)
            return True
        else:
            print("❌ Falha ao enviar:", resp.status_code, resp.text)
            with _sent_lock:
                if match_key in sent_messages:
                    sent_messages.remove(match_key)
                    save_sent_messages(sent_messages)
            return False
    except Exception as e:
        print("❌ Erro ao enviar mensagem:", e)
        return False

def fetch_games(status, limit=100, page=1):
    url = "https://api-v2.green365.com.br/api/v2/sport-events"
    for _ in range(3):
        try:
            resp = requests.get(url, params={"page": page, "limit": limit, "sport": "esoccer", "status": status}, timeout=15)
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as e:
            print(f"⚠️ Erro ao buscar {status}:", e)
            time.sleep(10)
    return []

def _mark_sent_and_persist(match_key):
    with _sent_lock:
        if match_key in sent_messages:
            return False
        sent_messages.add(match_key)
        save_sent_messages(sent_messages)
        return True

# --- BACKTEST E RELATÓRIO DIÁRIO ---
def load_relatorio():
    if not os.path.exists(RELATORIO_FILE):
        return {}
    try:
        with open(RELATORIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_relatorio(relatorio):
    try:
        with open(RELATORIO_FILE, "w", encoding="utf-8") as f:
            json.dump(relatorio, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("❌ Erro ao salvar relatório:", e)

def run_backtest(past_games):
    relatorio = load_relatorio()
    today_str = datetime.now(SP_TZ).strftime("%Y-%m-%d")
    if today_str not in relatorio:
        relatorio[today_str] = {"total": 0, "acertos": 0, "erros": 0, "jogos": []}

    for game in past_games:
        league = game.get("competition", {}).get("name")
        if league not in PREVISOES:
            continue

        home = game.get("home", {}).get("name")
        away = game.get("away", {}).get("name")
        score = parse_score(game.get("score"))
        total_goals = score["totalGoals"]

        prev = PREVISOES[league]
        acertou = total_goals >= prev["threshold"]

        relatorio[today_str]["total"] += 1
        if acertou:
            relatorio[today_str]["acertos"] += 1
        else:
            relatorio[today_str]["erros"] += 1

        relatorio[today_str]["jogos"].append({
            "home": home,
            "away": away,
            "liga": get_short_league_name(league),
            "previsto": f"Over {prev['gols']}",
            "resultado": f"{score['homeGoals']}-{score['awayGoals']}",
            "status": "ACERTO" if acertou else "ERRO"
        })

    save_relatorio(relatorio)
    return relatorio[today_str]

def send_daily_report():
    relatorio = load_relatorio()
    today_str = datetime.now(SP_TZ).strftime("%Y-%m-%d")
    data = relatorio.get(today_str)

    if not data or data["total"] == 0:
        return

    taxa = data["acertos"] / data["total"] * 100
    msg = (
        f"📈 <b>RELATÓRIO DIÁRIO - {today_str}</b>\n\n"
        f"✅ <b>{data['acertos']} acertos</b> | ❌ <b>{data['erros']} erros</b> | 🎯 <b>{taxa:.1f}%</b> de acerto\n\n"
    )

    por_liga = {}
    for jogo in data["jogos"]:
        liga = jogo["liga"]
        if liga not in por_liga:
            por_liga[liga] = {"acertos": 0, "total": 0}
        por_liga[liga]["total"] += 1
        if jogo["status"] == "ACERTO":
            por_liga[liga]["acertos"] += 1

    for liga, res in por_liga.items():
        msg += f"🏆 {liga}: {res['acertos']}/{res['total']}\n"

    msg += "\n👉 Amanhã continuamos com foco! 💪"

    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    try:
        requests.post(TELEGRAM_URL, json=payload, timeout=10)
        print("📅 Relatório diário enviado no Telegram!")
    except Exception as e:
        print("❌ Falha ao enviar relatório:", e)

# --- LOOP PRINCIPAL ---
def main_loop():
    print("🚀 Bot iniciado. Intervalo:", LOOP_INTERVAL, "segundos")
    last_report_sent = None

    with ThreadPoolExecutor(max_workers=MAX_SEND_WORKERS) as executor:
        while True:
            start_time = time.perf_counter()
            now = datetime.now(SP_TZ)

            try:
                # --- Backtest com jogos encerrados ---
                past = fetch_games("ended", 100)
                if past:
                    run_backtest(past)

                # --- Enviar relatório diário às 23:59 ---
                if now.hour == 23 and now.minute == 59:
                    if last_report_sent != now.strftime("%Y-%m-%d"):
                        send_daily_report()
                        last_report_sent = now.strftime("%Y-%m-%d")

                # --- Análise de próximos jogos ---
                upcoming = fetch_games("upcoming", 100)
                tasks = check_and_send_matches(past, upcoming)

                if tasks:
                    futures = [executor.submit(send_telegram_message, *t) for t in tasks]
                    for fut in as_completed(futures):
                        try:
                            fut.result(timeout=20)
                            time.sleep(0.2)
                        except Exception as e:
                            print("⚠️ Erro:", e)
                else:
                    print("— Nenhum sinal encontrado.")

            except Exception as e:
                print("❌ Erro geral:", e)

            elapsed = time.perf_counter() - start_time
            time.sleep(max(0, LOOP_INTERVAL - elapsed))

def check_and_send_matches(all_games_past, upcoming_games):
    tasks = []
    target_leagues = [
        "Esoccer GT Leagues – 12 mins play",
        "Esoccer Battle - 8 mins play",
        "Esoccer Battle Volta - 6 mins play",
        "Esoccer H2H GG League - 8 mins play",
        "Esoccer Adriatic League - 10 mins play"
    ]

    now = datetime.now(SP_TZ) if SP_TZ else datetime.now()

    for game in upcoming_games:
        league = game.get("competition", {}).get("name")
        if league not in target_leagues:
            continue

        home = game.get("home", {}).get("name")
        away = game.get("away", {}).get("name")
        if not home or not away:
            continue

        start_time_str = game.get("startTime", "")
        if not start_time_str:
            continue

        try:
            game_start = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            if SP_TZ:
                game_start = game_start.astimezone(SP_TZ)
        except:
            continue

        delta = game_start - now
        if delta > timedelta(minutes=2) or delta <= timedelta(seconds=0):
            print(f"{home} vs {away} | hora do jogo: {game_start.strftime('%H:%M')} | hora atual: {now.strftime('%H:%M')}")
            continue

        print(f"✅ {home} vs {away} | começa em {format_timedelta(delta)}")

        league_stats = get_league_stats(league, all_games_past)
        if not league_stats:
            print(f"⚠️ Poucos jogos na liga: {league}")
            continue

        p1 = get_player_stats(home, all_games_past)
        p2 = get_player_stats(away, all_games_past)

        if meets_league_criteria(league, p1, p2, league_stats):
            strategy_map = {
                "Esoccer Battle - 8 mins play": "Over_Battle",
                "Esoccer H2H GG League - 8 mins play": "Over_H2H",
                "Esoccer Battle Volta - 6 mins play": "Over_Volta",
                "Esoccer GT Leagues – 12 mins play": "HT_GT",
                "Esoccer Adriatic League - 10 mins play": "HT_ADRIATIC"
            }
            strategy = strategy_map.get(league)
        else:
            strategy = get_player_strategy(home, away, p1, p2)

        if strategy:
            tasks.append((game, league_stats, p1, p2, strategy))

    return tasks

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("✅ Encerrado. Estado salvo.")
        save_sent_messages(sent_messages)