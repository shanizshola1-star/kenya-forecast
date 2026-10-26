# Kenya weather forecast server

Public weather dashboards, plus a **staff portal** where logged-in staff add:

- **Warnings and alerts:** a coloured banner on every public page while active
- **County advisories:** forecasts and advice shown when someone selects that county and day
- **Forecast corrections:** replace an automatic figure for a county and day, with a reason
- **Weather stations and readings:** dots on the map and a table of the latest readings
- **Uploads:** a new KMD `7day.txt` file, or many station readings at once from a CSV

The Open-Meteo forecast refreshes by itself at **07:30 and 19:30 EAT**.

## What's inside

| Part | What it is |
|---|---|
| `app.py` | The back end: web server, database, logins, data API and scheduled updates |
| `manage.py` | Admin tasks run on the server: create the first admin, reset passwords |
| `public/` | The front end people see: `index.html`, `animated.html`, `kmd-outlook.html`, and `extras.js`, which adds staff data to them |
| `staff/` | The staff portal (front end), at `/staff` |
| `scripts/` | `update_forecast.py` (Open-Meteo) and `build_kmd.py` (KMD file conversion) |
| `data/` | Map boundaries, ward points, and the latest KMD file in `data/kmd/` |
| `instance/` | Created on first run: the database (`forecast.db`) and the secret key. **Back this folder up. Never share it.** |

## Set up (for the person running the server)

You need a computer that stays on, with Python 3.10 or newer. Linux is easiest; Windows also works.

```bash
# 1. Install the tools (once)
python -m pip install -r requirements.txt

# 2. Create the first administrator account (once)
python manage.py create-admin

# 3. Start the server
python app.py
```

Open `http://SERVER-ADDRESS:8000/` for the dashboard, and `http://SERVER-ADDRESS:8000/staff` to log in.
Administrators add other staff accounts in the portal under **Staff accounts**.

Settings, as environment variables:

- `KF_PORT`: port number (default `8000`)
- `KF_HOST`: address to listen on (default `0.0.0.0`, meaning all)
- `KF_HTTPS=1`: set this once the site is served over https, so login cookies are only sent securely

## Before opening it to the internet: use https

Staff passwords travel to the server when they log in, so the site **must** be served over **https** when reachable from outside the office.
The usual way is to put a web server such as **Caddy** or **nginx** in front of `app.py`, with a free Let's Encrypt certificate.
With Caddy, this whole file (`Caddyfile`) is enough, for a domain pointed at the server:

```
forecast.example.co.ke {
    reverse_proxy 127.0.0.1:8000
}
```

Then start the app with `KF_HOST=127.0.0.1 KF_HTTPS=1 python app.py`.

## Keep it running

**Linux (systemd):** save as `/etc/systemd/system/kenya-forecast.service`, adjusting the paths and user:

```ini
[Unit]
Description=Kenya forecast server
After=network-online.target

[Service]
WorkingDirectory=/opt/kenya-forecast
ExecStart=/usr/bin/python3 app.py
Environment=KF_HOST=127.0.0.1 KF_HTTPS=1
Restart=always
User=forecast

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now kenya-forecast`.

**Windows:** create a Task Scheduler task that runs `python app.py` in this folder **at startup**, with "Run whether user is logged on or not".

## Everyday use

- **New KMD file:** staff portal → **Upload files** → choose `7day.txt` → **Upload**. The KMD page updates in about a minute.
- **Station readings in bulk:** save a spreadsheet as CSV with the columns `station, date, rain_mm, tmax, tmin, notes`, then upload it in the same place.
- **Forgotten password:** an administrator resets it under **Staff accounts**, or on the server run `python manage.py reset-password`.
- **Who changed what:** administrators see every change under **Activity log**.

## Fetching KMD files automatically (next step)

If KMD's files sit on a server reachable by SSH/SFTP (the kind you open with MobaXterm), a small job can copy `7day.txt` into `data/kmd/` after each run and then rebuild the page.
Ask for the server address, username, login method, and the folder the file is in. Keep passwords and keys out of this folder's files.

## Backups

Copy the `instance/` folder regularly. It holds every account, warning, advisory, station, reading and correction.
