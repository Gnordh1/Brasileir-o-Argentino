from playwright.sync_api import sync_playwright
import pandas as pd
import json
import sqlite3
from datetime import datetime
import time

# --- CONFIGURAÇÕES DOS CAMPEONATOS ---
CAMPEONATOS = [
    {"nome": "Argentina", "id": 155, "seasons": [70268, 77826]},
    {"nome": "Brasil", "id": 325, "seasons": [72034]}
]

def calcular_idade(timestamp):
    if not timestamp: return "N/A"
    try:
        if timestamp > 10000000000: timestamp = timestamp / 1000
        nasc = datetime.fromtimestamp(timestamp)
        hoje = datetime.now()
        return hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
    except: return "Erro"

def buscar_ids_e_nomes():
    jogos = []
    ids_vistos = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        page = context.new_page()       
        try:
            for camp in CAMPEONATOS:
                for season in camp['seasons']:
                    print(f"🔍 Buscando calendário: {camp['nome']} (ID: {camp['id']}) | Temporada {season}...")
                    # Temporada Regular - Brasileirão precisa de 38 rodadas
                    for r in range(1, 39): 
                        url = f"https://api.sofascore.com/api/v1/unique-tournament/{camp['id']}/season/{season}/events/round/{r}"
                        try:
                            page.goto(url, wait_until="networkidle", timeout=30000)
                            content = page.locator("body").inner_text()
                            if not content or content == "{}": continue
                            data = json.loads(content)
                            for e in data.get('events', []):
                                if e.get('status', {}).get('type') == 'finished':
                                    e_id = str(e['id'])
                                    if e_id not in ids_vistos:
                                        jogos.append({
                                            'id': e_id, 
                                            'timestamp': e.get('startTimestamp', 0),
                                            'home_backup': e['homeTeam']['name'],
                                            'away_backup': e['awayTeam']['name']
                                        })
                                        ids_vistos.add(e_id)
                        except: continue

                    # Mata-mata ou Blocos Recentes
                    for bloco in range(0, 11): 
                        url_bloco = f"https://api.sofascore.com/api/v1/unique-tournament/{camp['id']}/season/{season}/events/last/{bloco}"
                        try:
                            page.goto(url_bloco, wait_until="networkidle", timeout=30000)
                            content = page.locator("body").inner_text()
                            if not content or content == "{}": break
                            data = json.loads(content)
                            eventos = data.get('events', [])
                            if not eventos: break
                            for e in eventos:
                                e_id = str(e['id'])
                                if e.get('status', {}).get('type') == 'finished' and e_id not in ids_vistos:
                                    jogos.append({
                                        'id': e_id, 
                                        'timestamp': e.get('startTimestamp', 0),
                                        'home_backup': e['homeTeam']['name'],
                                        'away_backup': e['awayTeam']['name']
                                    })
                                    ids_vistos.add(e_id)
                        except: break
        finally: browser.close()
    return jogos

