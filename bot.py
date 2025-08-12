import os
import time
import requests
import json
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot  # Requires python-telegram-bot v20.0+: pip install python-telegram-bot

BOT_TOKEN = "6569266928:AAHm7pOJVsd3WKzJEgdVDez4ZYdCAlRoYO8"
CHAT_ID = "-1001981134607"
LIVE_API_URL = "https://caveira-proxy.onrender.com/api/matches/live"
H2H_API_URL = "https://caveira-proxy.onrender.com/api/v1/historico/confronto/{player1}/{player2}?page=1&limit=10"

# Manaus timezone is UTC-4
MANAUS_TZ = timezone(timedelta(hours=-4))

def fetch_live_matches():
    try:
        response = requests.get(LIVE_API_URL)
        response.raise_for_status()
        data = response.json().get('data', [])
        print(f"[INFO] {len(data)} partidas ao vivo recebidas da API")
        return data
    except Exception as e:
        print(f"[ERROR] Erro ao buscar API de partidas ao vivo: {e}")
        return []

def fetch_h2h_data(player1, player2):
    try:
        url = H2H_API_URL.format(player1=player1, player2=player2)
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar H2H para {player1} vs {player2}: {e}")
        return None

def get_match_time_in_minutes(match):
    timer = match.get('timer')
    if not timer:
        print(f"[DEBUG] Partida {match.get('id', 'unknown')}: Sem timer disponível")
        return 0
    tm = timer.get('tm', 0)
    ts = timer.get('ts', 0)
    return tm + (ts / 60)

def is_first_half(match, league_name):
    time_in_minutes = get_match_time_in_minutes(match)
    if "8 mins play" in league_name:
        is_first = time_in_minutes < 4
        print(f"[DEBUG] Partida {match.get('id', 'unknown')} ({league_name}): Tempo {time_in_minutes:.2f} minutos, Primeiro tempo: {is_first}")
        return is_first
    elif "12 mins play" in league_name:
        is_first = time_in_minutes < 6
        print(f"[DEBUG] Partida {match.get('id', 'unknown')} ({league_name}): Tempo {time_in_minutes:.2f} minutos, Primeiro tempo: {is_first}")
        return is_first
    return False

def calculate_dangerous_attacks_rate(match, current_time):
    if current_time == 0:
        print(f"[DEBUG] Partida {match.get('id', 'unknown')}: Tempo atual é 0, taxa de ataques perigosos = 0")
        return 0
    dangerous_attacks = match.get('stats', {}).get('dangerous_attacks', [0, 0])
    total_da = int(dangerous_attacks[0]) + int(dangerous_attacks[1])
    da_rate = total_da / current_time
    print(f"[DEBUG] Partida {match.get('id', 'unknown')}: Ataques perigosos = {total_da}, Tempo = {current_time:.2f} minutos, Taxa = {da_rate:.2f}")
    return da_rate

def calculate_h2h_metrics(h2h_data, league_name):
    if not h2h_data or 'matches' not in h2h_data:
        print("[DEBUG] Dados H2H não disponíveis ou inválidos")
        return None

    matches = h2h_data['matches']
    total_matches = len(matches)
    if total_matches == 0:
        print("[DEBUG] Nenhum jogo H2H encontrado")
        return None

    over_0_5_ht = 0
    over_1_5_ht = 0
    over_2_5_ht = 0
    btts_ht = 0

    for match in matches:
        ht_home = match['halftime_score_home']
        ht_away = match['halftime_score_away']
        ht_goals = ht_home + ht_away
        if ht_goals > 0:
            over_0_5_ht += 1
        if ht_goals > 1:
            over_1_5_ht += 1
        if ht_goals > 2:
            over_2_5_ht += 1
        if ht_home > 0 and ht_away > 0:
            btts_ht += 1

    metrics = {
        'player1_win_percentage': h2h_data.get('player1_win_percentage', 0),
        'player2_win_percentage': h2h_data.get('player2_win_percentage', 0),
        'over_0_5_ht_percentage': (over_0_5_ht / total_matches) * 100 if total_matches > 0 else 0,
        'over_1_5_ht_percentage': (over_1_5_ht / total_matches) * 100 if total_matches > 0 else 0,
        'over_2_5_ht_percentage': (over_2_5_ht / total_matches) * 100 if total_matches > 0 else 0,
        'btts_ht_percentage': (btts_ht / total_matches) * 100 if total_matches > 0 else 0
    }
    print(f"[DEBUG] Métricas H2H: Over 0.5 HT = {metrics['over_0_5_ht_percentage']:.2f}%, Over 1.5 HT = {metrics['over_1_5_ht_percentage']:.2f}%, Over 2.5 HT = {metrics['over_2_5_ht_percentage']:.2f}%, BTTS HT = {metrics['btts_ht_percentage']:.2f}%")
    return metrics

