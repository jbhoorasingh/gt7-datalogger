# Desktop app

The desktop app is GT7 Datalogger as one download for macOS, Windows or Linux: no
Docker, no Python, no terminal. It runs the same server a Docker install runs and
shows the same dashboard, in your own browser. On macOS and Windows it adds an icon in
the menu bar or the notification area.

!!! warning "Not released yet"
    The first desktop release will be **0.9.0**. Until it is published, the download
    links below lead nowhere, and the way to run GT7 Datalogger is
    [Docker](quick-start.md), a [Raspberry Pi](raspberry-pi.md) or
    [from source](local-development.md). This page describes the app as it is built
    for that release.

## Download

| System | File |
| --- | --- |
| macOS 13 or later, Apple silicon | [GT7-Datalogger-macOS-arm64.dmg](https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/GT7-Datalogger-macOS-arm64.dmg) |
| Windows 10 or 11, x64 | [GT7-Datalogger-Windows-x64-Setup.exe](https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/GT7-Datalogger-Windows-x64-Setup.exe) (installer) |
| Windows, no install | [GT7-Datalogger-Windows-x64-Portable.zip](https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/GT7-Datalogger-Windows-x64-Portable.zip) |
| Linux x86_64 (Ubuntu 22.04+, Debian 12+, Fedora, RHEL 8+) | [GT7-Datalogger-Linux-x86_64.AppImage](https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/GT7-Datalogger-Linux-x86_64.AppImage) |
| Linux, plain folder | [GT7-Datalogger-Linux-x86_64.tar.gz](https://github.com/jbhoorasingh/gt7-datalogger/releases/latest/download/GT7-Datalogger-Linux-x86_64.tar.gz) |

The file names carry no version, so these links always fetch the latest release.
Every release also has `SHA256SUMS.txt` to check a download against, and
`release-manifest.json`, which lists the files. Older versions are on the
[releases page](https://github.com/jbhoorasingh/gt7-datalogger/releases).

There is no build for Intel Macs, Windows on Arm or 32-bit systems. Windows on Arm
runs the x64 build under emulation. On an Intel Mac or a Raspberry Pi, use
[Docker](quick-start.md) or the [Pi guide](raspberry-pi.md).

GT7 Datalogger is an unofficial tool. It is not affiliated with, endorsed by or
sponsored by Sony Interactive Entertainment or Polyphony Digital.

## Install and first start

=== "macOS"

    1. Open the `.dmg` and drag **GT7 Datalogger** to **Applications**.
    2. Open it from Applications. macOS says it is an app downloaded from the internet
       and asks whether to open it: click **Open**. The app is signed and notarized,
       so this question comes once.
    3. Your browser opens the dashboard at `http://127.0.0.1:8000/`, and the app's
       icon appears in the menu bar.

=== "Windows"

    1. Run `GT7-Datalogger-Windows-x64-Setup.exe`. The first page lets you install
       for every user of the computer (the default, which needs an administrator) or
       for you only.
    2. If **Windows protected your PC** appears (SmartScreen), click **More info**,
       then **Run anyway**. It can appear for a new release while Windows is still
       learning the signature.
    3. Start **GT7 Datalogger** from the Start menu. Your browser opens the
       dashboard, and the app's icon appears in the notification area (it may hide
       under the **^** arrow).

    The portable `.zip` needs no install: unzip it anywhere and run
    `GT7 Datalogger\gt7-datalogger.exe`. It adds no firewall rules, so see
    [Firewall and local network](#firewall-and-local-network).

=== "Linux"

    1. Make the AppImage executable and run it:

        ```bash
        chmod +x GT7-Datalogger-Linux-x86_64.AppImage
        ./GT7-Datalogger-Linux-x86_64.AppImage
        ```

    2. Your browser opens the dashboard at `http://127.0.0.1:8000/`.

    Linux has no tray icon in this release. Running the AppImage again opens the
    dashboard. To stop the app, run it with `--quit`:

    ```bash
    ./GT7-Datalogger-Linux-x86_64.AppImage --quit
    ```

    The `.tar.gz` holds the same app as a plain folder: unpack it and run
    `gt7-datalogger/gt7-datalogger`.

    If the AppImage stops with *Cannot mount AppImage*, see
    [Linux: "Cannot mount AppImage"](#linux-cannot-mount-appimage).

### The setup wizard

The first start opens a short setup in the browser. Until you finish it, the app
runs the built-in simulator, records nothing, does not announce itself on the network
and only answers this computer.

1. Choose **Connect my PlayStation**, or **Try the demo** to stay on the simulator.
2. The wizard says which prompt your computer is about to show (next section), then
   searches the network for the console. If it finds nothing, type the console's IP
   address: on a PS5 it is under **Settings › Network › Connection Status › View
   Connection Status**.
3. Start a race, a time trial or a replay in GT7. The wizard waits for telemetry.
4. Choose whether phones and tablets may connect (see
   [Phones, tablets and other computers](#phones-tablets-and-other-computers)).
5. **Finish**. Recording is on from now on.

**Skip setup** also finishes setup, with recording on.

## Firewall and local network

The console sends telemetry to this computer over UDP, and the app sends it a
heartbeat. Your system may ask before it allows that. The answer sticks: if you click
**Don't allow** or **Cancel**, telemetry never arrives until you undo it.

=== "macOS"

    macOS 15 and later ask whether **GT7 Datalogger** may find devices on your local
    network. The question appears when the app first contacts the console. Click
    **Allow**.

    To undo **Don't Allow**: open **System Settings › Privacy & Security › Local
    Network**, turn **GT7 Datalogger** on, then search again in the wizard or in
    **Settings › Connection**. While it is blocked, the menu-bar item says *Local
    Network blocked*.

    If the macOS firewall is on and you refused incoming connections, open **System
    Settings › Network › Firewall › Options** and set GT7 Datalogger to allow them.

=== "Windows"

    The installer, when it installs for every user, adds two firewall rules for the
    app, *GT7 Datalogger (UDP in)* and *GT7 Datalogger (TCP in)*, limited to your
    local network. You see no prompt.

    An install for you only, and the portable `.zip`, have no rules. Windows shows a
    **Windows Defender Firewall** prompt when the app first listens for telemetry.
    Click **Allow**, with both **Private** and **Public** networks ticked: Windows
    calls a new network Public, so a rule for Private only blocks telemetry at home.
    Without the rules, the dashboard also only answers this computer, so a phone
    cannot connect.

    To undo **Cancel**, or to add the rules later: right-click the tray icon and
    choose **Repair firewall**. It asks for administrator rights once, removes any
    block rules and adds the two allow rules. Quit and reopen the app afterwards so
    the dashboard answers the network again. You can also allow it by hand in
    **Windows Security › Firewall & network protection › Allow an app through
    firewall**.

=== "Linux"

    Linux shows no prompt. If a firewall runs, open the telemetry ports, and the
    dashboard port for phones and tablets:

    ```bash
    # ufw
    sudo ufw allow 33739:33740/udp
    sudo ufw allow 8000/tcp

    # firewalld
    sudo firewall-cmd --permanent --add-port=33739-33740/udp --add-port=8000/tcp
    sudo firewall-cmd --reload
    ```

## The tray menu

On macOS (menu bar) and Windows (notification area) the icon's menu has:

| Item | What it does |
| --- | --- |
| *status line* | *Recording*, *PlayStation connected*, *Waiting for the PlayStation*, *Simulator*, or the problem: *UDP 33740 in use*, *Local Network blocked*, *Could not start* |
| **Open dashboard** | opens the dashboard in your browser |
| **Open Race Engineer** | opens `/engineer` |
| **Let my phone/tablet connect** | LAN access on or off ([below](#phones-tablets-and-other-computers)) |
| **Start at login** | starts the app in the background when you log in |
| **Open data folder**, **Open logs** | the folders in [Where your data lives](#where-your-data-lives) |
| **Repair firewall** | Windows only, see above |
| **Check for updates** | opens Settings, where **About** shows whether a newer version is out |
| **Quit GT7 Datalogger** | stops recording and the app. Laps already completed are saved |

Opening the app again while it runs opens the dashboard; a second copy never starts.

## Where your data lives

| | Data | Logs |
| --- | --- | --- |
| macOS | `~/Library/Application Support/GT7 Datalogger/` | `~/Library/Logs/GT7 Datalogger/` |
| Windows | `%LOCALAPPDATA%\GT7 Datalogger\` | `%LOCALAPPDATA%\GT7 Datalogger\logs\` |
| Linux | `~/.local/share/gt7-datalogger/` | `~/.local/state/gt7-datalogger/logs/` |

On Linux, `XDG_DATA_HOME` and `XDG_STATE_HOME` move them, as usual. **Settings ›
About** shows the data folder and log file of the running app.

The data folder holds the database `gt7.db`, the track bundles, sync state, the
pre-upgrade backups and `desktop.json`, the app's own preferences. The logs are
`gt7-datalogger.log` (the server, the same log as **Settings › Logs**),
`launcher.log` (the app around it) and `crash.log`. Attach them to an issue when
the app misbehaves.

To keep the data somewhere else, start the app with `--data-dir <folder>`; the logs
then go in a `logs` folder inside it.

## Changing the port

The dashboard is on port 8000. When another program holds it, the app takes the next
free port from 8001 to 8009 and remembers it. When the program on 8000 is another GT7
Datalogger (a Docker container, say), the app says so and opens that one instead:
stop the other one first.

To choose the port yourself, quit the app, edit `desktop.json` in the data folder and
start the app again:

```json
{
  "port": 8010
}
```

Or start it once with `--port 8010`. A port given on the command line is used as is,
never swapped for a free one.

The telemetry ports (UDP 33740 and 33739) are in **Settings › Advanced**.

## Phones, tablets and other computers

A phone or tablet can show `/dash` or the Race Engineer while you drive, and OBS on
another PC can use the overlays. That needs **LAN access**: other devices on your
network may connect. It is off until the end of the setup wizard, which asks.

Turn it on or off at any time with **Let my phone/tablet connect**, in the tray menu
or in **Settings › Phone app**. It applies at once, without a restart. While it is
off, the app refuses every other device and stops announcing itself on the
network.

With LAN access on:

- Reading is open: the dashboard, the overlays, `/dash`, `/engineer` and the live
  stream need nothing.
- Changing anything from another device needs the **admin token**. When you turn LAN
  access on in the wizard and no token is set, the app makes one and shows it once.
  On a tablet that should change settings, enter it once in **Settings › Access**.
- This computer's own browser never needs the token.

To connect a phone or tablet, open **Settings › Phone app** and scan the QR code, or
type the address it shows (`http://<this computer's address>:8000/`). The phone app
finds the server by itself while LAN access is on.

Lost the token? On this computer, set a new one in **Settings › Advanced › Admin
token**, then enter it again on each device that changes settings.

For a stricter setup, set `"strict_loopback": true` in `desktop.json`: the app then
listens on `127.0.0.1` only, and the system refuses every other device outright.

## Updating

Once a day the app asks GitHub whether a newer version is out. When one is, **Settings
› About** shows the version and a download link. It never downloads or installs
anything by itself.

To update, quit the app, then install the new version the way you installed the
first:

- **macOS:** drag the new GT7 Datalogger to Applications and replace the old one.
- **Windows:** run the new `Setup.exe`. With the portable `.zip`, replace the folder.
- **Linux:** replace the AppImage file.

Your data stays where it is. When a new version changes the database, it copies the
old one to `backups/` in the data folder first; see
[Troubleshooting](../reference/troubleshooting.md#getting-more-detail) for going
back. A database a newer version has opened does not open in an older one.

To stop the daily check, turn off **Check for updates** in **Settings › Advanced**. To be
offered release candidates too, set **Update channel** to `prerelease`.

## Uninstalling

Uninstalling removes the app, not your data.

- **macOS:** quit the app, turn off **Start at login** if you turned it on, and move
  GT7 Datalogger from Applications to the Bin.
- **Windows:** **Settings › Apps › Installed apps › GT7 Datalogger › Uninstall**. It
  also removes the firewall rules. For the portable `.zip`, delete the folder.
- **Linux:** quit the app with `--quit` and delete the AppImage.

To remove your recordings and settings as well, delete the data and log folders
listed in [Where your data lives](#where-your-data-lives). Back them up first if
you might want them again.

## Moving from Docker

To bring a Docker or Pi install's sessions, surveys, settings and layouts into the
desktop app, use a backup:

1. On the old install, open **Settings › Data**, tick every part under **Back up**
   and **Download backup**.
2. Stop the old install (`docker stop gt7-datalogger`). Both listen on UDP 33740 and
   port 8000, so they cannot run side by side on one computer.
3. Install the desktop app and finish or skip the setup wizard.
4. In the desktop app, open **Settings › Data › Restore**, choose the backup, tick
   the parts and **Restore**. Tick **Install identity** only when the desktop app
   replaces the old install for good. See [Putting it
   back](../guide/settings.md#putting-it-back) for what each part does.

What each browser keeps for itself does not move: units, language, overlay
presets, Race Engineer voice settings and Analysis channel picks are stored per
address, and the desktop app has a new one. OBS sources that pointed at the old
address need the new one, and the admin token is never in a backup.

If the old install cannot open Settings, copy its data folder by hand instead:

```bash
docker cp gt7-datalogger:/data ./gt7-data
```

Quit the desktop app, then copy the contents of `gt7-data` into its data folder,
replacing `gt7.db`. Leave out `source-id.json` and every `sync-*.json` unless the
old install is retired for good: they identify an install to the survey votes and the
sync service, and two installs sharing them count as one.

## What it fetches on its own

The app records with no internet at all. When it has some, it makes three kinds of
request without being asked: the GT7 car-list refresh (first start, then weekly), the
shared track pull (once, on a first start) and the daily update check. None of them
sends your recordings. [What the app fetches on its own](configuration.md#what-the-app-fetches-on-its-own)
lists each one and how to turn it off. Sync with an online service stays off until
you set it up.

## Linux: "Cannot mount AppImage"

An AppImage mounts itself with FUSE, which needs `fusermount` (or `fusermount3`).
When it is missing, the AppImage stops with *Cannot mount AppImage, please check your
FUSE setup*. Either install FUSE 3:

```bash
sudo apt install fuse3        # Debian, Ubuntu
sudo dnf install fuse3        # Fedora, RHEL
```

or run it without mounting, which unpacks it to a temporary folder on every start:

```bash
./GT7-Datalogger-Linux-x86_64.AppImage --appimage-extract-and-run
# or
APPIMAGE_EXTRACT_AND_RUN=1 ./GT7-Datalogger-Linux-x86_64.AppImage
```

The `.tar.gz` needs no FUSE at all.

## Command line

The app's executable takes a few options. On macOS it is
`/Applications/GT7 Datalogger.app/Contents/MacOS/gt7-datalogger`; on Windows,
`gt7-datalogger.exe` in the install folder; on Linux, the AppImage itself.

| Option | What it does |
| --- | --- |
| `--quit` | stop the running app (waits up to `--timeout` seconds, default 20) |
| `--status` | print the running app's state as JSON: mode, port, address |
| `--open` | open the dashboard once it is running |
| `--background` | never open the browser |
| `--port N` | serve the dashboard on port N |
| `--data-dir <folder>` | keep data and logs in this folder |
| `--headless` | run the server alone, with no tray and no browser, serving the network the way Docker does. For a server or a systemd unit; `--host` sets the bind address (default `0.0.0.0`) |
| `--self-test` | check that the app's files are complete and print the result as JSON |
| `--firewall install`, `remove`, `repair` | Windows, as administrator: add, remove or repair the firewall rules |

Exit codes: 0 success, 1 the command failed, 2 a usage error, 3 the app could not
start.
