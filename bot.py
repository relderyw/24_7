import os
import time
import requests
import json
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot  # Requires python-telegram-bot v20.0+: pip install python-telegram-bot

BOT_TOKEN = "6569266928:AAHm7pOJVsd3WKzJEgdVDez4ZYdCAlRoYO8"
CHAT_ID = "-1001981134607"
OLD_LIVE_API_URL = "https://caveira-proxy.onrender.com/api/matches/live"
NEW_LIVE_API_URL = "https://esoccer.dev3.caveira.tips/v1/esoccer/inplay"
ENDED_API_URL = "https://api-v2.green365.com.br/api/v2/sport-events"
H2H_API_URL = "https://caveira-proxy.onrender.com/api/v1/historico/confronto/{player1}/{player2}?page=1&limit=10"
AUTH_TOKEN = "Bearer oat_OTI1ODQ.ODVCVHlCYmxjWEtBUDBXdEptb0Jlc29MbV9DMUhJaW9BSExCSDhfeDI4NzYxMTk2MTk"

# Manaus timezone is UTC-4
MANAUS_TZ = timezone(timedelta(hours=-4))

sent_tips = []  # List to store sent tips: {'match_id': id, 'strategy': str, 'sent_time': datetime, 'status': 'pending/green/red'}

def fetch_old_live_matches():
    try:
        response = requests.get(OLD_LIVE_API_URL)
        response.raise_for_status()
        data = response.json().get('data', [])
        # Filtrar partidas inválidas
        valid_matches = [m for m in data if m is not None and isinstance(m, dict) and 'id' in m and 'league' in m and 'home' in m and 'away' in m]
        print(f"[INFO] {len(data)} partidas recebidas da API antiga, {len(valid_matches)} válidas")
        return valid_matches
    except Exception as e:
        print(f"[ERROR] Erro ao buscar API antiga de partidas ao vivo: {e}")
        return []

def fetch_bet365_ids():
    try:
        headers = {
            "Authorization": AUTH_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        response = requests.post(NEW_LIVE_API_URL, headers=headers, json={})
        response.raise_for_status()
        data = response.json().get('data', [])
        print(f"[INFO] {len(data)} partidas recebidas da API nova para links")
        return {(m['player_home_name'].lower(), m['player_away_name'].lower()): m.get('bet365_ev_id') for m in data}
    except Exception as e:
        print(f"[ERROR] Erro ao buscar API nova para bet365_ev_id: {e}")
        return {}

def fetch_ended_matches():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 OPR/120.0.0.0",
        "Accept": "application/json",
        "Referer": "https://green365.com.br/",
        "Origin": "https://green365.com.br",
        "Authorization": AUTH_TOKEN
    }
    items = []
    page = 1
    today = datetime.now(timezone.utc).date()
    params = {
        "page": page,
        "limit": 150,
        "sport": "esoccer",
        "status": "ended"
    }
    url = ENDED_API_URL
    print(f"[DEBUG] Requisição para {url} com params: {params}")
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"[ERROR] Erro ao buscar API de partidas finalizadas: {response.status_code} {response.text}")
            return items
        data = response.json()
        for item in data.get('items', []):
            start_time_str = item.get('startTime', '').rstrip('Z')
            start_time = datetime.fromisoformat(start_time_str).replace(tzinfo=timezone.utc) if start_time_str else datetime.now(timezone.utc)
            if start_time.date() >= today:
                items.append(item)
        print(f"[DEBUG] Partidas finalizadas retornadas: {len(items)}")
    except Exception as e:
        print(f"[ERROR] Erro ao buscar API de partidas finalizadas: {e}")
    return items

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
    timer = match.get('timer', {})
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
    elif "12 mins play" in league_name or "10 mins play" in league_name:
        is_first = time_in_minutes < 6
        print(f"[DEBUG] Partida {match.get('id', 'unknown')} ({league_name}): Tempo {time_in_minutes:.2f} minutos, Primeiro tempo: {is_first}")
        return is_first
    return False

