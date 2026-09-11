import os
import json
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

# --- CHARGEMENT DYNAMIQUE DU TOP 10 ---
TOP_ROOMS_DEFAULT = [
    "A3-03 GRENIER A MUSIQUES", "A2-09 SCHUMANN", "A00-02 BEETHOVEN",
    "A2-04 CHOPIN", "B2-08 ALBENIZ", "A2-05 CAGE", "B0-03 MONK",
    "A1-08 Studio CROS", "A2-03 MENDELSSOHN", "B2-10 LIGETI"
]

def charger_top_rooms():
    if os.path.exists('salles.txt'):
        try:
            with open('salles.txt', 'r', encoding='utf-8') as f:
                rooms = [line.strip() for line in f if line.strip()]
                if rooms:
                    return rooms
        except Exception:
            print("⚠️ Erreur lors de la lecture de salles.txt, utilisation du défaut.")
    return TOP_ROOMS_DEFAULT

MOT_CLE = "Piano ENM"

# --- EXTRACTEUR JAVASCRIPT ---
JS_EXTRACTOR = """
() => {
    let roomMap = {};
    document.querySelectorAll('th').forEach(th => {
        let name = th.innerText.trim();
        if(name) {
            let rect = th.getBoundingClientRect();
            roomMap[name] = { left: rect.left, right: rect.right, reservations: [] };
        }
    });
    document.querySelectorAll('div.reservation').forEach(div => {
        let rect = div.getBoundingClientRect();
        let center = rect.left + (rect.width / 2);
        let mouseover = div.getAttribute('onmouseover') || '';
        let match = mouseover.match(/(\\d{2}:\\d{2})\\s*-\\s*(\\d{2}:\\d{2})/);
        if(match) {
            let timeSlot = { debut: match[1], fin: match[2] };
            for (let roomName in roomMap) {
                if (center >= roomMap[roomName].left && center <= roomMap[roomName].right) {
                    roomMap[roomName].reservations.push(timeSlot);
                    break;
                }
            }
        }
    });
    return roomMap;
}
"""

def ajuster_et_valider_creneau(occupations, req_debut, req_fin):
    req_deb_dt = datetime.datetime.strptime(req_debut, "%H:%M")
    req_fin_dt = datetime.datetime.strptime(req_fin, "%H:%M")
    total_duration_minutes = (req_fin_dt - req_deb_dt).total_seconds() / 60
    
    nouveau_deb_dt = req_deb_dt
    nouveau_fin_dt = req_fin_dt
    
    for res in occupations:
        res_deb_dt = datetime.datetime.strptime(res['debut'], "%H:%M")
        res_fin_dt = datetime.datetime.strptime(res['fin'], "%H:%M")
        
        if not (res_fin_dt <= nouveau_deb_dt or res_deb_dt >= nouveau_fin_dt):
            if res_deb_dt <= nouveau_deb_dt:
                nouveau_deb_dt = max(nouveau_deb_dt, res_fin_dt)
            if res_fin_dt >= nouveau_fin_dt and res_deb_dt < nouveau_fin_dt:
                nouveau_fin_dt = min(nouveau_fin_dt, res_deb_dt)
            elif res_deb_dt > nouveau_deb_dt and res_fin_dt < nouveau_fin_dt:
                nouveau_fin_dt = min(nouveau_fin_dt, res_deb_dt)

    retard_minutes = (nouveau_deb_dt - req_deb_dt).total_seconds() / 60
    if retard_minutes > 15:
        return None, None
        
    avance_minutes = (req_fin_dt - nouveau_fin_dt).total_seconds() / 60
    if avance_minutes > 0:
        max_truncation = min(30, total_duration_minutes * 0.20)
        if avance_minutes > max_truncation:
            return None, None
            
    duree_restante = (nouveau_fin_dt - nouveau_deb_dt).total_seconds() / 60
    if duree_restante < 30:
        return None, None
        
    return nouveau_deb_dt.strftime("%H:%M"), nouveau_fin_dt.strftime("%H:%M")

