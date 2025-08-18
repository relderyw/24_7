# Código Completo Corrigido para Estratégias HT e FT

import os
import time
import requests
import json
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot  # Requires python-telegram-bot v20+: pip install python-telegram-bot

BOT_TOKEN = "6569266928:AAHm7pOJVsd3WKzJEgdVDez4ZYdCAlRoYO8"
CHAT_ID = "-1001981134607"
OLD_LIVE_API_URL = "https://caveira-proxy.onrender.com/api/matches/live"
NEW_LIVE_API_URL = "https://esoccer.dev3.caveira.tips/v1/esoccer/inplay"
ENDED_API_URL = "https://api-v2.green365.com.br/api/v2/sport-events"
H2H_API_URL = "https://caveira-proxy.onrender.com/api/v1/historico/confronto/{player1}/{player2}?page=1&limit=10"
AUTH_TOKEN = "Bearer oat_OTI1ODQ.ODVCVHlCYmxjWEtBUDBXdEptb0Jlc29MbV9DMUhJaW9BSExCSDhfeDI4NzYxMTk2MTk"

# Manaus timezone is UTC-4
MANAUS_TZ = timezone(timedelta(hours=-4))

sent_tips = []  # [{'match_id': int, 'strategy': str, 'sent_time': datetime, 'status': 'pending/green/red', 'message_id': int, 'message_text': str}]
last_summary = None  # para não spammar o indicador