def calculate_dangerous_attacks_rate(match, current_time):
    if current_time == 0:
        print(f"[DEBUG] Partida {match.get('id', 'unknown')}: Tempo atual é 0, taxa de ataques perigosos = 0")
        return 0
    stats = match.get('stats')
    if not stats or not isinstance(stats, dict):
        print(f"[DEBUG] Partida {match.get('id', 'unknown')}: Stats não disponível ou inválido, usando [0, 0] como padrão")
        return 0
    dangerous_attacks = stats.get('dangerous_attacks', [0, 0])
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
        'over_1.5_ht_percentage': (over_1_5_ht / total_matches) * 100 if total_matches > 0 else 0,
        'over_2_5_ht_percentage': (over_2_5_ht / total_matches) * 100 if total_matches > 0 else 0,
        'btts_ht_percentage': (btts_ht / total_matches) * 100 if total_matches > 0 else 0
    }
    print(f"[DEBUG] Métricas H2H: Over 0.5 HT = {metrics['over_0_5_ht_percentage']:.2f}%, Over 1.5 HT = {metrics['over_1.5_ht_percentage']:.2f}%, Over 2.5 HT = {metrics['over_2_5_ht_percentage']:.2f}%, BTTS HT = {metrics['btts_ht_percentage']:.2f}%")
    return metrics

def format_message(match, h2h_metrics, strategy, bet365_ev_id):
    league = match['league']['name']
    home = match['home']['name']
    away = match['away']['name']
    player1 = home.split('(')[-1].rstrip(')') if '(' in home else home
    player2 = away.split('(')[-1].rstrip(')') if '(' in away else away
    timer = match.get('timer', {})
    if timer:
        minutes = timer.get('tm', 0)
        seconds = timer.get('ts', 0)
        game_time = f"{minutes}:{seconds:02d}"
    else:
        game_time = "0:00"
    
    message = f"\n\n🏆 {league}\n\n🎯 {strategy}\n\n⏰ Tempo: {game_time}\n\n"   
    message += f"🎮 {player1} vs {player2}\n"
    message += f"🏅  {h2h_metrics['player1_win_percentage']:.2f}% vs {h2h_metrics['player2_win_percentage']:.2f}%\n\n"
    if h2h_metrics:
        message += (
            f"📊 H2H (últimos 10 jogos):\n\n"
            f"⚽ +0.5 Gols HT: {h2h_metrics['over_0_5_ht_percentage']:.2f}%\n"
            f"⚽ +1.5 Gols HT: {h2h_metrics['over_1.5_ht_percentage']:.2f}%\n"
            f"⚽ +2.5 Gols HT: {h2h_metrics['over_2_5_ht_percentage']:.2f}%\n"
            f"⚽ BTTS HT: {h2h_metrics['btts_ht_percentage']:.2f}%"
        )
    else:
        message += "📊 Estatísticas H2H: Não disponíveis"
    
    if bet365_ev_id:
        message += f'\n\n<a href="https://www.bet365.bet.br/#/IP/EV{bet365_ev_id}">🔗Bet365</a>\n\n'

    return message

async def send_message(bot, match_id, message, sent_matches, strategy):
    if match_id not in sent_matches:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML", disable_web_page_preview=True)
            sent_matches.add(match_id)
            print(f"[INFO] Mensagem enviada para a partida {match_id}")
            sent_tips.append({
                'match_id': int(match_id),  # Converter para inteiro para consistência
                'strategy': strategy,
                'sent_time': datetime.now(MANAUS_TZ),
                'status': 'pending'
            })
        except Exception as e:
            print(f"[ERROR] Erro ao enviar mensagem para a partida {match_id}: {e}")

