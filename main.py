import os
import json
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

# --- CHARGEMENT DYNAMIQUE DU TOP 10 ---
try:
    with open('salles.txt', 'r', encoding='utf-8') as f:
        TOP_ROOMS = [line.strip() for line in f if line.strip()]
except FileNotFoundError:
    print("⚠️ Fichier salles.txt introuvable, utilisation d'un classement par défaut.")
TOP_ROOMS = [
    "A3-03 GRENIER A MUSIQUES", "A2-09 SCHUMANN", "A00-02 BEETHOVEN",
    "A2-04 CHOPIN", "B2-08 ALBENIZ", "A2-05 CAGE", "B0-03 MONK",
    "A1-08 Studio CROS", "A2-03 MENDELSSOHN", "B2-10 LIGETI"
]
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

def verifier_disponibilite(reservations, req_debut, req_fin):
    for res in reservations:
        if not (req_fin <= res['debut'] or req_debut >= res['fin']):
            return False
    return True

def get_calendar_service():
    # Le robot s'authentifie grâce au fichier JSON (stocké de façon sécurisée)
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/calendar.events']
    )
    return build('calendar', 'v3', credentials=creds)

def main():
    print("🤖 Démarrage du Bot ENM...")
    calendar_id = os.environ.get('CALENDAR_ID')
    service = get_calendar_service()

    # On cherche les événements sur les 7 prochains jours
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
        
        # Si on trouve "Piano ENM" et que la salle est vide
        if MOT_CLE in titre and not lieu:
            cible_trouvee = True
            start_dt = datetime.datetime.fromisoformat(event['start'].get('dateTime'))
            end_dt = datetime.datetime.fromisoformat(event['end'].get('dateTime'))
            
            date_str = start_dt.strftime("%Y-%m-%d")
            req_deb_str = start_dt.strftime("%H:%M")
            req_fin_str = end_dt.strftime("%H:%M")
            
            print(f"🎯 Créneau repéré le {date_str} de {req_deb_str} à {req_fin_str}. Scraping de Rooming'it...")
            
            url = f"https://enmvilleurbanne.roomingit.fr/Agenda.php?Vue=Jour&DateCourante={date_str}"
            
            # --- SCRAPING AVEC PLAYWRIGHT ---
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url)
                page.wait_for_timeout(3000)
                data_salles = page.evaluate(JS_EXTRACTOR)
                browser.close()
            
            # --- ANALYSE DES SALLES ---
            salle_choisie = "Maison (Aucune salle libre)"
            for salle_preferee in TOP_ROOMS:
                if salle_preferee in data_salles:
                    occupations = data_salles[salle_preferee]['reservations']
                    if verifier_disponibilite(occupations, req_deb_str, req_fin_str):
                        salle_choisie = salle_preferee
                        break
                else:
                    # Si la salle n'est pas codée aujourd'hui, on suppose qu'elle est dispo
                    salle_choisie = salle_preferee
                    break
            
            # --- MISE A JOUR DE L'AGENDA ---
            print(f"✅ Résultat de la recherche : {salle_choisie}")
            event['location'] = salle_choisie
            service.events().update(calendarId=calendar_id, eventId=event['id'], body=event).execute()
            print("✏️ Agenda mis à jour !")

    if not cible_trouvee:
        print("📭 Aucun créneau 'Piano ENM' sans lieu détecté pour les 7 prochains jours.")

if __name__ == '__main__':
    main()