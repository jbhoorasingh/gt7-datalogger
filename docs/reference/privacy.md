# Privacy

GT7 Datalogger runs on your own computer, Raspberry Pi or server. Your recordings, laps,
settings and track surveys stay in its data folder. Nothing is sent to the project or to
anyone else unless this page says so.

## What leaves your network on its own

Three requests are made without you asking, each to download public data. None of them
uploads your recordings or settings. Each one can be turned off.

| Request | Goes to | When | What it sends | Turn it off |
|---|---|---|---|---|
| Car list refresh | `www.gran-turismo.com` | first start, then at most weekly | a normal web request | Settings › Advanced › **Refresh the car list** (`GT7_CAR_REFRESH=false`); the setup wizard and the Windows installer ask too |
| Shared track surveys | `jbhoorasingh.github.io` (GitHub Pages) | once, on a first start with no tracks; afterwards only when you open Tracks › Shared bundles | a normal web request | leave Settings › Advanced › **Shared track repository** empty (`GT7_SHARED_BUNDLES_URL=`); the Windows installer asks too |
| Update check | `api.github.com` | once a day | the app's version in its `User-Agent` | Settings › Advanced › **Check for updates** (`GT7_UPDATE_CHECK=false`); off by default except in the desktop app, where the setup wizard and the Windows installer ask |

Like any web request, each of these tells the server your IP address. Nothing is
downloaded and installed: the update check only shows that a new version exists.

## What leaves your network only when you set it up

- **Sync** sends your laps, track surveys or a live position stream to a sync service,
  only after you paste a connection string and turn each type on
  ([Settings › Sync](../guide/settings.md#sync)).
- **Notifications** post personal bests, session summaries and race events to the
  webhook URL you enter, such as a Discord channel.
- **Shared track pulls** from the Tracks view, when you press Pull.

## What stays on your local network

- **The PlayStation.** The app sends GT7's telemetry heartbeat to your console on the
  local network and receives its telemetry. With no console address saved, the heartbeat
  is broadcast on the local network to find it.
- **mDNS.** With LAN access on, the server announces itself as `_gt7dl._tcp` so the phone
  app finds it. The announcement carries a random name, never your computer's name.
- **Phones, tablets and browsers** you open the dashboard on talk to the server directly.

## The desktop app

The desktop app makes the same requests as any other install, with the update check on
by default. Its setup wizard shows each background request with a switch before
recording starts, and the Windows installer offers the same choices before the app first
runs. Logs and data stay in the folders listed under
[Where your data lives](../getting-started/desktop.md#where-your-data-lives).

There is no telemetry, analytics or crash reporting: the project does not know you run it.