async def periodic_check(bot):
    while True:
        await asyncio.sleep(600)  # 5 minutes
        print("[INFO] Verificando status das tips...")
        ended = fetch_ended_matches()
        ended_dict = {int(m['eventID']): m for m in ended}  # Converter eventID para inteiro
        today = datetime.now(MANAUS_TZ).date()
        greens = 0
        reds = 0
        for tip in sent_tips:
            if tip['sent_time'].date() != today:
                continue
            if tip['status'] == 'pending':
                m = ended_dict.get(tip['match_id'])
                if m:
                    print(f"[DEBUG] Verificando status para partida {tip['match_id']}: {m}")
                    ht_goals = m.get('scoreHT', {}).get('home', 0) + m.get('scoreHT', {}).get('away', 0)
                    print(f"[DEBUG] Gols do primeiro tempo para {tip['match_id']}: {ht_goals}")
                    if tip['strategy'] == '+0.5 HT':
                        tip['status'] = 'green' if ht_goals > 0 else 'red'
                    elif tip['strategy'] == '+1.5 HT':
                        tip['status'] = 'green' if ht_goals > 1 else 'red'
                    print(f"[DEBUG] Status atualizado para {tip['match_id']}: {tip['status']}")
            if tip['status'] == 'green':
                greens += 1
            elif tip['status'] == 'red':
                reds += 1
        total = greens + reds
        if total > 0:
            perc = (greens / total) * 100
            indicator = f"\n\n👑 ʀᴡ ᴛɪᴘs - ғɪғᴀ 🎮\n\n✅ Green [{greens}] x [{reds}] Red ❌\n\n📊 {perc:.2f}%"
            print(f"[INFO] Indicador de desempenho: {indicator}")
            try:
                await bot.send_message(chat_id=CHAT_ID, text=indicator)
            except Exception as e:
                print(f"[ERROR] Erro ao enviar indicador: {e}")
        else:
            print("[INFO] Nenhum resultado disponível para enviar indicador")

