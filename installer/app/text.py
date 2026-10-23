"""Every word the installer's window shows, in one place, in the same plain
style as the desktop app's Simple mode."""

from __future__ import annotations

TEXT = {
    "window": "Command Block setup",
    "app": "Command Block",
    "version": "Version {version}",
    "welcome": "Set up Command Block",
    "welcome_sub": "Run and watch your Minecraft servers from any of your devices.",
    "run_here": "Run servers on this PC",
    "run_here_sub": "This PC runs your Minecraft servers. You can reach them from your "
    "phone and other PCs.",
    "connect": "Connect to someone else's PC",
    "connect_sub": "Someone else runs the servers and gave you a link. Only the app is "
    "installed here.",
    "connect_options_sub": "Command Block is installed for your Windows user only.",
    "desktop_shortcut": "Desktop shortcut",
    "desktop_shortcut_sub": "Open Command Block from your desktop.",
    "open_at_sign_in": "Open when Windows starts",
    "open_at_sign_in_sub": "It waits in the notification area, so alerts reach you.",
    "install": "Install",
    "your_servers": "Your servers",
    "have_server": "I have a Minecraft server",
    "have_server_sub": "Pick the folder it's in.",
    "no_server": "I don't have one yet",
    "no_server_sub": "You'll make one in Command Block right after setup.",
    "import_pc": "Import from another PC",
    "import_pc_sub": "Use an export file made with Export everything.",
    "next": "Next",
    "back": "Back",
    "close": "Close",
    "update": "Update",
    "repair": "Repair",
    "where_server": "Where is your server?",
    "where_server_sub": "Pick the folder that has your server's files in it.",
    "browse": "Browse…",
    "add_another": "Add another server",
    "remove": "Remove",
    "server_name": "Name",
    "folder_placeholder": "C:\\Minecraft\\Survival",
    "color": "Color",
    "found_type": "Looks like a {type} server.",
    "password": "Choose your owner password",
    "password_sub": "You'll use it to sign in on your phone and other devices. "
    "At least 10 characters.",
    "password_again": "Type it again",
    "show": "Show",
    "hide": "Hide",
    "too_short": "Use at least 10 characters.",
    "no_match": "The two passwords don't match.",
    "java_title": "Minecraft needs Java {needed}",
    "java_missing": "Java wasn't found on this PC.",
    "java_old": "This PC has Java {found}, which is too old for new Minecraft versions "
    "(they need {needed}).",
    "java_install": "Install Java {major}",
    "java_install_sub": "Eclipse Temurin, from Adoptium. Checked before it's installed.",
    "java_skip": "I'll install it myself",
    "java_skip_sub": "Servers won't start until Java is installed.",
    "options": "A couple of choices",
    "start_with_pc": "Start the server panel when my PC turns on",
    "start_with_pc_sub": "Your servers can be managed even before anyone signs in to Windows.",
    "allow_devices": "Allow my phone and other devices to connect",
    "allow_devices_sub": "Only your own devices on Tailscale can reach it.",
    "advanced": "Advanced",
    "install_folder": "Install location",
    "older_here": "An older version is installed here. Do you want to replace it with the new "
    "version? Your settings, servers and backups are kept.",
    "yes_replace": "Yes, replace it",
    "no": "No",
    "import_title": "Import from another PC",
    "import_file": "Export file",
    "import_to": "Put the servers in",
    "import_pass": "Passphrase the file was saved with",
    "import_pass_sub": "Leave it empty to choose a new password instead.",
    "import_filter": "Export files (*.zip);;All files (*.*)",
    "working": "Setting things up",
    "working_sub": "This can take a few minutes. Your servers aren't changed.",
    "failed_title": "Setup stopped",
    "rolled_back": "Everything was put back: your previous version is running again.",
    "rolled_back_new": "Everything it had done was undone.",
    "not_rolled_back": "Putting the previous version back didn't fully work. "
    "Open the log for what was done.",
    "nothing_changed": "Nothing had been changed yet.",
    "try_again": "Try again",
    "show_details": "Show details",
    "hide_details": "Hide details",
    "open_log": "Open log",
    "copy_log": "Copy log",
    "copied": "Copied",
    "log_at": "The log is at {path}",
    "done_title": "All set",
    "done_sub": "The server panel is running.",
    "done_connect_sub": "Open Command Block to connect. It sets up Tailscale first, then "
    "takes the link you were given.",
    "open_app": "Open Command Block",
    "on_phone": "On your phone, scan this code with the camera, or type:",
    "no_qr": "The code for your phone isn't ready yet:",
    "players_one": "1 player is online. Update now or wait?",
    "players_many": "{count} players are online. Update now or wait?",
    "players_unknown": "A Minecraft server is running, and who is on it isn't known. "
    "Update now or wait?",
    "update_now": "Update now",
    "wait": "Wait",
    "found_sub": "Updating keeps your settings, servers and backups.",
    "java_for_servers": "Java for your servers",
    "java_use_have": "Use Java {major}, already on this PC, for:",
    "java_use_install": "Install Java {major} from Adoptium and use it for:",
    "java_server_now": "now Java {major}",
    "java_server_broken": "its Java doesn't run",
    "java_use_note": "Old Minecraft versions may need the Java they have, so only ticked "
    "servers change.",
    "pick_one": "Choose which one to update:",
    "remove_title": "Remove Command Block",
    "remove_sub": "This removes the app and its startup task. "
    "Your Minecraft server folders are never touched.",
    "remove_connect_sub": "This removes the app, its shortcuts and your sign-in, and signs "
    "this PC out on the PC you connect to. Tailscale stays installed.",
    "remove_data": "Also delete my settings, database and backups",
    "remove_button": "Remove",
    "removed": "Command Block was removed.",
    "kept_at": "Your settings and backups are kept in {path}",
    "not_admin": "Running servers on this PC needs Administrator rights. Select Try again, "
    "then Yes when Windows asks.",
    "still_working": "Setup is still working. It finishes first, then closes.",
}


def t(key: str, **values: object) -> str:
    return TEXT[key].format(**values) if values else TEXT[key]


def players(count: int) -> str:
    return t("players_one") if count == 1 else t("players_many", count=count)
