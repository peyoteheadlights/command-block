"""Request bodies accepted by the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class OneTimeSignInRequest(BaseModel):
    code: str = Field(max_length=128)
    device: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    label: str = Field(default="", max_length=80)
    # "Keep me signed in on this device", and what to call the device on
    # the Security page (the phone app sends its name; else label is used).
    remember: bool = False
    device: str = Field(default="", max_length=80)
    # Only the phone apps (phone) and the desktop app (app) send these: their
    # kept sign-in lasts until signed out. Any other sign-in ends after
    # remember_days.
    phone: bool = False
    app: bool = False


class CommandRequest(BaseModel):
    command: str = Field(max_length=512)
    confirm: bool = False


class StopRequest(BaseModel):
    timeout: float | None = Field(default=None, ge=1, le=1800)
    force: bool = False


class BackupRequest(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    includes: list[str] | None = None
    note: str | None = Field(default=None, max_length=300)


class RestoreRequest(BaseModel):
    confirm: bool = False
    start_after: bool = False
    safety_backup: bool = True


class UndoRequest(BaseModel):
    start_after: bool = False
    # The change the person was shown; refused if it is no longer the newest.
    change_id: int | None = None


class ModInstallRequest(BaseModel):
    project: str = Field(max_length=100)
    version_id: str | None = Field(default=None, max_length=64)
    allow_replace: bool = False
    install_dependencies: bool = False


class ModFileRequest(BaseModel):
    filename: str = Field(max_length=180)


class ModUpdateRequest(ModFileRequest):
    version_id: str | None = Field(default=None, max_length=64)


class UpdateAllRequest(BaseModel):
    """The ticked mods or plugins, each with the version the plan showed."""

    mods: list[ModUpdateRequest] = Field(min_length=1, max_length=200)


class ModRollbackRequest(BaseModel):
    mod_id: str = Field(max_length=80)
    archive_path: str = Field(max_length=500)


class ScheduleRequest(BaseModel):
    name: str = Field(max_length=80)
    task: str = Field(max_length=32)
    kind: str = Field(max_length=16)
    expr: str = Field(max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class SettingsRequest(BaseModel):
    updates: dict[str, Any]


class MaintenanceRequest(BaseModel):
    enabled: bool


class DependencyInstallRequest(BaseModel):
    mod_ids: list[str] | None = Field(default=None, max_length=50)


class TpsCommandRequest(BaseModel):
    command: str = Field(max_length=100, description='"auto", "off", or a Minecraft command')


class ServerAddRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # The folder that already holds the Minecraft server. Checked by
    # agent.security.paths.check_server_folder before anything is saved.
    directory: str = Field(min_length=1, max_length=400)
    # The server jar inside that folder. Empty: fabric-server-launch.jar or
    # server.jar, whichever is there.
    jar: str = Field(default="", max_length=180)
    # What kind of server is in the folder. Empty: Fabric, as before
    # server types existed.
    type: str | None = Field(default=None, max_length=32)
    id: str | None = Field(default=None, max_length=64)
    # A palette id or "#RRGGBB". Empty: the next color no server uses.
    color: str | None = Field(default=None, max_length=16)


class ServerSettingsRequest(BaseModel):
    updates: dict[str, Any]


class ServerColorRequest(BaseModel):
    color: str = Field(max_length=16, description='A palette id such as "teal", or "#RRGGBB"')


class PreferencesRequest(BaseModel):
    mode: Literal["simple", "technical"] | None = None
    theme: Literal["system", "light", "dark", "graphite", "contrast"] | None = None


class RecommendationActionRequest(BaseModel):
    action: Literal["dismiss", "snooze", "restore"]


class VersionChangeRequest(BaseModel):
    # Empty: keep the type this server already is.
    type: str | None = Field(default=None, max_length=32)
    minecraft_version: str = Field(min_length=1, max_length=64)
    # The loader or build version, where the type picks one separately.
    loader_version: str | None = Field(default=None, max_length=64)
    confirm: bool = False
    start_after: bool = False


class CreateServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # The folder to create. Must be empty or not exist yet; checked by
    # agent.security.paths.check_new_server_folder.
    directory: str = Field(min_length=1, max_length=400)
    type: str = Field(max_length=32)
    minecraft_version: str = Field(min_length=1, max_length=64)
    loader_version: str | None = Field(default=None, max_length=64)
    memory_mb: int | None = Field(default=None, ge=512, le=65536)
    # A palette id or "#RRGGBB". Empty: the next color no server uses.
    color: str | None = Field(default=None, max_length=16)
    # True only when the person ticked the box themselves. The agent never
    # accepts Minecraft's rules on their behalf.
    eula_accepted: bool = False


class AddonToggleRequest(BaseModel):
    enabled: bool


class BedrockTermsRequest(BaseModel):
    # True only when the person ticked the box themselves.
    accepted: bool


class CrossplayRequest(BaseModel):
    enabled: bool
    # The UDP port Bedrock players use. Empty: the port manager picks a free one.
    port: int | None = Field(default=None, ge=1, le=65535)


class GameSettingsRequest(BaseModel):
    values: dict[str, Any] = Field(max_length=40)


class GameSettingsRawRequest(BaseModel):
    text: str = Field(max_length=256 * 1024)


class PlayerActionRequest(BaseModel):
    action: Literal["whitelist_add", "whitelist_remove", "op", "deop", "kick", "ban", "pardon"]
    name: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=100)
    confirm: bool = False


class DuplicateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    directory: str = Field(min_length=1, max_length=400)
    world: Literal["copy", "fresh"]


class ModpackNewRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    directory: str = Field(min_length=1, max_length=400)
    memory_mb: int | None = Field(default=None, ge=512, le=65536)
    color: str | None = Field(default=None, max_length=16)
    # True only when the person ticked the box themselves.
    eula_accepted: bool = False


class ModpackIntoRequest(BaseModel):
    token: str = Field(min_length=32, max_length=32)
    confirm: bool = False


class ChatRequest(BaseModel):
    message: str = Field(max_length=220)


class WorldUndoRequest(BaseModel):
    backup_id: int
    confirm: bool = False


class AlertsReadRequest(BaseModel):
    up_to: int = Field(ge=0)


class AppPhoneRequest(BaseModel):
    # The phone's address on Google's push service (FCM), or on Apple's
    # (APNs: hex, as it goes in the address the agent sends to).
    token: str = Field(min_length=20, max_length=4096, pattern=r"^[A-Za-z0-9_:\-]+$")
    platform: Literal["android", "ios"]
    label: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def _apple_address(self) -> AppPhoneRequest:
        if self.platform == "ios" and not all(c in "0123456789abcdefABCDEF" for c in self.token):
            raise ValueError("An iPhone's push address is hex.")
        return self


class DeviceAlertsRequest(BaseModel):
    own: bool
    events: list[str] = Field(default_factory=list, max_length=100)


class ShareKeyRequest(BaseModel):
    kind: Literal["oauth", "token"]
    secret: str = Field(min_length=10, max_length=300)
    client_id: str | None = Field(default=None, max_length=100)


class WakeSetupRequest(BaseModel):
    on: bool | None = None
    router_url: str | None = Field(default=None, max_length=300)


class WakeHelperRequest(BaseModel):
    address: str = Field(max_length=45)
    port: int = Field(ge=1024, le=65535)


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    # Left out: the moderator chooses it with the invite the answer carries.
    password: str | None = Field(default=None, min_length=1, max_length=256)
    # The server ids this moderator may use; None means every server, now
    # and later.
    servers: list[str] | None = None
    # What they may do on each of those servers; None is "Look after it".
    permissions: list[str] | None = None


class AccountUpdateRequest(BaseModel):
    # Every server, now and later (True), or only the servers given
    # permissions (False). Left out: as it is.
    all_servers: bool | None = None
    # The older form: limit the moderator to these servers, keeping what
    # they had on each.
    servers: list[str] | None = None
    # The permissions on each named server ("Save for this server", "Save
    # for…", "Apply to all"); an empty list hides that server.
    grants: dict[str, list[str]] | None = None
    # What an "All servers" moderator gets on the servers not named, and on
    # each new server.
    new_server: list[str] | None = None
    # A new password, which signs the helper out everywhere.
    password: str | None = Field(default=None, max_length=256)
    # PC-wide permissions ("Start the agent"); left out: as they are.
    pc: list[str] | None = None


class InviteRequest(BaseModel):
    # Also clear the password they have and sign them out ("Reset password").
    forget_password: bool = False


class InviteCheckRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class InviteAcceptRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    password: str = Field(max_length=256)
    # The phone's name, shown with the other signed-in devices.
    device: str = Field(default="", max_length=80)


class PasswordChangeRequest(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class WorldFolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=400)


class WorldImportRequest(BaseModel):
    token: str = Field(min_length=32, max_length=32)
    confirm: bool = False


class OffsiteRequest(BaseModel):
    # The folder for the second copies; empty turns them off.
    directory: str = Field(default="", max_length=400)


class MemoryRequest(BaseModel):
    memory_mb: int = Field(ge=512, le=1024 * 1024)


class ExportRequest(BaseModel):
    worlds: bool = False
    backups: bool = False
    # Passwords and keys only go in encrypted, with this passphrase.
    secrets: bool = False
    passphrase: str | None = Field(default=None, max_length=256)
    # A folder on the PC to save the file in (the phones' Export everything).
    folder: str | None = Field(default=None, max_length=1024)


class HelpBundleRequest(BaseModel):
    confirm: bool = False


class SupportAskAnswer(BaseModel):
    answer: Literal["donate", "no_thanks"]


class DiscordToken(BaseModel):
    # A bot token is three dot-separated parts of base64; never logged.
    token: str = Field(
        min_length=50,
        max_length=120,
        pattern=r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$",
    )


class DiscordProfile(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=32)
    # A PNG or JPEG as base64, or "logo" for the Command Block logo.
    avatar: str | None = Field(None, max_length=11_000_000)


class DiscordSetup(BaseModel):
    servers: list[str] = Field(min_length=1, max_length=50)


class DiscordTie(BaseModel):
    kind: Literal["alerts", "quests", "events", "chat", "status"]
    server_id: str = Field(default="", max_length=64)
    channel_id: str | None = Field(None, pattern=r"^\d{1,25}$")


class DiscordRoleGrants(BaseModel):
    role_name: str = Field(default="", max_length=100)
    grants: dict[str, list[str]] = Field(max_length=50)


class DiscordLevelRole(BaseModel):
    level: int = Field(ge=1, le=1000)
    role_id: str = Field(pattern=r"^\d{1,25}$")


class DiscordLevelSettings(BaseModel):
    announce: bool = True
    # 0 (Monday) to 6 (Sunday); null: no weekly leaderboard.
    leaderboard_day: int | None = Field(None, ge=0, le=6)
    # Left out: the Level roles stay as they are.
    roles: list[DiscordLevelRole] | None = Field(None, max_length=20)


class DiscordAddress(BaseModel):
    server_id: str = Field(max_length=64)
    # A name or address and maybe a port, as friends type it into Minecraft.
    address: str | None = Field(None, max_length=253, pattern=r"^[A-Za-z0-9.\-]+(:\d{1,5})?$")


class StartRequestAnswer(BaseModel):
    answer: Literal["start", "no"]


class DiscordLinkAnswer(BaseModel):
    answer: Literal["approve", "refuse"]
    uuid: str | None = Field(None, max_length=64)


class QuestReward(BaseModel):
    kind: Literal["role", "item", "xp", "level_xp"]
    role_id: str | None = Field(None, pattern=r"^\d{5,25}$")
    item: str | None = Field(None, max_length=100)
    count: int | None = Field(None, ge=1, le=64)
    levels: int | None = Field(None, ge=1, le=30)
    amount: int | None = Field(None, ge=1, le=500)


class NewQuest(BaseModel):
    title: str = Field(max_length=200)
    details: str = Field("", max_length=600)
    kind: Literal["stat", "advancement", "playtime", "joins", "items", "approval"]
    goal: str = Field("", max_length=100)
    count: int = Field(1, ge=1, le=100_000)
    repeat: Literal["once", "daily", "weekly", "monthly", "yearly"] = "once"
    ends: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    rewards: list[QuestReward] = Field(default_factory=list, max_length=4)
    # Made from a member's /quest suggest: that idea is answered and removed.
    suggestion_id: int | None = None


class QuestAnswer(BaseModel):
    answer: Literal["approve", "refuse"]
