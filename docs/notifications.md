# Discord, email and phone alerts

All three channels are off until you configure them. Secrets go in `.env`, never in
`config.yaml` and never in the apps.

## Discord

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**
2. Choose the channel, press **Copy Webhook URL**
3. Paste it into `.env` on the Minecraft PC:

```
MCSC_DISCORD_WEBHOOK=https://discord.com/api/webhooks/123456789/abcdef...
```

4. Restart the agent
5. In the desktop app: gear → **PC settings** → **Alerts** → tick Discord → **Send test**

Crash alerts arrive as an embed with the exit code, the likely cause, the
evidence lines, who was online, and the machine's RAM/CPU/disk at the time.

## Email

The host and addresses are not secret, so they live in `config/config.yaml`:

```yaml
notifications:
  email_enabled: true
  email:
    host: smtp.gmail.com
    port: 587
    use_tls: true
    use_ssl: false
    from_address: your.address@gmail.com
    to_addresses:
      - your.address@gmail.com
    attach_crash_report: true
    attach_log_tail: true
```

The credentials go in `.env`:

```
MCSC_SMTP_USERNAME=your.address@gmail.com
MCSC_SMTP_PASSWORD=your-app-password
```

**Gmail:** you must use an App Password, not your account password. Turn on
2-Step Verification, then create one at
https://myaccount.google.com/apppasswords.

Crash emails attach the crash report and the console tail, so you can read them
on a phone without opening the app.

## Phone alerts

The same alerts as a lock-screen notification on your phone. The Android and
iPhone apps get them through Firebase and Apple's push service; the steps are
under [The phone app](#the-phone-app) below. The old browser alerts (Web Push)
were removed.

## The phone app

The Android and iPhone apps (`mobile/`) have an **Alerts** tab with
the alerts the PC sent since the phone signed in: the same alerts as above,
kept by the agent (its newest 200) whether or not any channel is turned on.
A helper sees only the alerts about servers they may use; alerts about the
PC itself (failed sign-ins, the certificate, the PC's memory) are the
owner's.

**Lock-screen alerts in the Android app** go through Google's Firebase Cloud
Messaging (FCM), sent by the agent itself; nothing else sits in between.
The message carries no words, only the alert's number, and the phone reads
the alert from the PC over Tailscale. Setting it up is free:

1. In the [Firebase console](https://console.firebase.google.com/), make a
   project (Google Analytics: off), then add an Android app with the package
   name `io.github.peyoteheadlights.commandblock`. Download its
   `google-services.json`; it is for building the app (see
   `mobile/README.md`), not for the PC.
2. Still in the console, **Project settings → Service accounts → Generate
   new private key**. Keep the downloaded file safe: it can send messages
   to every phone of this project.
3. On the PC, from the app's folder:
   `python -m installer.setup_phone_alerts C:\path\to\key.json`.
   It checks the key and copies it into the data folder
   (`phone-alerts\fcm-service-account.json`), readable only by this account
   and administrators. Then delete the downloaded copy. It never goes in
   the repository or `.env`. `--remove` takes it out again.
4. Restart the agent. In the app, **Settings → Lock-screen alerts**.

A phone's push address is forgotten whenever its sign-in ends: signing out
in the app, signing the phone out in the desktop app's **Signed-in devices**, removing a
helper, or the sign-in expiring. Up to 20 phones can have one.

**Lock-screen alerts in the iPhone app** go through Apple's push service
(APNs), sent by the agent itself, the same way: the message says only "New
alert" and the alert's number, and the app's notification extension reads
the alert from the PC over Tailscale and shows its words instead (when the
PC can't be reached, "New alert" is what shows). Your steps:

1. Join the [Apple Developer Program](https://developer.apple.com/programs/)
   (paid yearly). It's needed anyway to put the app on an iPhone.
2. In [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/identifiers/list),
   open the app ID `io.github.peyoteheadlights.commandblock` (make it if
   it isn't there) and tick **Push Notifications**.
3. **Keys → +**: give it a name like "Command Block alerts", tick **Apple
   Push Notifications service (APNs)**, then **Continue → Register →
   Download**. The file is called `AuthKey_<Key ID>.p8`. Apple lets you
   download it only once, so keep a copy somewhere safe (not in the
   repository). Your team ID is under **Membership details**.
4. On the PC, from the app's folder:
   `python -m installer.setup_phone_alerts --apple C:\path\to\AuthKey_ABC123DEFG.p8 --team ABCDE12345`.
   It checks the key and copies it into the data folder
   (`phone-alerts\apns-key.p8` and `apns.json`), readable only by this
   account and administrators. Add `--sandbox` while you test a build
   installed from Xcode; run it again without for TestFlight and the App
   Store. If you renamed the file, add `--key-id`. `--remove` takes both
   keys out.
5. In the app, **Settings → Lock-screen alerts**.

Apple's push service only speaks HTTP/2, so the agent uses httpx's `http2`
extra (h2, hpack and hyperframe: about 0.55 MB installed).

**Choose alerts for this device.** By default every phone and desktop app
gets the shared list above. In the phone apps' **Settings → Lock-screen
alerts** (the desktop app: **Your account**) a device can pick its own; the
choice is kept with that device's sign-in on the agent and applies to its
lock-screen alerts and its Alerts tab.

## Choosing events

PC settings, under **Alerts**, has a checkbox per event: server started/stopped/crashed/restarted/
recovered, player joined/left, high RAM/CPU/MSPT, low disk/TPS, backup
completed/failed, mod installed/removed/updated, dependency problems, failed
sign-ins, maintenance mode, a server added, removed or duplicated, a CPU core
limit that could not be applied, a player button the server confirmed
(whitelist, operator, kick, ban, unban; on by default), game settings saved
(off by default), a modpack imported (on by default) and a server put to
sleep because nobody was playing (on by default).

Every alert names its server ("Survival crashed"), so with several servers you
know which one without opening the app. CPU and RAM are measured for the
whole PC, so those alerts are sent once for the machine, not once per server.

Performance alerts are throttled by `notifications.min_interval_seconds`
(default 300), so a server at 95% RAM for an hour sends one message, not
hundreds. The throttle counts each server separately: a slow Creative does
not hold back an alert about Survival.

## When sending fails

A notification failure **never** affects the server. The failure is written to
the `notifications_log` table, shown in the notification history, and published
as a `notification_failed` event: the agent carries on regardless. A Discord
outage cannot take your Minecraft server down.