def format_message(match, h2h_metrics):
    league = match['league']['name']
    home = match['home']['name']
    away = match['away']['name']
    now = datetime.now(MANAUS_TZ).strftime("%Y-%m-%d %H:%M:%S")

    player1 = home.split('(')[-1].rstrip(')')  # Extract player name from e.g., "Napoli (CASTLE)"
    player2 = away.split('(')[-1].rstrip(')')  # Extract player name from e.g., "Liverpool (SMHAMILA)"

    message = f"🏆 {league}\n\n🎯 +0.5 HT\n\n📅 Data/Hora: {now}\n\n⚽️ {home} vs {away}\n\n"
    
    if h2h_metrics:
        message += (
            f"📊 Estatísticas H2H (últimos 10 jogos):\n"
            f"🏅 {player1} Vitórias: {h2h_metrics['player1_win_percentage']:.2f}%\n"
            f"🏅 {player2} Vitórias: {h2h_metrics['player2_win_percentage']:.2f}%\n"
            f"⚽ +0.5 Gols HT: {h2h_metrics['over_0_5_ht_percentage']:.2f}%\n"
            f"⚽ +1.5 Gols HT: {h2h_metrics['over_1_5_ht_percentage']:.2f}%\n"
            f"⚽ +2.5 Gols HT: {h2h_metrics['over_2_5_ht_percentage']:.2f}%\n"
            f"⚽ BTTS HT: {h2h_metrics['btts_ht_percentage']:.2f}%"
        )
    else:
        message += "📊 Estatísticas H2H: Não disponíveis"

    return message

async def send_message(bot, match_id, message, sent_matches):
    if match_id not in sent_matches:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
            sent_matches.add(match_id)
            print(f"[INFO] Mensagem enviada para a partida {match_id}")
        except Exception as e:
            print(f"[ERROR] Erro ao enviar mensagem para a partida {match_id}: {e}")

async def main():
    bot = Bot(token=BOT_TOKEN)
    sent_matches = set()  # To avoid sending duplicates

    while True:
        print(f"[INFO] Iniciando novo ciclo de verificação às {datetime.now(MANAUS_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        matches = fetch_live_matches()
        for match in matches:
            match_id = match.get('id', 'unknown')
            league_name = match['league']['name']
            home = match['home']['name']
            away = match['away']['name']
            print(f"[DEBUG] Analisando partida {match_id}: {home} vs {away} ({league_name})")

            ss = match.get('ss')
            if ss != "0-0":
                print(f"[DEBUG] Partida {match_id}: Ignorada, placar não é 0-0 ({ss})")
                continue

            if not is_first_half(match, league_name):
                continue

            current_time = get_match_time_in_minutes(match)
            if current_time == 0:
                print(f"[DEBUG] Partida {match_id}: Ignorada, tempo atual é 0")
                continue

            # Extract player names from team names
            player1 = home.split('(')[-1].rstrip(')')  # e.g., CASTLE from Napoli (CASTLE)
            player2 = away.split('(')[-1].rstrip(')')  # e.g., SMHAMILA from Liverpool (SMHAMILA)
            print(f"[DEBUG] Partida {match_id}: Jogadores extraídos: {player1} vs {player2}")

            # Fetch H2H data
            h2h_data = fetch_h2h_data(player1, player2)
            h2h_metrics = calculate_h2h_metrics(h2h_data, league_name)

            if "Esoccer Battle - 8 mins play" in league_name:
                if h2h_metrics and h2h_metrics['over_2_5_ht_percentage'] == 100.0 and h2h_metrics['btts_ht_percentage'] == 100.0:
                    print(f"[DEBUG] Partida {match_id}: Todas as condições atendidas para Esoccer Battle, enviando mensagem")
                    message = format_message(match, h2h_metrics)
                    await send_message(bot, match_id, message, sent_matches)
                else:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, métricas H2H para Esoccer Battle não atendidas (Over 2.5 HT: {h2h_metrics['over_2_5_ht_percentage'] if h2h_metrics else 'N/A'}%, BTTS HT: {h2h_metrics['btts_ht_percentage'] if h2h_metrics else 'N/A'}%)")
            else:
                da_rate = calculate_dangerous_attacks_rate(match, current_time)
                if da_rate < 1.0:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, taxa de ataques perigosos insuficiente ({da_rate:.2f} < 1.0)")
                    continue

                # Check H2H metrics conditions for other leagues
                if h2h_metrics and h2h_metrics['over_0_5_ht_percentage'] == 100.0 and h2h_metrics['over_1_5_ht_percentage'] >= 85.0:
                    print(f"[DEBUG] Partida {match_id}: Todas as condições atendidas, enviando mensagem")
                    message = format_message(match, h2h_metrics)
                    await send_message(bot, match_id, message, sent_matches)
                else:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, métricas H2H não atendidas (Over 0.5 HT: {h2h_metrics['over_0_5_ht_percentage'] if h2h_metrics else 'N/A'}%, Over 1.5 HT: {h2h_metrics['over_1_5_ht_percentage'] if h2h_metrics else 'N/A'}%)")

        await asyncio.sleep(10)  # Update every 10 seconds

if __name__ == "__main__":
    asyncio.run(main())