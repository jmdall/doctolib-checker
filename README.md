# doctolib-checker
Script Python pour surveiller les disponibilités Doctolib et notifier via Pushover.

## Ce que tu peux faire maintenant
- Choisir un **docteur par nom**.
- Vérifier les créneaux **entre 2 dates**.
- Utiliser une **interface web légère** pour démarrer/arrêter une surveillance en tâche de fond.
- Garder aussi le mode CLI.

## Installation
- Python 3.11+
- Dépendance:
```bash
pip install pyyaml
```

## Configuration (`config.yaml`)
- `doctors`: liste de docteurs (`name`, `start_date`, `limit_date`, `url`).
- Le `url` doit contenir `start_date=%(start_date)s&limit=%(limit)s`.
- `pushover_credentials`: `api_token` + `user_key`.

## Usage
### 1) Interface web (recommandé)
```bash
python doctolib_checker.py --web --host 0.0.0.0 --port 8080
```
Puis ouvre `http://localhost:8080`.

Depuis l’UI web tu peux:
- choisir le docteur,
- saisir date de début/fin,
- définir l’intervalle de check (ex: 300s),
- démarrer/arrêter la surveillance (thread de fond).

### 2) Mode CLI
```bash
python doctolib_checker.py --doctor "Dr Martin" --from-date 2026-03-01 --to-date 2026-04-01
```

### 3) Mode interactif terminal
```bash
python doctolib_checker.py --interactive --doctor "Dr Martin"
```

## Option cron (si tu préfères)
Si tu veux un check périodique système plutôt qu’un thread en mémoire, ajoute un cron:
```cron
*/5 * * * * cd /workspace/doctolib-checker && /usr/bin/python3 doctolib_checker.py --doctor "Dr Martin" --from-date 2026-03-01 --to-date 2026-04-01
```