def extrair_consolidado():
    jogos = buscar_ids_e_nomes()
    if not jogos:
        print("❌ Nenhum jogo encontrado.")
        return

    print(f"🚀 Iniciando extração detalhada de {len(jogos)} partidas...")
    lista_bruta = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        page = context.new_page()
        
        for i, jogo in enumerate(jogos):
            try:
                # 1. INCIDENTES (Cartões)
                page.goto(f"https://api.sofascore.com/api/v1/event/{jogo['id']}/incidents", timeout=30000)
                inc_text = page.locator("body").inner_text()
                inc_data = json.loads(inc_text) if inc_text and inc_text != "{}" else {}
                
                cartoes_jogo = {}
                for incident in inc_data.get('incidents', []):
                    if incident.get('incidentType') == 'card':
                        p_id = incident.get('player', {}).get('id')
                        tipo = incident.get('incidentClass')
                        if p_id:
                            if p_id not in cartoes_jogo: cartoes_jogo[p_id] = {'amarelo': 0, 'vermelho': 0}
                            if tipo == 'yellow': cartoes_jogo[p_id]['amarelo'] += 1
                            elif tipo in ['red', 'yellowRed']: cartoes_jogo[p_id]['vermelho'] += 1

                # 2. LINEUPS (Estatísticas, Biometria e Foto)
                page.goto(f"https://api.sofascore.com/api/v1/event/{jogo['id']}/lineups", timeout=30000)
                lineup_text = page.locator("body").inner_text()
                if not lineup_text or lineup_text == "{}": continue
                lineup_data = json.loads(lineup_text)
                
                for lado in ['home', 'away']:
                    nome_time = lineup_data.get(lado, {}).get('team', {}).get('name')
                    if not nome_time or nome_time == "None":
                        nome_time = jogo['home_backup'] if lado == 'home' else jogo['away_backup']
                    
                    players = lineup_data.get(lado, {}).get('players', [])
                    for p_data in players:
                        stats = p_data.get('statistics', {})
                        if not stats or stats.get('minutesPlayed', 0) <= 0: continue
                        
                        p_info = p_data.get('player', {})
                        p_id = p_info.get('id')
                        
                        altura_cm = p_info.get('height', 0)
                        altura_m = round(altura_cm / 100, 2) if altura_cm > 0 else 0
                        
                        lista_bruta.append({
                            'player_id': p_id,
                            'nome': p_info.get('name'),
                            'url_foto': f"https://sofascore.com/api/v1/player/{p_id}/image",
                            'time': nome_time,
                            'nacionalidade': p_info.get('country', {}).get('name', 'N/A'),
                            'posicao': p_data.get('position') or p_info.get('position', 'N/A'),
                            'idade': calcular_idade(p_info.get('dateOfBirthTimestamp')),
                            'altura': altura_m,
                            'valor_mercado': p_info.get('proposedMarketValueRaw', {}).get('value', 0) if p_info.get('proposedMarketValueRaw') else 0,
                            'matches': 1,
                            'cartao_amarelo': cartoes_jogo.get(p_id, {}).get('amarelo', 0),
                            'cartao_vermelho' : cartoes_jogo.get(p_id, {}).get('vermelho', 0),
                            'timestamp': jogo['timestamp'],
                            **{k: v for k, v in stats.items() if not isinstance(v, dict)}
                        })
                
                if (i + 1) % 10 == 0:
                    print(f"✅ {i+1}/{len(jogos)} jogos processados...")

            except Exception as e:
                print(f"⚠️ Pulei o jogo {jogo['id']} devido a um erro: {e}")
                continue

        browser.close()

    if lista_bruta:
        print("📊 Consolidando estatísticas finais...")
        df = pd.DataFrame(lista_bruta)
        
        # 1. Lógica de Último Time e Foto Atual
        df_ultimo = df.sort_values('timestamp', ascending=False).drop_duplicates('player_id')
        df_ultimo_info = df_ultimo[['player_id', 'time', 'url_foto']].rename(columns={'time': 'time_atual'})

        # 2. Posição onde mais atuou (Baseado em Minutos)
        df_pos = df.groupby(['player_id', 'posicao'])['minutesPlayed'].sum().reset_index()
        df_pos = df_pos.sort_values('minutesPlayed', ascending=False).drop_duplicates('player_id')
        df_pos_oficial = df_pos[['player_id', 'posicao']].rename(columns={'posicao': 'pos_oficial'})

        # 3. Agregação Geral (Soma scouts, Média rating)
        cols_meta = ['player_id', 'nome', 'url_foto', 'time', 'posicao', 'pos_oficial', 'time_atual', 'idade', 'nacionalidade', 'altura', 'timestamp']
        agg_rules = {col: 'sum' for col in df.columns if col not in cols_meta}
        if 'rating' in agg_rules: agg_rules['rating'] = 'mean'

        df_final = df.groupby(['player_id', 'nome', 'idade', 'nacionalidade', 'altura']).agg({
            **agg_rules,
            'valor_mercado': 'max'
        }).reset_index()

        # 4. Merge das informações consolidadas
        df_final = df_final.merge(df_ultimo_info, on='player_id').merge(df_pos_oficial, on='player_id')
        
        # Limpeza e Formatação
        df_final = df_final.rename(columns={'time_atual': 'time', 'pos_oficial': 'posicao'}).round(2)
        
        # Ordem de colunas para o Power BI
        ordem_bi = ['player_id', 'nome', 'url_foto', 'time', 'nacionalidade', 'posicao', 'idade', 'altura', 'matches', 'valor_mercado', 'cartao_amarelo', 'cartao_vermelho']
        todas_cols = ordem_bi + [c for c in df_final.columns if c not in ordem_bi and c != 'timestamp']
        
        # Salva no Banco de Dados
        conn = sqlite3.connect('base_scouts_futebol.db')
        df_final[todas_cols].to_sql('scouts', conn, if_exists='replace', index=False)
        conn.close()
        
        print(f"💾 SUCESSO! Banco 'base_scouts_futebol.db' criado com {len(df_final)} jogadores.")

if __name__ == "__main__":
    extrair_consolidado()