def fetch_old_live_matches():
    try:
        response = requests.get(OLD_LIVE_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json().get('data', [])
        valid_matches = [m for m in data if
                         isinstance(m, dict) and m.get('id') and m.get('league') and m.get('home') and m.get('away')]
        print(f"[INFO] {len(data)} partidas da API antiga; {len(valid_matches)} válidas")
        return valid_matches
    except Exception as e:
        print(f"[ERROR] fetch_old_live_matches: {e}")
        return []


def fetch_bet365_ids():
    try:
        headers = {
            "Authorization": AUTH_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        response = requests.post(NEW_LIVE_API_URL, headers=headers, json={}, timeout=10)
        response.raise_for_status()
        data = response.json().get('data', [])
        print(f"[INFO] {len(data)} partidas da API nova (links)")
        mapping = {}
        for m in data:
            try:
                k = (m['player_home_name'].lower(), m['player_away_name'].lower())
                mapping[k] = m.get('bet365_ev_id')
            except KeyError:
                continue
        return mapping
    except Exception as e:
        print(f"[ERROR] fetch_bet365_ids: {e}")
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
    today = datetime.now(timezone.utc).date()
    params = {"page": 1, "limit": 150, "sport": "esoccer", "status": "ended"}
    url = ENDED_API_URL
    print(f"[DEBUG] GET {url} params={params}")
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code != 200:
            print(f"[ERROR] ended_matches {response.status_code} {response.text}")
            return items
        data = response.json()
        for item in data.get('items', []):
            start_time_str = (item.get('startTime') or '').rstrip('Z')
            start_time = datetime.fromisoformat(start_time_str).replace(tzinfo=timezone.utc) if start_time_str else datetime.now(
                timezone.utc)
            if start_time.date() >= today:
                items.append(item)
        print(f"[DEBUG] Finalizadas retornadas (hoje+): {len(items)}")
    except Exception as e:
        print(f"[ERROR] fetch_ended_matches: {e}")
    return items


def fetch_h2h_data(player1, player2):
    try:
        url = H2H_API_URL.format(player1=player1, player2=player2)
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Debug temporário - imprimir estrutura da resposta
        #print(f"[DEBUG] H2H Response para {player1} vs {player2}: {json.dumps(data, indent=2)[:1000]}...")
        
        return data
    except Exception as e:
        print(f"[ERROR] fetch_h2h_data {player1} vs {player2}: {e}")
        return None


def get_match_time_in_minutes(match):
    timer = match.get('timer') or {}
    if not timer:
        print(f"[DEBUG] Partida {match.get('id','?')}: sem timer")
        return 0.0
    tm = timer.get('tm', 0)
    ts = timer.get('ts', 0)
    try:
        return float(tm) + float(ts) / 60.0
    except Exception:
        return float(tm) if isinstance(tm, (int, float)) else 0.0


def is_first_half(match, league_name):
    minutes = get_match_time_in_minutes(match)
    if "8 mins play" in league_name:
        is_first = minutes < 4
    elif "12 mins play" in league_name or "10 mins play" in league_name:
        is_first = minutes < 6
    else:
        is_first = False
    print(f"[DEBUG] {match.get('id','?')} ({league_name}): {minutes:.2f} min | 1ºT: {is_first}")
    return is_first


def calculate_dangerous_attacks_rate(match, current_time):
    if current_time <= 0:
        print(f"[DEBUG] {match.get('id','?')}: tempo=0 ⇒ da_rate=0")
        return 0.0
    stats = match.get('stats')
    if not isinstance(stats, dict):
        print(f"[DEBUG] {match.get('id','?')}: stats ausente/ inválido ⇒ da_rate=0")
        return 0.0
    da = stats.get('dangerous_attacks', [0, 0])
    try:
        total_da = int(da[0]) + int(da[1])
    except Exception:
        total_da = 0
    da_rate = total_da / current_time
    print(f"[DEBUG] {match.get('id','?')}: DA={total_da} | t={current_time:.2f} ⇒ da_rate={da_rate:.2f}")
    return da_rate


def calculate_h2h_metrics(h2h_data, league_name):
    if not h2h_data or 'matches' not in h2h_data:
        print("[DEBUG] H2H ausente/ inválido")
        return None
    
    matches = h2h_data['matches']
    total = len(matches)
    if total == 0:
        print("[DEBUG] H2H vazio")
        return None

    # Contadores para as estatísticas
    over_0_5_ht = 0
    over_1_5_ht = 0
    over_2_5_ht = 0
    btts_ht = 0
    player1_wins = 0
    player2_wins = 0
    player1_total_goals = 0
    player2_total_goals = 0

    print(f"[DEBUG] Analisando {total} jogos H2H...")
    
    for i, m in enumerate(matches):
        # Debug do match atual
        #print(f"[DEBUG] Match {i+1}: {json.dumps(m, indent=2)[:300]}...")
        
        # Resultados do primeiro tempo - mantém como está (funcionando)
        ht_home = m.get('halftime_score_home', 0)
        ht_away = m.get('halftime_score_away', 0)
        ht_goals = (ht_home or 0) + (ht_away or 0)
        if ht_goals > 0: over_0_5_ht += 1
        if ht_goals > 1: over_1_5_ht += 1
        if ht_goals > 2: over_2_5_ht += 1
        if (ht_home or 0) > 0 and (ht_away or 0) > 0: btts_ht += 1

        # CORREÇÃO: Múltiplas tentativas para acessar gols finais
        # Tentar diferentes estruturas possíveis dos dados
        ft_home = None
        ft_away = None
        
        # Tentativa 1: 'final_score_home' e 'final_score_away'
        if ft_home is None:
            ft_home = m.get('final_score_home')
            ft_away = m.get('final_score_away')
            
        # Tentativa 2: 'score_home' e 'score_away'
        if ft_home is None:
            ft_home = m.get('score_home')
            ft_away = m.get('score_away')
            
        # Tentativa 3: 'home_score' e 'away_score'
        if ft_home is None:
            ft_home = m.get('home_score')
            ft_away = m.get('away_score')
            
        # Tentativa 4: nested em 'score' ou 'final_score'
        if ft_home is None:
            score_obj = m.get('score') or m.get('final_score')
            if isinstance(score_obj, dict):
                ft_home = score_obj.get('home')
                ft_away = score_obj.get('away')
                
        # Tentativa 5: 'result' object
        if ft_home is None:
            result = m.get('result')
            if isinstance(result, dict):
                ft_home = result.get('home') or result.get('home_score')
                ft_away = result.get('away') or result.get('away_score')
        
        # Tentativa 6: 'ft_score_home' e 'ft_score_away'  
        if ft_home is None:
            ft_home = m.get('ft_score_home')
            ft_away = m.get('ft_score_away')
            
        # Tentativa 7: 'home_goals' e 'away_goals'
        if ft_home is None:
            ft_home = m.get('home_goals')
            ft_away = m.get('away_goals')
            
        # Se ainda não encontrou, tentar parsing de string 'score'
        if ft_home is None:
            score_str = m.get('score')
            if isinstance(score_str, str) and '-' in score_str:
                try:
                    parts = score_str.split('-')
                    if len(parts) == 2:
                        ft_home = int(parts[0].strip())
                        ft_away = int(parts[1].strip())
                except (ValueError, IndexError):
                    pass
        
        # Garantir valores padrão
        if ft_home is None:
            ft_home = 0
        if ft_away is None:
            ft_away = 0
            
        # Converter para int se necessário
        try:
            ft_home = int(ft_home) if ft_home is not None else 0
            ft_away = int(ft_away) if ft_away is not None else 0
        except (ValueError, TypeError):
            ft_home = 0
            ft_away = 0

        #print(f"[DEBUG] Match {i+1} gols finais: {ft_home}-{ft_away}")

        # Contagem de vitórias
        if ft_home > ft_away:
            player1_wins += 1
        elif ft_away > ft_home:
            player2_wins += 1

        # Contagem de gols
        player1_total_goals += ft_home
        player2_total_goals += ft_away

    # Calculando as porcentagens
    player1_win_percentage = (player1_wins / total) * 100.0 if total > 0 else 0.0
    player2_win_percentage = (player2_wins / total) * 100.0 if total > 0 else 0.0
    player1_avg_goals = player1_total_goals / total if total > 0 else 0.0
    player2_avg_goals = player2_total_goals / total if total > 0 else 0.0

    metrics = {
        'player1_win_percentage': player1_win_percentage,
        'player2_win_percentage': player2_win_percentage,
        'player1_avg_goals': player1_avg_goals,
        'player2_avg_goals': player2_avg_goals,
        'over_0_5_ht_percentage': (over_0_5_ht / total) * 100.0 if total > 0 else 0.0,
        'over_1_5_ht_percentage': (over_1_5_ht / total) * 100.0 if total > 0 else 0.0,
        'over_2_5_ht_percentage': (over_2_5_ht / total) * 100.0 if total > 0 else 0.0,
        'btts_ht_percentage': (btts_ht / total) * 100.0 if total > 0 else 0.0
    }
    
    print(
        f"[DEBUG] H2H FINAL {league_name} | Win1={player1_win_percentage:.1f}% Win2={player2_win_percentage:.1f}% "
        f"AvgG1={player1_avg_goals:.2f} AvgG2={player2_avg_goals:.2f} | "
        f"O0.5={metrics['over_0_5_ht_percentage']:.1f} O1.5={metrics['over_1_5_ht_percentage']:.1f} "
        f"O2.5={metrics['over_2_5_ht_percentage']:.1f} BTTS={metrics['btts_ht_percentage']:.1f}"
    )
    print(f"[DEBUG] Totais: P1_wins={player1_wins}, P2_wins={player2_wins}, P1_goals={player1_total_goals}, P2_goals={player2_total_goals}")
    
    return metrics


def format_message(match, h2h_metrics, strategy, bet365_ev_id):
    league = match['league']['name']
    home = match['home']['name']
    away = match['away']['name']
    player1 = home.split('(')[-1].rstrip(')') if '(' in home else home
    player2 = away.split('(')[-1].rstrip(')') if '(' in away else away
    timer = match.get('timer') or {}
    minutes = timer.get('tm', 0)
    seconds = timer.get('ts', 0)
    game_time = f"{minutes}:{int(seconds):02d}"
    ss = match.get('ss', '0-0')
    # Cabeçalho
    msg = f"\n\n<b>🏆 {league}</b>\n\n<b>🎯 {strategy}</b>\n\n⏳ Tempo: {game_time}\n\n"
    msg += f"🎮 {player1} vs {player2}\n"
    msg += f"⚽ Placar: {ss}\n"
    # H2H
    if h2h_metrics:
        msg += (
                f"🏅 <i>{h2h_metrics.get('player1_win_percentage', 0):.2f}% vs "
                f"{h2h_metrics.get('player2_win_percentage', 0):.2f}%</i>\n\n"
                f"<b>📊 H2H (últimos 10 jogos):</b>\n\n"
                f"💠 Média gols: <i>{h2h_metrics.get('player1_avg_goals', 0):.2f}</i> vs <i>{h2h_metrics.get('player2_avg_goals', 0):.2f}</i>\n\n"
                f"⚽ +0.5 HT: <i>{h2h_metrics.get('over_0_5_ht_percentage', 0):.0f}%</i> | +1.5 HT: <i>{h2h_metrics.get('over_1_5_ht_percentage', 0):.0f}%</i> | +2.5 Gols HT: <i>{h2h_metrics.get('over_2_5_ht_percentage', 0):.2f}%</i>\n\n"
                f"⚽ BTTS HT: <i>{h2h_metrics.get('btts_ht_percentage', 0):.2f}%</i>\n"
        )
    else:
        msg += "📊 H2H: <i>não disponível</i>"
    if bet365_ev_id:
        msg += f"\n\n🌐 <a href='https://www.bet365.bet.br/#/IP/EV{bet365_ev_id}'>🔗Bet365</a>\n\n"
    return msg


async def send_message(bot, match_id, message, sent_matches, strategy):
    if match_id in sent_matches:
        return
    try:
        message_obj = await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML",
                                            disable_web_page_preview=True)
        sent_matches.add(match_id)
        print(f"[INFO] Enviado match_id={match_id} ({strategy})")
        sent_tips.append({
            'match_id': int(match_id),
            'strategy': strategy,
            'sent_time': datetime.now(MANAUS_TZ),
            'status': 'pending',
            'message_id': message_obj.message_id,
            'message_text': message
        })
    except Exception as e:
        print(f"[ERROR] send_message {match_id}: {e}")


async def periodic_check(bot):
    global last_summary
    while True:
        try:
            await asyncio.sleep(300)  # 5 min
            print("[INFO] Verificando status das tips...")
            ended = fetch_ended_matches()
            ended_dict = {}
            for m in ended:
                try:
                    ended_dict[int(m['eventID'])] = m
                except Exception:
                    continue
            today = datetime.now(MANAUS_TZ).date()
            greens = reds = 0
            for tip in sent_tips:
                if tip['sent_time'].date() != today:
                    continue
                if tip['status'] == 'pending':
                    m = ended_dict.get(tip['match_id'])
                    if m:
                        # Verificação para estratégias HT
                        if "HT" in tip['strategy']:
                            ht_goals = (m.get('scoreHT', {}).get('home', 0) or 0) + (
                                    m.get('scoreHT', {}).get('away', 0) or 0)
                            if tip['strategy'] == '+0.5 HT':
                                tip['status'] = 'green' if ht_goals > 0 else 'red'
                            elif tip['strategy'] == '+1.5 HT':
                                tip['status'] = 'green' if ht_goals > 1 else 'red'
                            elif tip['strategy'] == '+2.5 HT':
                                tip['status'] = 'green' if ht_goals > 2 else 'red'
                        # Verificação para estratégias FT
                        else:
                            ft_goals = (m.get('scoreFT', {}).get('home', 0) or 0) + (
                                    m.get('scoreFT', {}).get('away', 0) or 0)
                            if "1.5, 2.0" in tip['strategy']:
                                tip['status'] = 'green' if 2 <= ft_goals <= 3 else 'red'
                            elif "2.5, 3.0" in tip['strategy']:
                                tip['status'] = 'green' if 3 <= ft_goals <= 4 else 'red'
                            elif "+1.5 gols" in tip['strategy']:
                                # Para este tipo de estratégia, verificamos se o jogador específico marcou mais de 1.5 gols
                                player_name = tip['strategy'].split('+1.5 gols ')[1]
                                # Determinar se é o jogador da casa ou visitante
                                home = tip['message_text'].split('🎮 ')[1].split(' vs ')[0].strip()
                                away = tip['message_text'].split(' vs ')[1].split('\n')[0].strip()
                                home_goals = m.get('scoreFT', {}).get('home', 0) or 0
                                away_goals = m.get('scoreFT', {}).get('away', 0) or 0

                                if player_name in home:
                                    tip['status'] = 'green' if home_goals > 1.5 else 'red'
                                elif player_name in away:
                                    tip['status'] = 'green' if away_goals > 1.5 else 'red'
                                else:
                                    tip['status'] = 'red'  # Não identificou o jogador

                        print(f"[DEBUG] Tip {tip['match_id']} ⇒ {tip['status']}")
                        # Editar a mensagem original
                        if tip['status'] in ['green', 'red']:
                            emoji = "✅✅✅✅✅" if tip['status'] == 'green' else "❌❌❌❌❌"
                            new_text = tip['message_text'] + f"{emoji}"
                            try:
                                await bot.edit_message_text(chat_id=CHAT_ID, message_id=tip['message_id'], text=new_text,
                                                            parse_mode="HTML", disable_web_page_preview=True)
                                print(f"[INFO] Mensagem {tip['message_id']} editada para {tip['status']}")
                            except Exception as edit_e:
                                print(f"[ERROR] Erro ao editar mensagem {tip['message_id']}: {edit_e}")
                if tip['status'] == 'green': greens += 1
                if tip['status'] == 'red': reds += 1
            total = greens + reds
            if total > 0:
                perc = (greens / total) * 100.0
                current_summary = (
                        f"\n\n<b>👑 ʀᴡ ᴛɪᴘs - ғɪғᴀ 🎮</b>\n\n"
                        f"<b>✅ Green [{greens}] x [{reds}] Red ❌</b>\n\n"
                        f"📊 <i>Desempenho: {perc:.2f}%</i>\n\n"
                )
                if current_summary != last_summary:
                    try:
                        await bot.send_message(chat_id=CHAT_ID, text=current_summary, parse_mode="HTML")
                        last_summary = current_summary
                        print("[INFO] Indicador enviado.")
                    except Exception as e:
                        print(f"[ERROR] indicador: {e}")
                else:
                    print("[INFO] Indicador igual ao anterior — não reenviado.")
            else:
                print("[INFO] Sem resultados para indicador.")
        except Exception as e:
            print(f"[ERROR] periodic_check: {e}")


async def main():
    bot = Bot(token=BOT_TOKEN)
    sent_matches = set()
    asyncio.create_task(periodic_check(bot))
    while True:
        print(f"[INFO] Ciclo às {datetime.now(MANAUS_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            bet365_dict = fetch_bet365_ids()
            matches = fetch_old_live_matches()
            for match in matches:
                if not isinstance(match, dict):
                    print(f"[WARN] Match inválido: {match}")
                    continue
                match_id = match.get('id', 'unknown')
                league_name = (match.get('league') or {}).get('name', 'Desconhecida')
                home = (match.get('home') or {}).get('name', 'Desconhecido')
                away = (match.get('away') or {}).get('name', 'Desconhecido')
                print(f"[DEBUG] Match {match_id}: {home} vs {away} | {league_name}")
                # Placar atual
                ss = match.get('ss')
                if ss:
                    try:
                        home_goals, away_goals = map(int, ss.split('-'))
                    except Exception:
                        home_goals, away_goals = 0, 0
                else:
                    home_goals, away_goals = 0, 0
                total_goals = home_goals + away_goals
                current_time = get_match_time_in_minutes(match)

                # Jogadores (apenas nomes entre parênteses, se houver)
                player1 = home.split('(')[-1].rstrip(')') if '(' in home else home
                player2 = away.split('(')[-1].rstrip(')') if '(' in away else away
                k = (player1.lower(), player2.lower())
                bet365_ev_id = bet365_dict.get(k)
                # H2H
                h2h_data = fetch_h2h_data(player1, player2)
                h2h_metrics = calculate_h2h_metrics(h2h_data, league_name)

                # ---------- Estratégias para o Primeiro Tempo (HT) ----------
                # Verifica se está no primeiro tempo antes de processar estratégias HT
                if is_first_half(match, league_name):
                    # Estratégia +1.5 HT: 1x0 ou 0x1
                    if (home_goals == 1 and away_goals == 0) or (home_goals == 0 and away_goals == 1):
                        # 8 mins play: até 3'
                        if ("8 mins play" in league_name
                                and current_time <= 3
                                and h2h_metrics
                                and h2h_metrics.get('over_1_5_ht_percentage', 0) >= 100.0
                                and h2h_metrics.get('over_2_5_ht_percentage', 0) >= 70.0):
                            print(f"[DEBUG] {match_id}: +1.5 HT (8m) OK")
                            msg = format_message(match, h2h_metrics, "+1.5 HT", bet365_ev_id)
                            await send_message(bot, match_id, msg, sent_matches, "+1.5 HT")

                        # 12/10 mins play: até 5'
                        elif (("12 mins play" in league_name or "10 mins play" in league_name)
                              and current_time <= 5
                              and h2h_metrics
                              and h2h_metrics.get('over_1_5_ht_percentage', 0) >= 100.0
                              and h2h_metrics.get('over_2_5_ht_percentage', 0) >= 85.0):
                            print(f"[DEBUG] {match_id}: +1.5 HT (12/10m) OK")
                            msg = format_message(match, h2h_metrics, "+1.5 HT", bet365_ev_id)
                            await send_message(bot, match_id, msg, sent_matches, "+1.5 HT")
                        else:
                            print(
                                f"[DEBUG] {match_id}: +1.5 HT NÃO — t={current_time:.2f} O1.5={h2h_metrics.get('over_1_5_ht_percentage','N/A') if h2h_metrics else 'N/A'} O2.5={h2h_metrics.get('over_2_5_ht_percentage','N/A') if h2h_metrics else 'N/A'}")

                    # Estratégia +0.5 HT: 0x0
                    if total_goals == 0:
                        if "Esoccer Battle - 8 mins play" in league_name:
                            # Critérios para Battle 8m
                            if (h2h_metrics
                                    and h2h_metrics.get('over_1_5_ht_percentage', 0) >= 100.0
                                    and h2h_metrics.get('btts_ht_percentage', 0) >= 100.0
                                    and current_time <= 3):
                                print(f"[DEBUG] {match_id}: +0.5 HT (Battle 8m) OK")
                                msg = format_message(match, h2h_metrics, "+0.5 HT", bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, "+0.5 HT")
                            else:
                                print(
                                    f"[DEBUG] {match_id}: +0.5 HT (Battle 8m) NÃO — O1.5={h2h_metrics.get('over_1_5_ht_percentage','N/A') if h2h_metrics else 'N/A'} BTTS={h2h_metrics.get('btts_ht_percentage','N/A') if h2h_metrics else 'N/A'} t={current_time:.2f}")
                        else:
                            # Outras ligas — incluir taxa de ataques perigosos
                            da_rate = calculate_dangerous_attacks_rate(match, current_time)
                            if da_rate >= 1.0 and h2h_metrics and h2h_metrics.get('over_0_5_ht_percentage', 0) >= 100.0 and \
                                    h2h_metrics.get('over_1_5_ht_percentage', 0) >= 85.0:
                                print(f"[DEBUG] {match_id}: +0.5 HT OK — da_rate={da_rate:.2f}")
                                msg = format_message(match, h2h_metrics, "+0.5 HT", bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, "+0.5 HT")
                            else:
                                print(
                                    f"[DEBUG] {match_id}: +0.5 HT NÃO — O0.5={h2h_metrics.get('over_0_5_ht_percentage','N/A') if h2h_metrics else 'N/A'} O1.5={h2h_metrics.get('over_1_5_ht_percentage','N/A') if h2h_metrics else 'N/A'} da_rate={da_rate:.2f}")

                # ---------- Estratégias para o Tempo Todo (FT) ----------
                # Estratégias para placar 1x0 ou 0x1
                if (home_goals == 1 and away_goals == 0) or (home_goals == 0 and away_goals == 1):
                    # Para Esoccer Battle - 8 mins play e Esoccer H2H GG League - 8 mins play
                    if ("Esoccer Battle - 8 mins play" in league_name or "Esoccer H2H GG League - 8 mins play" in league_name) and current_time > 4:
                        if h2h_metrics and (h2h_metrics['player1_avg_goals'] + h2h_metrics['player2_avg_goals'] >= 4.0):
                            strategy = "2.5, 3.0 gols FT"
                            print(f"[DEBUG] {match_id}: {strategy} OK (8m)")
                            msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                            await send_message(bot, match_id, msg, sent_matches, strategy)

                    # Para Esoccer GT Leagues – 12 mins play
                    elif "Esoccer GT Leagues – 12 mins play" in league_name and current_time > 4:
                        if h2h_metrics and (h2h_metrics['player1_avg_goals'] + h2h_metrics['player2_avg_goals'] >= 4.0):
                            strategy = "2.5, 3.0 gols FT"
                            print(f"[DEBUG] {match_id}: {strategy} OK (GT 12m)")
                            msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                            await send_message(bot, match_id, msg, sent_matches, strategy)

                # Estratégias para placar 0x0
                if total_goals == 0:
                    # Para Esoccer Battle - 8 mins play e Esoccer H2H GG League - 8 mins play
                    if ("Esoccer Battle - 8 mins play" in league_name or "Esoccer H2H GG League - 8 mins play" in league_name) and current_time > 4:
                        if h2h_metrics:
                            # "+1.5 gols {player}" para cada jogador
                            if h2h_metrics['player1_avg_goals'] >= 2.5 and h2h_metrics['player1_win_percentage'] >= 60.0:
                                strategy = f"+1.5 gols {player1}"
                                print(f"[DEBUG] {match_id}: {strategy} OK (8m)")
                                msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, strategy)

                            if h2h_metrics['player2_avg_goals'] >= 2.5 and h2h_metrics['player2_win_percentage'] >= 60.0:
                                strategy = f"+1.5 gols {player2}"
                                print(f"[DEBUG] {match_id}: {strategy} OK (8m)")
                                msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, strategy)

                            # "1.5, 2.0 gols FT" se soma das médias >= 3.0
                            if h2h_metrics['player1_avg_goals'] + h2h_metrics['player2_avg_goals'] >= 3.0:
                                strategy = "1.5, 2.0 gols FT"
                                print(f"[DEBUG] {match_id}: {strategy} OK (8m)")
                                msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, strategy)

                    # Para Esoccer GT Leagues – 12 mins play
                    elif "Esoccer GT Leagues – 12 mins play" in league_name:
                        # "+1.5 gols {player}" para cada jogador (tempo > 6 minutos)
                        if current_time > 6 and h2h_metrics:
                            if h2h_metrics['player1_avg_goals'] >= 2.5 and h2h_metrics['player1_win_percentage'] >= 60.0:
                                strategy = f"+1.5 gols {player1}"
                                print(f"[DEBUG] {match_id}: {strategy} OK (GT 12m)")
                                msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, strategy)

                            if h2h_metrics['player2_avg_goals'] >= 2.5 and h2h_metrics['player2_win_percentage'] >= 60.0:
                                strategy = f"+1.5 gols {player2}"
                                print(f"[DEBUG] {match_id}: {strategy} OK (GT 12m)")
                                msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                                await send_message(bot, match_id, msg, sent_matches, strategy)

                        # "1.5, 2.0 gols FT" (tempo > 4 minutos)
                        if current_time > 4 and h2h_metrics and (
                                h2h_metrics['player1_avg_goals'] + h2h_metrics['player2_avg_goals'] >= 3.0):
                            strategy = "1.5, 2.0 gols FT"
                            print(f"[DEBUG] {match_id}: {strategy} OK (GT 12m)")
                            msg = format_message(match, h2h_metrics, strategy, bet365_ev_id)
                            await send_message(bot, match_id, msg, sent_matches, strategy)

        except Exception as e:
            print(f"[ERROR] loop principal: {e}")
        await asyncio.sleep(15)  # a cada 30s


if __name__ == "__main__":
    asyncio.run(main())