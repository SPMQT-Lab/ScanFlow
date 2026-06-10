# Lab sync + monitor toolkit

Two small utilities for working with an offline-ish lab PC:

```
DEV PC (this machine)               LAB PC (Createc rig)
═══════════════                    ═══════════════
                                    
  push_scanflow.bat  ────────►  ScanFlow source (editable -e install
                                  picks up the new files immediately)
                                  
                                  monitor_loop.bat
                                  └─ take_screenshots.py
                                     └─ writes PNGs every 5 min to a
                                        shared folder C:\…\screenshots
                                                              │
relay_to_slack.py ◄───────────  shared via SMB / OneDrive / …  ┘
   └─ forwards each new PNG
      to Slack or Telegram
```

There are three pieces. All three are independent — set them up in
whichever order you like.

---

## 1.  Push code from this PC to the lab PC

**File:** `tools/lab-sync/push_scanflow.bat` (runs on the DEV PC)

Robocopy mirrors your local `ScanFlow\` folder to a SMB share on the
lab PC. Because you installed ScanFlow with `pip install -e .`,
overwriting the source files is enough — no reinstall.

**Setup:**

1. On the lab PC, share `C:\Tools\ScanFlow` for read/write to your
   dev-PC user. (Right-click → Properties → Sharing → Advanced.)
2. On the dev PC, test the share is reachable: `dir \\LABPC\Tools\ScanFlow`.
3. Edit the top of `push_scanflow.bat`:

   ```bat
   set "SRC=C:\Tools\ScanFlow"
   set "DST=\\LABPC\Tools\ScanFlow"
   ```

4. Double-click the .bat. Excluded by default: `.git`, `__pycache__`,
   `.pytest_cache`, `*.pyc`, `*.log`, virtualenvs, build artefacts.

Uses `/E` not `/MIR` — files on the lab PC outside your source tree
are never deleted. Change to `/MIR` only if you really want strict
mirroring.

---

## 2.  Screenshot the lab PC

**Files:** `tools/lab-monitor/take_screenshots.py` and
`tools/lab-monitor/monitor_loop.bat` (both run on the LAB PC)

The Python script grabs three PNGs per cycle:

| File | Contents |
|---|---|
| `<stamp>_desktop.png` | All monitors, full pixel grid |
| `<stamp>_scanflow.png` | Just the ScanFlow window (matched by title substring) |
| `<stamp>_createc.png` | Just the STMAFM window |

`<stamp>` is `YYYYMMDDTHHMMSSZ` (UTC), so filenames sort naturally and
never collide.

**Setup:**

1. On the lab PC, activate the ScanFlow venv and install Pillow + pywin32:

   ```cmd
   pip install pillow pywin32
   ```

2. Make the output folder and share it so the dev PC can read:

   ```cmd
   mkdir C:\ScanflowMonitor\screenshots
   ```

   Share `C:\ScanflowMonitor` as `ScanflowMonitor` (read-only access
   is fine for your dev-PC user).

3. Edit the top of `monitor_loop.bat`:

   ```bat
   set "PYTHON=C:\Tools\venv\Scripts\python.exe"
   set "SCRIPT=C:\Tools\ScanFlow\tools\lab-monitor\take_screenshots.py"
   set "OUTPUT=C:\ScanflowMonitor\screenshots"
   set "INTERVAL=300"
   ```

4. Double-click `monitor_loop.bat`. It captures every 5 min until you
   close the CMD window.

### Unattended operation — use Task Scheduler

For a setup that survives log-off / reboot, register the `--once` form
as a scheduled task:

1. Open Task Scheduler → Create Basic Task.
2. Trigger: every 5 minutes.
3. Action: Start a program:
   - Program: `C:\Tools\venv\Scripts\python.exe`
   - Arguments: `C:\Tools\ScanFlow\tools\lab-monitor\take_screenshots.py --once`
4. Settings → "Run whether user is logged on or not" if you want it
   to survive log-off.

### Custom window titles

Real STMAFM versions sometimes have window titles like `STMAFM2022.10.07`
or `pstmafm – RIG_NAME`. Pass a more specific substring:

```cmd
python take_screenshots.py --scanflow-title "ScanFlow" ^
                           --createc-title "STMAFM"
```

If a window isn't found that cycle the script just logs a warning and
keeps going — no crash.

---

## 3.  Relay screenshots to Slack or Telegram

**File:** `tools/lab-monitor/relay_to_slack.py` (runs on the DEV PC)

Watches the screenshot folder and forwards each new PNG to your
chosen messenger. A JSON state file tracks what's already been sent
so a restart doesn't re-spam you.

### Slack (recommended for labs)

1. Create a Slack app: https://api.slack.com/apps → Create New App
2. OAuth & Permissions → Bot Token Scopes → add `files:write`,
   `chat:write`.
3. Install the app to your workspace; copy the Bot User OAuth Token
   (`xoxb-…`).
4. Invite the bot to the channel that should receive screenshots
   (`/invite @your-bot` in that channel).
5. Get the channel ID — right-click the channel name → View channel
   details → bottom of the popup.
6. On the dev PC, set the two env vars and install slack_sdk:

   ```cmd
   pip install slack_sdk
   set SLACK_BOT_TOKEN=xoxb-...
   set SLACK_CHANNEL=C0123456789
   ```

7. Run the relay:

   ```cmd
   python tools\lab-monitor\relay_to_slack.py ^
       --folder \\LABPC\ScanflowMonitor\screenshots ^
       --interval 60
   ```

   First run: pass `--catch-up` once to mark all existing files as
   "already sent" — otherwise you'll get every PNG ever produced as
   a flood.

### Telegram (alternative, no app to register)

1. Talk to `@BotFather` → `/newbot`, follow the prompts. It hands you
   a token like `1234:ABCdef...`.
2. Talk to your new bot (just send `/start`). Then run, in a browser:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — the response
   includes your chat ID.
3. On the dev PC:

   ```cmd
   pip install requests
   set TELEGRAM_TOKEN=1234:ABCdef...
   set TELEGRAM_CHAT_ID=12345678
   ```

4. Run the same relay command — it auto-detects which backend to use.

### WhatsApp — why it's not in this script

WhatsApp doesn't have a free programmatic upload path for personal
accounts. The only sustainable option is **Twilio's WhatsApp Business
API** (paid, requires the user to opt in by texting a number).
Implementation differences: image must be hosted at a public URL that
Twilio fetches — so the relay would also have to upload each PNG to
S3 / Imgur / file.io first, then POST the URL.

If you actually need WhatsApp, easiest path:

1. Sign up for Twilio, enable WhatsApp sandbox.
2. Send a join-sandbox message from your phone.
3. Add a `twilio_uploader()` to `relay_to_slack.py` that POSTs to
   `https://api.twilio.com/...Messages.json` with `MediaUrl` pointing
   to wherever you stashed the PNG.

For most labs Slack or Telegram is enough and free.

---

## Networking notes

- All sharing is plain Windows SMB — make sure both PCs are on the
  same workgroup or domain, and the dev-PC user has been added to
  the share's permission list.
- If the lab PC is firewalled, allow inbound port 445 from the dev
  PC's IP only.
- For a multi-site setup (lab in one building, dev at home),
  substitute the SMB share for OneDrive / Dropbox / Syncthing —
  the relay just reads from a local folder so anything that
  mirrors the lab folder onto your dev PC works.
