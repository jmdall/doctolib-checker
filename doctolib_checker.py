import argparse
import datetime
import html
import http.client
import json
import pathlib
import threading
import time
import urllib
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CONFIG_PATH = pathlib.Path(__file__).parent.resolve() / "config.yaml"


def load_config():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("Missing dependency: pyyaml. Install with `pip install pyyaml`.") from exc

    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def parse_date(date_str):
    return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()


def send_pushover_notification(config, message):
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request(
        "POST",
        "/1/messages.json",
        urllib.parse.urlencode(
            {
                "token": config["pushover_credentials"]["api_token"],
                "user": config["pushover_credentials"]["user_key"],
                "message": message,
            }
        ),
        {"Content-type": "application/x-www-form-urlencoded"},
    )
    conn.getresponse()


def get_closest_available_time_slot(json_data, limit_date):
    for slot in json_data["availabilities"]:
        if not slot["slots"]:
            continue
        if datetime.datetime.strptime(slot["date"][:10], "%Y-%m-%d").date() <= limit_date:
            return slot["slots"][0]
    return ""


def format_string_to_date(date):
    return str(datetime.datetime.strptime(date[:-10], "%Y-%m-%dT%H:%M:%S"))


def get_date_windows(start_date, end_date, max_days=15):
    windows = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + datetime.timedelta(days=max_days - 1), end_date)
        limit = (window_end - cursor).days + 1
        windows.append((cursor, limit, window_end))
        cursor = window_end + datetime.timedelta(days=1)
    return windows


def fetch_slots(url_template, window_start, limit):
    url = url_template % {"start_date": window_start.strftime("%Y-%m-%d"), "limit": limit}
    req = urllib.request.Request(url, headers={"User-Agent": "Magic Browser"})
    con = urllib.request.urlopen(req)
    return json.loads(con.read())


def list_doctors(config):
    doctors = config.get("doctors", [])
    if not doctors and config.get("url"):
        doctors = [{"name": "Default doctor", "url": config["url"]}]
    if not doctors:
        raise ValueError("No doctor configured. Add at least one entry in 'doctors'.")
    return doctors


def find_doctor(config, doctor_name=None):
    doctors = list_doctors(config)
    if not doctor_name:
        return doctors[0]
    for doctor in doctors:
        if doctor["name"].lower() == doctor_name.lower():
            return doctor
    raise ValueError(f"Doctor '{doctor_name}' not found in config.")


def resolve_dates(config, doctor, from_date=None, to_date=None):
    from_date = from_date or doctor.get("start_date") or config.get("start_date")
    to_date = to_date or doctor.get("limit_date") or config.get("limit_date")

    if not from_date or not to_date:
        raise ValueError("Both start and end date are required.")

    start_date = parse_date(from_date)
    end_date = parse_date(to_date)

    if start_date < datetime.date.today():
        start_date = datetime.date.today()
    if end_date < start_date:
        raise ValueError("End date must be greater than or equal to start date.")

    return start_date, end_date


def check_appointments(doctor, start_date, end_date):
    for window_start, limit, window_end in get_date_windows(start_date, end_date):
        json_data = fetch_slots(doctor["url"], window_start, limit)

        if json_data["total"] > 0:
            closest_slot = get_closest_available_time_slot(json_data, end_date)
            if closest_slot:
                return {
                    "doctor": doctor["name"],
                    "slot": closest_slot,
                    "window": f"{window_start} → {window_end}",
                    "total": json_data["total"],
                }
        elif json_data.get("next_slot"):
            next_slot = datetime.datetime.strptime(json_data["next_slot"][:10], "%Y-%m-%d").date()
            if next_slot <= end_date:
                return {
                    "doctor": doctor["name"],
                    "slot": json_data["next_slot"],
                    "window": f"{window_start} → {window_end}",
                    "total": json_data.get("total", 0),
                }
    return None