def get_calendar_service():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/calendar.events']
    )
    return build('calendar', 'v3', credentials=creds)

def main():
    print("🤖 Démarrage du Bot ENM...")
    top_rooms = charger_top_rooms()
    
    calendar_id = os.environ.get('CALENDAR_ID')
    service = get_calendar_service()

    now = datetime.datetime.utcnow().isoformat() + 'Z'
    dans_7_jours = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat() + 'Z'
    
    print("📅 Récupération de l'agenda...")
    events_result = service.events().list(
        calendarId=calendar_id, timeMin=now, timeMax=dans_7_jours,
        singleEvents=True, orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    cible_trouvee = False

    for event in events:
        titre = event.get('summary', '')
        lieu = event.get('location', '').strip()
        
        if MOT_CLE in titre and not lieu:
            cible_trouvee = True
            start_dt = datetime.datetime.fromisoformat(event['start'].get('dateTime'))
            end_dt = datetime.datetime.fromisoformat(event['end'].get('dateTime'))
            
            date_str = start_dt.strftime("%Y-%m-%d")
            req_deb_str = start_dt.strftime("%H:%M")
            req_fin_str = end_dt.strftime("%H:%M")
            
            print(f"🎯 Créneau repéré le {date_str} de {req_deb_str} à {req_fin_str}. Scraping de Rooming'it...")
            
            url = f"https://enmvilleurbanne.roomingit.fr/Agenda.php?Vue=Jour&DateCourante={date_str}"
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url)
                page.wait_for_timeout(3000)
                data_salles = page.evaluate(JS_EXTRACTOR)
                browser.close()
            
            salles_disponibles = []
            
            for salle_preferee in top_rooms:
                if salle_preferee in data_salles:
                    occupations = data_salles[salle_preferee]['reservations']
                    deb_valide, fin_validee = ajuster_et_valider_creneau(occupations, req_deb_str, req_fin_str)
                    if deb_valide and fin_validee:
                        salles_disponibles.append({
                            'nom': salle_preferee,
                            'deb': deb_valide,
                            'fin': fin_validee
                        })
                else:
                    salles_disponibles.append({
                        'nom': salle_preferee,
                        'deb': req_deb_str,
                        'fin': req_fin_str
                    })
                
                if len(salles_disponibles) >= 3:
                    break
            
            if salles_disponibles:
                meilleure_salle = salles_disponibles[0]
                print(f"✅ Top 3 trouvé ! Meilleure option : {meilleure_salle['nom']}")
                
                event['location'] = meilleure_salle['nom']
                
                description_texte = "🎹 Top 3 des salles à demander à l'accueil :\n"
                for i, s in enumerate(salles_disponibles, 1):
                    description_texte += f"{i}. {s['nom']} ({s['deb']} - {s['fin']})\n"
                
                event['description'] = description_texte.strip()
                
                if meilleure_salle['deb'] != req_deb_str:
                    event['start']['dateTime'] = start_dt.replace(
                        hour=int(meilleure_salle['deb'][:2]), minute=int(meilleure_salle['deb'][3:])
                    ).isoformat()
                if meilleure_salle['fin'] != req_fin_str:
                    event['end']['dateTime'] = end_dt.replace(
                        hour=int(meilleure_salle['fin'][:2]), minute=int(meilleure_salle['fin'][3:])
                    ).isoformat()
                
                service.events().update(calendarId=calendar_id, eventId=event['id'], body=event).execute()
                print("✏️ Agenda mis à jour avec le Top 3 !")
            else:
                print("🚨 Aucune salle dispo dans le Top 10 respectant les critères.")

    if not cible_trouvee:
        print("📭 Aucun créneau 'Piano ENM' sans lieu détecté pour les 7 prochains jours.")

if __name__ == '__main__':
    main()