async def main():
    bot = Bot(token=BOT_TOKEN)
    sent_matches = set()  # To avoid sending duplicates
    asyncio.create_task(periodic_check(bot))

    while True:
        print(f"[INFO] Iniciando novo ciclo de verificação às {datetime.now(MANAUS_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        bet365_dict = fetch_bet365_ids()
        matches = fetch_old_live_matches()
        for match in matches:
            if match is None or not isinstance(match, dict):
                print(f"[ERROR] Partida inválida detectada: {match}")
                continue
            match_id = match.get('id', 'unknown')
            league_name = match.get('league', {}).get('name', 'Desconhecida')
            home = match.get('home', {}).get('name', 'Desconhecido')
            away = match.get('away', {}).get('name', 'Desconhecido')
            print(f"[DEBUG] Analisando partida {match_id}: {home} vs {away} ({league_name})")

            ss = match.get('ss')
            if ss:
                try:
                    home_goals, away_goals = map(int, ss.split('-'))
                except ValueError:
                    home_goals, away_goals = 0, 0
            else:
                home_goals, away_goals = 0, 0
            total_goals = home_goals + away_goals
            current_time = get_match_time_in_minutes(match)
            
            # Verificar se está no primeiro tempo
            if not is_first_half(match, league_name):
                print(f"[DEBUG] Partida {match_id}: Ignorada, não está no primeiro tempo")
                continue

            # Extrair nomes dos jogadores
            player1 = home.split('(')[-1].rstrip(')') if '(' in home else home
            player2 = away.split('(')[-1].rstrip(')') if '(' in away else away
            print(f"[DEBUG] Partida {match_id}: Jogadores extraídos: {player1} vs {player2}")

            # Obter bet365_ev_id da nova API
            bet365_ev_id = bet365_dict.get((player1.lower(), player2.lower()))

            # Buscar dados H2H
            h2h_data = fetch_h2h_data(player1, player2)
            h2h_metrics = calculate_h2h_metrics(h2h_data, league_name)

            # Estratégia +1.5 HT: Placar 1-0 ou 0-1
            if (home_goals == 1 and away_goals == 0) or (home_goals == 0 and away_goals == 1):
                if "8 mins play" in league_name and current_time < 3 and h2h_metrics and h2h_metrics['over_1.5_ht_percentage'] >= 100.0:
                    print(f"[DEBUG] Partida {match_id}: Condições atendidas para +1.5 HT (8 mins, tempo {current_time:.2f} min, Over 1.5 HT {h2h_metrics['over_1.5_ht_percentage']:.2f}%)")
                    message = format_message(match, h2h_metrics, "+1.5 HT", bet365_ev_id)
                    await send_message(bot, match_id, message, sent_matches, "+1.5 HT")
                elif ("12 mins play" in league_name or "10 mins play" in league_name) and current_time < 5 and h2h_metrics and h2h_metrics['over_1.5_ht_percentage'] >= 100.0:
                    print(f"[DEBUG] Partida {match_id}: Condições atendidas para +1.5 HT (12/10 mins, tempo {current_time:.2f} min, Over 1.5 HT {h2h_metrics['over_1.5_ht_percentage']:.2f}%)")
                    message = format_message(match, h2h_metrics, "+1.5 HT", bet365_ev_id)
                    await send_message(bot, match_id, message, sent_matches, "+1.5 HT")
                else:
                    print(f"[DEBUG] Partida {match_id}: Ignorada para +1.5 HT (Tempo: {current_time:.2f}, Over 1.5 HT: {h2h_metrics['over_1.5_ht_percentage'] if h2h_metrics else 'N/A'}%)")
                continue

            # Estratégia +0.5 HT: Placar 0-0
            if total_goals != 0:
                print(f"[DEBUG] Partida {match_id}: Ignorada para +0.5 HT, placar não é 0-0 (Total de gols: {total_goals})")
                continue

            if current_time == 0:
                print(f"[DEBUG] Partida {match_id}: Ignorada, tempo atual é 0")
                continue

            if "Esoccer Battle - 8 mins play" in league_name:
                if h2h_metrics and h2h_metrics['over_2_5_ht_percentage'] >= 100.0 and h2h_metrics['btts_ht_percentage'] >= 100.0:
                    print(f"[DEBUG] Partida {match_id}: Todas as condições atendidas para +0.5 HT (Esoccer Battle), enviando mensagem")
                    message = format_message(match, h2h_metrics, "+0.5 HT", bet365_ev_id)
                    await send_message(bot, match_id, message, sent_matches, "+0.5 HT")
                else:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, métricas H2H para Esoccer Battle não atendidas (Over 2.5 HT: {h2h_metrics['over_2_5_ht_percentage'] if h2h_metrics else 'N/A'}%, BTTS HT: {h2h_metrics['btts_ht_percentage'] if h2h_metrics else 'N/A'}%)")
            else:
                da_rate = calculate_dangerous_attacks_rate(match, current_time)
                if da_rate < 1.0:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, taxa de ataques perigosos insuficiente ({da_rate:.2f} < 1.0)")
                    continue

                if h2h_metrics and h2h_metrics['over_0_5_ht_percentage'] >= 100.0 and h2h_metrics['over_1.5_ht_percentage'] >= 85.0:
                    print(f"[DEBUG] Partida {match_id}: Todas as condições atendidas para +0.5 HT, enviando mensagem")
                    message = format_message(match, h2h_metrics, "+0.5 HT", bet365_ev_id)
                    await send_message(bot, match_id, message, sent_matches, "+0.5 HT")
                else:
                    print(f"[DEBUG] Partida {match_id}: Ignorada, métricas H2H não atendidas (Over 0.5 HT: {h2h_metrics['over_0_5_ht_percentage'] if h2h_metrics else 'N/A'}%, Over 1.5 HT: {h2h_metrics['over_1.5_ht_percentage'] if h2h_metrics else 'N/A'}%)")

        await asyncio.sleep(30)  # Update every 30 seconds

if __name__ == "__main__":
    asyncio.run(main())