class BackgroundMonitor:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.thread = None
        self.stop_event = threading.Event()
        self.status = "stopped"
        self.last_check = "never"
        self.last_result = ""
        self.settings = None

    def start(self, doctor_name, from_date, to_date, interval_seconds):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return "A monitor is already running. Stop it first."
            self.settings = {
                "doctor_name": doctor_name,
                "from_date": from_date,
                "to_date": to_date,
                "interval": interval_seconds,
            }
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            self.status = "running"
            return "Monitor started."

    def stop(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.status = "stopped"
                return "No monitor running."
            self.stop_event.set()
            self.status = "stopping"
            return "Stopping monitor..."

    def _run(self):
        try:
            doctor = find_doctor(self.config, self.settings["doctor_name"])
            start_date, end_date = resolve_dates(
                self.config, doctor, self.settings["from_date"], self.settings["to_date"]
            )
            interval = max(30, int(self.settings["interval"]))

            while not self.stop_event.is_set():
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.last_check = now
                result = check_appointments(doctor, start_date, end_date)

                if result:
                    message = (
                        f"New appointment available for {result['doctor']}!\n"
                        f"Date range: {start_date} → {end_date}\n"
                        f"Earliest appointment: {format_string_to_date(result['slot'])}"
                    )
                    self.last_result = message
                    send_pushover_notification(self.config, message)
                else:
                    self.last_result = (
                        f"No appointment found for {doctor['name']} between {start_date} and {end_date}."
                    )

                self.stop_event.wait(interval)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.last_result = f"Error: {e}"
        finally:
            self.status = "stopped"


def build_html(config, monitor, notice=""):
    doctor_options = "\n".join(
        [f"<option>{html.escape(doc['name'])}</option>" for doc in list_doctors(config)]
    )
    status = html.escape(monitor.status)
    last_check = html.escape(str(monitor.last_check))
    last_result = html.escape(str(monitor.last_result))
    notice_html = f"<p><strong>{html.escape(notice)}</strong></p>" if notice else ""

    return f"""<!doctype html>
<html>
<head><meta charset='utf-8'><title>Doctolib Checker</title></head>
<body style='font-family:Arial;max-width:720px;margin:40px auto;'>
  <h1>Doctolib Checker</h1>
  {notice_html}
  <p>Status: <b>{status}</b><br>Last check: {last_check}</p>
  <p>Last result:<br><pre>{last_result}</pre></p>

  <h2>Start background monitoring</h2>
  <form method='post' action='/start'>
    <label>Doctor</label><br>
    <select name='doctor'>{doctor_options}</select><br><br>
    <label>Start date (YYYY-MM-DD)</label><br>
    <input name='from_date' required><br><br>
    <label>End date (YYYY-MM-DD)</label><br>
    <input name='to_date' required><br><br>
    <label>Interval (seconds)</label><br>
    <input name='interval' value='300' required><br><br>
    <button type='submit'>Start</button>
  </form>

  <h2>Stop monitoring</h2>
  <form method='post' action='/stop'>
    <button type='submit'>Stop</button>
  </form>
</body>
</html>"""


def start_web_server(config, host, port):
    monitor = BackgroundMonitor(config)

    class Handler(BaseHTTPRequestHandler):
        def _render(self, notice=""):
            body = build_html(config, monitor, notice).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._render()

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = urllib.parse.parse_qs(raw)

            if self.path == "/start":
                notice = monitor.start(
                    payload.get("doctor", [""])[0],
                    payload.get("from_date", [""])[0],
                    payload.get("to_date", [""])[0],
                    payload.get("interval", ["300"])[0],
                )
                self._render(notice)
                return

            if self.path == "/stop":
                notice = monitor.stop()
                self._render(notice)
                return

            self.send_error(404)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Web UI available on http://{host}:{port}")
    server.serve_forever()


def parse_args():
    parser = argparse.ArgumentParser(description="Doctolib appointment checker")
    parser.add_argument("--interactive", action="store_true", help="Prompt for doctor and dates")
    parser.add_argument("--doctor", help="Doctor name configured in config.yaml")
    parser.add_argument("--from-date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", help="End date YYYY-MM-DD")
    parser.add_argument("--web", action="store_true", help="Run lightweight web interface")
    parser.add_argument("--host", default="127.0.0.1", help="Web host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Web port (default: 8080)")
    return parser.parse_args()


def run_cli(config, args):
    doctor = find_doctor(config, args.doctor)

    from_date = args.from_date
    to_date = args.to_date
    if args.interactive:
        from_date = input("Start date (YYYY-MM-DD): ").strip()
        to_date = input("End date (YYYY-MM-DD): ").strip()

    start_date, end_date = resolve_dates(config, doctor, from_date, to_date)

    while True:
        result = check_appointments(doctor, start_date, end_date)
        if result:
            message = (
                f"New appointment available for {result['doctor']}!\n"
                f"Date range: {start_date} → {end_date}\n"
                f"Earliest appointment: {format_string_to_date(result['slot'])}"
            )
            send_pushover_notification(config, message)
            print(message)
        else:
            print(f"No appointment found for {doctor['name']} between {start_date} and {end_date}.")

        if config.get("run_in_loop"):
            time.sleep(config["interval_in_seconds"])
        else:
            break


def main():
    args = parse_args()
    config = load_config()
    if args.web:
        start_web_server(config, args.host, args.port)
        return
    run_cli(config, args)


if __name__ == "__main__":
    main()
