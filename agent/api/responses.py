"""What the API answers, described for clients (the desktop app and the phone
app). FastAPI publishes these in ``/api/openapi.json``.

Every model is *open*: it lists the fields a client may rely on, with their
types, and lets through any others the route adds. So a new field never
needs a new model first, and nothing a route returned before is dropped.
A listed field is a promise: ``tests/test_api_contract.py`` compares the
schema with the copy saved at the last release and fails when a route or a
listed field disappears without having been marked deprecated first.

Routes added since the installer came in must declare a response model
(``tests/test_api_contract.py`` checks that too). The routes the phone app
uses come first; the rest of the older routes are listed in
``tests/api_untyped_routes.txt`` and get models as they are touched.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------ shared pieces
class Job(Open):
    id: str
    kind: str
    title: str
    server_id: str | None = None
    state: str = Field(description="running, succeeded, failed or interrupted")
    step: str | None = None
    progress: float | None = Field(None, description="0 to 1; null when it can't be measured")
    message: str | None = None
    result: dict[str, Any] | None = None
    created_at: float | None = None
    finished_at: float | None = None


class GamePort(Open):
    port: int | None = None


# ------------------------------------------------------------ servers
class ServerRow(Open):
    id: str
    name: str
    color: str | None = None
    state: str
    state_verified: bool | None = None
    uptime: float | None = None
    players_online: int | None = Field(None, description="null until it has been measured")
    players_verified: bool | None = None
    max_players: int | None = None
    minecraft_version: str | None = None
    type: str | None = None
    type_name: str | None = None
    default: bool | None = None
    edition: str | None = Field(None, description="java or bedrock")
    capabilities: dict[str, bool] | None = Field(
        None, description="What this server's type can do; false means not applicable"
    )


class ServerList(Open):
    servers: list[ServerRow]
    default: str | None = None


class ServerStatus(Open):
    """One server's state, as the Overview shows it."""

    server_id: str | None = None
    name: str | None = None
    state: str
    state_verified: bool | None = None
    uptime: float | None = None
    minecraft_version: str | None = None
    server_type: str | None = None
    server_type_name: str | None = None
    players_online: int | None = None
    players_verified: bool | None = None
    players: list[Any] = []
    max_players: int | None = None
    metrics: dict[str, Any] | None = None
    job: Job | None = None


class ServerAction(Open):
    """Start, stop and restart. ``result`` says how sure the agent is:
    REQUESTED, IN_PROGRESS or VERIFIED."""

    result: str
    detail: str | None = None
    state: str
    startup_confirmed: bool | None = None


class CrashBlockCleared(Open):
    ok: bool
    auto_restart_blocked: bool


# ------------------------------------------------------------ console and chat
class ConsoleLine(Open):
    seq: int = Field(description="Counts up per line; starts at 1 again when the agent restarts")
    ts: float
    raw: str = Field(description="The line exactly as the server printed it")
    level: str = Field(description="INFO, WARN, ERROR, DEBUG, FATAL or TRACE")
    thread: str = Field(description="The thread the line names; empty when it names none")
    message: str
    source: str = Field(description="stdout, agent, or command for a command sent from here")


class ConsoleLog(Open):
    lines: list[ConsoleLine]
    buffered: int = Field(description="How many lines the agent holds now")
    buffer_limit: int = Field(description="The most lines it keeps")


class CommandCheck(Open):
    valid: bool = Field(description="Whether the command may be sent at all")
    error: str | None = Field(None, description="Why it can't be sent; null when it can")
    danger_reason: str | None = Field(
        None, description="Why it needs confirming first; null when it doesn't"
    )


class CommandSent(Open):
    result: str = Field(
        description="SENT: written to the console, not yet answered. BLOCKED: ban-ip went "
        "to Join protection instead"
    )
    detail: str
    command: str
    name: str = Field(description="The command's first word")
    dangerous: bool
    danger_reason: str | None = None


class ChatMessage(Open):
    seq: int = Field(description="Counts up per message; separate from the console's")
    ts: float
    kind: str = Field(description="player, server or action")
    name: str | None = Field(None, description="Who said it; null for the server")
    text: str


class ChatLog(Open):
    messages: list[ChatMessage]
    running: bool = Field(description="Whether a message can be sent now")
    kept: int = Field(description="How many messages the agent holds")


class ChatSent(Open):
    result: str = Field(description="SENT: it appears in the chat once the server prints it")
    detail: str
    message: str = Field(description="The text that was sent, trimmed")


# ------------------------------------------------------------ players, backups, alerts, jobs
class PlayerAction(Open):
    """A whitelist, op, kick, ban or unban sent to the server, and whether
    its console has confirmed it."""

    id: str
    kind: str = Field(description="whitelist_add, whitelist_remove, op, deop, kick, ban or pardon")
    name: str
    command: str
    sent_at: float
    state: str = Field(description="sent, done, unchanged, failed or no_answer")
    message: str | None = Field(None, description="The console line that answered")
    answered_at: float | None = None


class PlayerActionResult(Open):
    result: str = Field(description="SENT; the action's state says when it is confirmed")
    action: PlayerAction


class PlayerActionStatus(Open):
    action: PlayerAction


class OnlinePlayer(Open):
    username: str
    uuid: str | None = None
    session_started: float | None = None
    total_seconds: float = Field(description="Time played before this session")
    sessions: int
    first_seen: float | None = None
    last_seen: float | None = None
    edition: str | None = Field(None, description="java or bedrock; null when it can't be told")
    session_seconds: float = Field(description="0 when the session's start isn't known")


class KnownPlayer(OnlinePlayer):
    server_id: str
    online: int = Field(description="1 while the agent counts them as online")
    total_seconds_live: float = Field(description="Total time played, this session included")


class PlayerList(Open):
    players: list[dict[str, Any]] | None = Field(
        None, description="null when the file isn't there or can't be read; reason says why"
    )
    file: str | None = None
    reason: str | None = None
    not_applicable: bool | None = Field(None, description="true for a list this edition lacks")


class PlayerLists(Open):
    whitelist: PlayerList
    ops: PlayerList
    banned: PlayerList


class Players(Open):
    online: list[OnlinePlayer]
    online_count: int | None = Field(None, description="null until the list has been read")
    verified: bool | None = None
    source: str | None = Field(None, description="How the online list was learned")
    known: list[KnownPlayer] = Field(description="Everyone seen, last seen first")
    max_players: int | None = None
    lists: PlayerLists = Field(description="From Minecraft's own files")
    bans: bool = Field(description="false where the server has no ban list")
    edition: str = Field(description="java or bedrock")
    running: bool | None = None
    actions: list[PlayerAction] = Field(description="The latest sent, newest first")


# ------------------------------------------------------------ history
class EventRow(Open):
    id: int
    server_id: str = Field(description="The server, or _agent for the agent's own events")
    ts: float
    type: str
    level: str = Field(description="info, success, warn or error")
    message: str
    data: str | None = Field(None, description="JSON text, not an object; null when none")


class EventList(Open):
    events: list[EventRow] = Field(description="Newest first")


class Crash(Open):
    id: int
    server_id: str
    ts: float
    exit_code: int | None = Field(None, description="null when it isn't known")
    category: str | None = None
    confidence: str | None = Field(None, description="confirmed, likely, possible or unknown")
    summary: str | None = None
    evidence: list[str] = Field(description="The log lines the cause was read from")
    report_path: str | None = None
    log_path: str | None = None
    context: dict[str, Any] = Field(description="What was known when it crashed")
    fix: dict[str, Any] | None = Field(
        None, description="The fix, only for a confirmed or likely cause (analyzer.fix_for)"
    )
    restarted: int = Field(description="1 once it was started again after the crash")


class CrashList(Open):
    crashes: list[Crash] = Field(description="Newest first")


class CrashDetail(Crash):
    log_tail: list[str] | None = Field(
        None, description="The saved log's last lines; null when the file is gone"
    )


# ------------------------------------------------------------ the Overview's cards
class ChecklistItem(Open):
    id: str
    done: bool = Field(description="Ticked from what was measured, never from a click")
    page: str = Field(description="The page in the apps that does it")
    evidence_key: str
    evidence: dict[str, Any]


class Checklist(Open):
    items: list[ChecklistItem]
    done: int
    total: int
    show: bool
    dismissed: bool
    finished: bool


class JoinAddress(Open):
    address: str
    adapter: str
    virtual: bool = Field(description="A virtual adapter, like a VPN or a virtual machine's")


class TailscaleAddress(Open):
    address: str | None = None
    dns_name: str | None = None
    connected: bool | None = Field(None, description="null when it couldn't be read")
    verified: bool
    source: str | None = None
    detail: str | None = None


class JavaJoin(Open):
    port: int
    port_source: str = Field(description="Where the port number was read, in words")
    default_port: bool
    local: list[JoinAddress]
    tailscale: TailscaleAddress


class BedrockJoin(Open):
    port: int | None = Field(None, description="null when it isn't set up yet")
    port_source: str | None = None
    protocol: str
    default_port: bool | None = None
    ready: bool | None = None
    address: str | None = Field(None, description="The Tailscale address; null when unknown")
    local: list[JoinAddress]
    tailscale: TailscaleAddress | None = None
    consoles_note: bool | None = None
    own_server: bool | None = Field(None, description="true for a Bedrock server of its own")


class InternetReach(Open):
    known: bool = Field(description="Always false: reaching it from the internet isn't tested")
    reason: str | None = None


class JoinInfo(Open):
    java: JavaJoin | None = Field(None, description="null for a Bedrock server")
    bedrock: BedrockJoin | None = Field(None, description="null when Bedrock players can't join")
    internet: InternetReach
    running: bool


class BackupCopy(Open):
    """A backup's second copy off the PC, as found on the drive just now."""

    state: str = Field(description="ok, failed, unreachable, missing or none")
    reason: str | None = None
    path: str | None = None
    checked_at: float | None = None


class Backup(Open):
    id: int
    name: str
    kind: str | None = Field(None, description="manual, scheduled or safety")
    created_at: float | None = None
    size_bytes: int | None = None
    status: str | None = Field(None, description="ok (checked), unverified or failed")
    note: str | None = None
    includes: str | None = Field(None, description="Its top-level folders, comma-separated")
    exists: bool | None = Field(None, description="Whether its file is still on disk")
    copy_: BackupCopy | None = Field(None, alias="copy")


class Retention(Open):
    daily: int
    weekly: int
    monthly: int


class BackupList(Open):
    backups: list[Backup] = Field(description="Newest first")
    directory: str | None = None
    retention: Retention | None = None


class BackupMade(Open):
    ok: bool
    id: int | None = None
    name: str
    size_bytes: int | None = None
    verified: bool | None = None
    verification: dict[str, Any] | None = None
    job_id: str | None = None


class BackupCheck(Open):
    """Opening the archive and checking it now. ``reason`` says why it failed."""

    ok: bool
    reason: str | None = None
    entries: int | None = None
    size_bytes: int | None = None
    checks: list[str] = []


class SafetyBackup(Open):
    id: int | None = None
    name: str


class Undo(Open):
    kind: str
    backup_id: int
    backup: str


class Restored(Open):
    """A restore asked without ``confirm`` answers ``confirmation_required``
    and ``will_happen``; a confirmed one answers what was put back."""

    ok: bool | None = None
    confirmation_required: bool | None = None
    will_happen: list[str] | None = None
    backup: dict[str, Any] | None = None
    restored: str | None = None
    safety_backup: SafetyBackup | None = Field(
        None, description="The backup made just before; null when none was"
    )
    undo: Undo | None = None
    verified: bool | None = None
    job_id: str | None = None


class BackupDeleted(Open):
    ok: bool
    deleted: str


class Offsite(Open):
    directory: str = Field(description="Where second copies go; empty when they're off")
    unavailable: str | None = Field(None, description="Why that folder can't be reached now")
    subfolder: str | None = None


class OffsiteSaved(Open):
    ok: bool
    directory: str


class BackupCopied(Open):
    ok: bool
    copy_: BackupCopy = Field(alias="copy")


class World(Open):
    key: str
    label: str
    exists: bool
    size_bytes: int | None = None
    modified: float | None = None
    last_backup: dict[str, Any] | None = Field(None, description="name and created_at")


class WorldList(Open):
    worlds: list[World]


class Requested(Open):
    """Written to the server console; Minecraft gives no answer to check."""

    result: str = Field(description="REQUESTED")
    detail: str


class WorldPoint(Open):
    backup_id: int
    name: str
    created_at: float
    age_seconds: float
    size_bytes: int | None = None
    kind: str | None = None
    note: str | None = None
    checked: bool = Field(description="Only a checked backup is offered")
    status: str | None = None
    played_seconds: float | None = Field(
        None, description="Time with players online since; null when sessions weren't recorded"
    )


class WorldTimeline(Open):
    points: list[WorldPoint] = Field(description="Newest first")
    server_running: bool
    can_restore: bool
    blocked_by: str | None = Field(None, description="The task holding the server, if any")
    sessions_recorded: bool


# ------------------------------------------------------------ schedules
class Schedule(Open):
    id: int
    name: str
    task: str
    kind: str = Field(description="daily, weekly or interval")
    expr: str
    payload: dict[str, Any] = {}
    enabled: bool
    last_run: float | None = None
    next_run: float | None = None
    last_result: str | None = None


class ScheduleList(Open):
    schedules: list[Schedule]
    tasks: list[str] = Field(description="What a schedule can run")


class ScheduleSaved(Open):
    ok: bool
    schedule: Schedule


class ScheduleRan(Open):
    ok: bool
    result: str = Field(description="What the task said, in its own words")


# ------------------------------------------------------------ performance
class Snapshot(Open):
    """The values now. Each is null when it couldn't be measured."""

    ts: float
    cpu_percent: float | None = None
    process_cpu_percent: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    ram_percent: float | None = None
    proc_ram_mb: float | None = Field(None, description="null while the server isn't running")
    disk_free_gb: float | None = None
    disk_unknown_reason: str | None = None
    net_sent_mb_s: float | None = None
    net_recv_mb_s: float | None = None
    tps: float | None = None
    mspt: float | None = None


class SeriesPoint(Open):
    """One time bucket's averages. A value nobody measured is null: a gap."""

    ts: float
    cpu_percent: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    proc_ram_mb: float | None = None
    tps: float | None = None
    mspt: float | None = None
    players: float | None = None
    players_max: float | None = None


class Series(Open):
    points: list[SeriesPoint] = Field(description="Oldest first; empty buckets left out")
    hours: float
    bucket_seconds: float


class Storage(Open):
    worlds_gb: float
    backups_gb: float
    mods_gb: float
    logs_gb: float
    other_gb: float
    free_gb: float | None = None
    free_unknown_reason: str | None = None


class Performance(Open):
    current: Snapshot
    series: Series
    sample_interval: float
    memory_limit_mb: int | None = Field(None, description="The -Xmx setting; null when none")
    storage: Storage | None = Field(None, description="null when asked with storage=false")


class HealthCheck(Open):
    name: str
    status: str = Field(description="ok, warn or unknown (never counted as passing)")
    value: Any = None
    threshold: Any = None
    detail: str | None = None
    source: str | None = None


class ServerHealth(Open):
    overall: str = Field(description="ALL CHECKS VERIFIED, PARTIALLY VERIFIED or ATTENTION NEEDED")
    note: str = ""
    checks: list[HealthCheck]
    generated_at: float


class AlertRow(Open):
    id: int
    ts: float
    channel: str
    event: str
    status: str
    detail: str | None = None


class AlertHistory(Open):
    history: list[AlertRow]


class JobList(Open):
    jobs: list[Job]


class Change(Open):
    id: int
    title: str
    user: str | None = None
    made_at: float
    undone_at: float | None = Field(None, description="Set on the redo stack")
    scope: list[str] | None = Field(
        None, description="What Undo or Redo puts back; null for everything the backup holds"
    )
    backup_id: int
    backup: str
    backup_at: float = Field(description="When the backup that would be put back was made")
    played_seconds: float | None = Field(
        None,
        description="How long people played since that backup; null when no sessions were recorded",
    )


class Changes(Open):
    undo: list[Change] = Field(description="Newest first: Undo reverses the first one")
    redo: list[Change] = Field(description="Undone last first: Redo puts back the first one")
    server_running: bool
    blocked_by: str | None = None


class ChangeMoved(Open):
    ok: bool
    change: Change
    restored: str
    safety_backup: SafetyBackup | None = None
    job_id: str | None = None


class JobUndone(Open):
    ok: bool
    restored: str
    safety_backup: SafetyBackup | None = None
    undo: Undo | None = None
    job_id: str | None = None


class Recommendations(Open):
    recommendations: list[dict[str, Any]]
    hidden: list[dict[str, Any]] = []


# ------------------------------------------------------------ the agent itself
class LoginResult(Open):
    token: str = Field(description="Send as 'Authorization: Bearer <token>'")
    user: str
    expires_at: float | None = Field(
        description="null for a phone's sign-in: it lasts until signed out"
    )
    role: str = Field(description="owner or helper")
    remember: bool


class Preferences(Open):
    mode: str = Field(description="simple or technical")
    theme: str = Field(description="system, light, dark, graphite or contrast")


class Me(Open):
    user: str
    kind: str
    expires_at: float | None = None
    role: str = Field(description="owner or helper (a moderator)")
    servers: list[str] | None = Field(
        None, description="A moderator's servers; null for every server"
    )
    permissions: list[str] = Field(
        description="Every permission held on at least one server, plus the account's own; "
        "the list apps released before per-server permissions read"
    )
    account_permissions: list[str] = Field(
        description="Permissions that don't belong to one server"
    )
    server_permissions: dict[str, list[str]] = Field(
        description="What this account may do on each server it can see, by server id"
    )
    preferences: dict[str, Any]


class Health(Open):
    ok: bool
    auth_configured: bool | None = None
    api_version: int = Field(description="The API version; see /api/version and CHANGELOG.md")


class VersionInfo(Open):
    version: str = Field(description="The app's version, like 1.2.0")
    api_version: int = Field(description="Goes up only for a change a client must know about")


class PairingCode(Open):
    ok: bool
    address: str | None = None
    port: int | None = None
    fingerprint: str | None = Field(None, description="SHA-256 of the certificate, 64 hex digits")
    api_version: int | None = None
    url: str | None = Field(None, description="What the code holds; no password or token")
    qr: list[str] | None = Field(None, description="QR rows, 1 = dark square, no border")
    reason: str | None = None


class AppAlert(Open):
    id: int
    ts: float
    server_id: str | None = None
    event: str
    title: str
    body: str
    page: str = Field(description="The page in the apps the alert opens")
    url: str = Field(description="Its link, like /#survival/crashes")


class AppAlerts(Open):
    alerts: list[AppAlert]
    latest: int = Field(description="Ask with after=latest next time")
    more: bool = Field(False, description="More alerts are waiting after latest")
    enabled: bool = Field(description="Always false: browser alerts were removed")
    read_up_to: int = Field(0, description="This account has read every alert up to this id")


class AlertsRead(Open):
    read_up_to: int


class AppPhone(Open):
    configured: bool = Field(description="Whether this PC has a Firebase key to send with")
    registered: bool = Field(description="Whether this sign-in has a push address")
    phone: dict[str, Any] | None = None


class DeviceAlerts(Open):
    own: bool = Field(description="Whether this device chose its own alerts (else the shared list)")
    events: dict[str, bool] = Field(description="Each alert, and whether this device gets it")


class WakeDevice(Open):
    on: bool = Field(description="Whether Wake-on-LAN is set up, with the PC's MAC recorded")
    pc: str = Field(description="The PC's name, for 'Wake <PC>'")
    mac: str | None = None
    broadcast: str | None = Field(None, description="The home network's broadcast address")
    helper: dict[str, Any] | None = Field(None, description="The helping PC: address and port")
    router_url: str | None = Field(None, description="The router's wake page (https)")
    has_key: bool = Field(description="Whether this sign-in has a wake key")


class WakeKey(Open):
    key: str = Field(description="This device's wake key, shown once")


class WakeKeys(Open):
    mac: str | None = None
    broadcast: str | None = None
    keys: list[str] = Field(description="SHA-256 of each valid wake key")


class WakeSetup(Open):
    on: bool
    now: dict[str, Any] | None = Field(
        None, description="The adapter with the home address now; wired null: not known"
    )
    mac: str | None = None
    trying_since: float | None = None
    tested_at: float | None = Field(None, description="When a wake after 'Try it' was noticed")
    helper: dict[str, Any] | None = None
    router_url: str | None = None


class ShareKey(Open):
    saved: bool
    kind: str | None = Field(None, description="oauth or token")
    expires_at: float | None = Field(None, description="When an API token runs out, if known")
    expiring: bool = Field(False, description="Runs out within a week")
    revoke_page: str | None = Field(None, description="After Remove key: where to revoke it")


class ShareLink(Open):
    url: str = Field(description="A one-time Tailscale share link for this PC")


class Release(Open):
    version: str
    page: str | None = None
    notes: str | None = None
    published_at: str | None = None


class UpdateResult(Open):
    ok: bool | None = None
    version: str | None = None
    message: str | None = None
    rolled_back: bool | None = None


class UpdateStatus(Open):
    current: str
    latest: Release | None = None
    available: bool
    checked_at: float | None = None
    error: str | None = None
    checking: bool
    install_kind: str = Field(description="installer, or project for a setup.ps1 copy")
    can_install: bool
    cannot_install_reason: str | None = None
    last_result: UpdateResult | None = None


class UpdatePreflight(UpdateStatus):
    players_online: int | None = Field(None, description="null when it couldn't be measured")
    players: list[dict[str, Any]] = []


class UpdateStarted(Open):
    ok: bool
    job: Job


class OldCopy(Open):
    path: str
    version: str | None = None
    moved_at: str | None = None
    offer: bool
    problems: list[str] = []
    removes: list[str] = []


class OldCopyStatus(Open):
    old_copy: OldCopy | None = None


class OldCopyRemoveResult(Open):
    ok: bool
    path: str
    removed: list[str]
    kept: list[str]
    failed: list[str]
    folder_removed: bool


class Device(Open):
    id: str = Field(description="Use it to sign this device out")
    user: str
    label: str | None = None
    created_at: float | None = None
    last_used: float | None = None
    expires_at: float | None = None
    source_ip: str | None = None
    remember: bool = False
    current: bool = False


class DeviceList(Open):
    sessions: list[Device]
    remember_days: float | None = None


class Ok(Open):
    ok: bool


class TpsStatus(Open):
    """How the server's speed (TPS) is read, for "Speed readings"."""

    mode: str = Field(description="auto, manual, disabled or unsupported")
    state: str = Field(description="idle, detecting, active, unavailable or disabled")
    command: str | None = None
    detection: str | None = None
    message: str | None = None
    tried: list[dict[str, Any]] = []
    last_reading_at: float | None = None
    server_state: str


# ------------------------------------------------------------ moderators
class Described(Open):
    permissions: list[str]
    level: str = Field(description="start_and_stop, look_after_it, run_it, custom or none")


class ServerAccess(Described):
    id: str
    name: str


class OpenInvite(Open):
    created_at: float
    expires_at: float


class Moderator(Open):
    username: str
    role: str
    servers: list[str] | None = Field(None, description="null: every server, now and later")
    all_servers: bool
    new_server: Described = Field(description="What a new server gives an all-servers moderator")
    access: list[ServerAccess] = Field(description="What they may do on each server")
    pc: list[str] = Field(default_factory=list, description="PC-wide permissions")
    invite_waiting: bool = Field(description="True until they choose a password with an invite")
    invite: OpenInvite | None = None
    created_at: float
    active_sessions: int
    last_used: float | None = None
    last_sign_in: float | None = None


class PermissionLevel(Open):
    id: str
    permissions: list[str]


class PermissionGroup(Open):
    id: str
    permissions: list[str]


class PermissionCatalogue(Open):
    levels: list[PermissionLevel] = Field(description="Smallest first")
    default_level: str
    groups: list[PermissionGroup] = Field(description="The switches, as the apps group them")
    technical: list[str] = Field(description="Switches shown only in Technical mode")
    warn: list[str] = Field(description="Switches that let someone do anything in the game")
    needs: dict[str, list[str]] = Field(description="A switch brings these along")
    pc: list[str] = Field(default_factory=list, description="Switches for the whole PC")


class ServerName(Open):
    id: str
    name: str


class ModeratorList(Open):
    owner: str
    helpers: list[Moderator]
    permissions: PermissionCatalogue
    servers: list[ServerName]


class InviteCode(Open):
    ok: bool = Field(description="False when the PC has no pairing code; reason says why")
    username: str
    expires_at: float
    url: str | None = Field(None, description="The pairing link with the one-time invite")
    qr: list[str] | None = Field(None, description="QR rows, 1 = dark square, no border")
    reason: str | None = None


class ModeratorAdded(Open):
    ok: bool
    username: str
    invite: InviteCode | None = Field(None, description="Present when no password was given")


class ModeratorChanged(Open):
    ok: bool
    username: str


class ModeratorRemoved(Open):
    ok: bool
    removed: str


class InviteCheck(Open):
    username: str
    expires_at: float


# ------------------------------------------------------------ Bedrock
class BedrockTerms(Open):
    """Whether Mojang's EULA and Privacy Policy were accepted (needed before
    the Bedrock server is downloaded), by whom and when."""

    accepted: bool
    user: str | None = None
    at: float | None = None
    eula_url: str
    privacy_url: str


class KeptBedrockVersion(Open):
    version: str
    sha256: str | None = None
    size: int | None = None
    downloaded_at: float | None = None


class BedrockVersions(Open):
    """The Bedrock versions this app kept. Mojang only offers the newest, so
    older ones can be installed only from these."""

    kept: list[KeptBedrockVersion]
    folder: str


class Addon(Open):
    uuid: str
    version: str
    name: str
    description: str | None = None
    kind: str = Field(description="behavior or resource")
    folder: str
    enabled: bool


class AddonList(Open):
    addons: list[Addon]
    world: str | None = Field(None, description="The world the packs are enabled in")
    running: bool
    restart_needed: bool | None = None


class AddonResult(Open):
    ok: bool
    installed: list[Addon] = []
    restart_needed: bool | None = None


# ------------------------------------------------------------ mods and plugins
class ModIssue(Open):
    kind: str
    severity: str = Field(description="error, warn or info")
    detail: str


class ModUpdate(Open):
    """A newer version found on Modrinth at the last check."""

    mod_id: str
    latest_version: str
    version_id: str
    installed_version: str | None = None


class ModUpdates(Open):
    updates: list[dict[str, Any]] = Field(description="Newer versions found, as ModUpdate")
    checked_at: float | None = Field(None, description="null when never checked")


class Mod(Open):
    filename: str
    mod_id: str
    name: str
    version: str
    enabled: bool
    description: str | None = None
    size_bytes: int | None = None
    minecraft_range: str | None = None
    loader: str | None = None
    environment: str | None = None
    status: str | None = Field(None, description="ok, warn, error or disabled")
    issues: list[ModIssue] = []
    has_history: bool | None = None
    update_available: ModUpdate | None = None
    source: str | None = Field(
        None,
        description="modrinth, hangar, curseforge, github or file; null for a jar the app "
        "didn't add",
    )
    source_name: str | None = None
    checked_by: str | None = Field(
        None,
        description="The site that lists this file (Modrinth, Hangar, CurseForge); null: nobody",
    )
    checked_with: str | None = Field(
        None, description="sha1 for CurseForge, a weaker check than the other sites use"
    )


class ModProblem(ModIssue):
    mod: str | None = None
    filename: str | None = None


class NeedsYou(Open):
    """A CurseForge mod its author keeps from other apps: the owner opens
    its page, downloads it and drops the file in."""

    project: str
    version_id: str | None = None
    name: str | None = None
    filename: str | None = None
    page: str | None = Field(None, description="Its CurseForge page; null when not known now")


class ModList(Open):
    mods: list[Mod]
    needs_you: list[NeedsYou] = []
    problems: list[ModProblem] = []
    minecraft_version: str | None = Field(
        None, description="null until the server has started once"
    )
    server_state: str
    updates_checked_at: float | None = None


class ModChanged(Open):
    """Turning a mod or plugin on or off."""

    ok: bool
    mod: Mod
    changed: bool | None = None


class ModDependent(Open):
    name: str
    mod_id: str
    filename: str
    requires: str | None = None


class ModImpact(Open):
    """What else needs a mod, before it is removed."""

    dependents: list[ModDependent]
    warning: str | None = None


class ModRemoved(Open):
    ok: bool
    removed: dict[str, Any]
    trash: str | None = Field(None, description="Where the file was kept")


class ModUpdated(Open):
    ok: bool
    updated: dict[str, Any]
    previous_version: str | None = None


class ModVersion(Open):
    """A copy of a mod kept on this server, which it can roll back to."""

    version: str | None = None
    filename: str
    archive_path: str
    created_at: float
    is_current: bool
    exists: bool = Field(description="False when the kept file has gone")


class ModVersions(Open):
    versions: list[ModVersion]


class ModRolledBack(Open):
    ok: bool
    restored: dict[str, Any]
    previous_version: str | None = None


class ModHistoryRow(Open):
    ts: float
    user: str | None = None
    action: str
    mod_id: str | None = None
    mod_name: str | None = None
    old_version: str | None = None
    new_version: str | None = None
    result: str = Field(description="ok or failed")
    detail: str | None = None


class ModHistory(Open):
    history: list[ModHistoryRow] = Field(description="Newest first")


class ModrinthHit(Open):
    project_id: str | None = None
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    downloads: int | None = None
    icon_url: str | None = Field(None, description="Loaded by the client straight from Modrinth")
    page: str | None = None
    server_side: str | None = None
    client_side: str | None = None
    source: str | None = Field(None, description="modrinth, hangar or curseforge")
    source_name: str | None = None
    blocked: bool | None = Field(
        None, description="CurseForge: its author doesn't let other apps download it"
    )
    look_alike: str | None = Field(
        None, description="A far more downloaded result whose name is nearly the same"
    )


class ModSearch(Open):
    hits: list[ModrinthHit]
    total: int | None = None
    problems: list[str] = Field([], description='"Hangar isn\'t available right now."')


class ModrinthFile(Open):
    filename: str | None = None
    size: int | None = None


class ModrinthDependency(Open):
    project_id: str | None = None
    type: str | None = Field(None, description="required, optional, incompatible or embedded")


class ModrinthVersion(Open):
    version_id: str | None = None
    version_number: str | None = None
    release_type: str | None = None
    game_versions: list[str] = []
    dependencies: list[ModrinthDependency] = []
    file: ModrinthFile | None = None


class ModrinthProject(Open):
    project_id: str | None = None
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    license: str | None = None
    server_side: str | None = None
    client_side: str | None = None
    icon_url: str | None = None
    page: str | None = None


class ModProject(Open):
    """A Modrinth project and its versions that fit this server."""

    project: ModrinthProject
    versions: list[ModrinthVersion]
    installed: dict[str, Any] | None = None


class DependencyNeeded(Open):
    mod_id: str
    title: str | None = None
    installed: bool = False


class DependencyLink(Open):
    name: str | None = None
    url: str
    required: bool


class DependencyOptional(Open):
    name: str | None = None
    project: str


class ModInstalled(Open):
    ok: bool
    installed: dict[str, Any]
    dependencies_required: list[DependencyNeeded] = []
    dependencies_installed: list[dict[str, Any]] = []
    dependencies_links: list[DependencyLink] = Field(
        [], description="Needed plugins that aren't on Hangar: a link only, never downloaded"
    )
    dependencies_optional: list[DependencyOptional] = Field(
        [], description="Plugins it works with, listed but not added"
    )
    needs_you: list[NeedsYou] = Field([], description="Needed mods now under Needs you")
    checked_by: str | None = None
    checked_with: str | None = Field(None, description='"sha1" for CurseForge')


class DependencyItem(Open):
    mod_id: str
    name: str
    kind: str = Field(description="required or optional")
    platform: bool = Field(description="Minecraft, the loader or Java rather than a mod")
    status: str | None = None
    range_text: str | None = None
    reason: str | None = None
    modrinth: dict[str, Any] | None = Field(None, description="Its Modrinth page, when found")


class DependencyReport(Open):
    items: list[DependencyItem]
    missing_required: list[str] = []
    lookup_error: str | None = None


class PlannedDependency(Open):
    mod_id: str
    title: str | None = None
    version_number: str | None = None
    size: int | None = None
    page: str | None = None


class UnresolvableDependency(Open):
    mod_id: str
    name: str | None = None
    reason: str
    page: str | None = Field(None, description="A page to get it by hand, when known")


class DependencyPlan(Open):
    """What installing the missing ones would download. Nothing is yet."""

    items: list[PlannedDependency]
    unresolvable: list[UnresolvableDependency] = []


class DependencyResult(Open):
    title: str
    result: str = Field(description="installed, skipped or failed")
    detail: str | None = None
    version: str | None = None


class DependencyInstall(Open):
    results: list[DependencyResult]
    still_missing: list[dict[str, Any]] = []


class ModUploaded(Open):
    ok: bool
    installed: dict[str, Any]
    source: str | None = Field(
        None,
        description="modrinth, hangar or curseforge when a site lists it, else file or github",
    )
    checked_by: str | None = Field(None, description="null: nobody checked it")
    checked_with: str | None = Field(None, description='"sha1" for CurseForge')
    undo: Undo | None = None


class ModLinkFile(Open):
    version_id: str
    filename: str
    size: int | None = None
    checksum: bool = Field(description="GitHub lists its SHA-256, so the download is checked")


class ModLink(Open):
    """A pasted link: a GitHub release's files, or the browser-and-drop answer."""

    kind: str = Field(description="github or browser")
    message: str | None = None
    repo: str | None = None
    tag: str | None = None
    name: str | None = None
    prerelease: bool | None = None
    page: str | None = None
    files: list[ModLinkFile] = []


class UpdateAllRow(Open):
    mod_id: str
    name: str
    filename: str
    enabled: bool = Field(description="Turned-off ones are listed but not ticked first")
    installed_version: str
    latest_version: str
    version_id: str
    source: str | None = Field(None, description="The site the update comes from")
    saved: str | None = Field(
        None, description="How old the site's list was, when only its saved copy could be read"
    )


class UpdateAllUnchecked(Open):
    mod_id: str
    name: str
    filename: str
    reason: str


class UpdateAllPlan(Open):
    """What Update all would change, checked on Modrinth just now."""

    updates: list[UpdateAllRow]
    unchecked: list[UpdateAllUnchecked] = Field(
        description="Ones Modrinth couldn't be asked about, with the reason"
    )
    server_running: bool
    blocked_by: str | None = Field(None, description="The task holding the server, if any")
    checked_at: float | None = Field(None, description="When Modrinth last answered; null: never")


class UpdateAllDone(Open):
    mod_id: str
    name: str
    filename: str
    previous_version: str
    version: str


class UpdateAllResult(Open):
    ok: bool
    job_id: str
    updated: list[UpdateAllDone]
    safety_backup: SafetyBackup | None = Field(None, description="The one backup made first")
    undo: Undo | None = None


# ------------------------------------------------------------ suggestions
class Downloads(Open):
    count: int
    source: str = Field(description="The site the count comes from")
    read_at: float = Field(description="When it was read (a saved copy keeps its own date)")


class Suggestion(Open):
    project: str = Field(description="The id to send to /mods/install")
    name: str
    description: str | None = None
    kind: str = Field(description="performance, good_to_have or often_used_with")
    group: str = Field(description="Plugin servers: starter, protect, bedrock, maps, voice; or ''")
    changes_gameplay: bool
    technical_only: bool = Field(description="Alpha or beta build, or marked so: Technical only")
    release_type: str | None = None
    version: str | None = Field(None, description="The build that fits this server")
    place: str = Field(description="server, both (players need it too) or players")
    page: str | None = None
    downloads: Downloads | None = Field(None, description="null: the site gave no count")
    with_: list[str] = Field([], alias="with", description="often_used_with: what triggers it")


class SpeedUp(Open):
    """A performance suggestion: an add-on (as Suggestion), Paper's own
    settings ("settings", with "values" to save) or "change_type"."""

    kind: str
    name: str


class ForPlayers(Open):
    name: str
    page: str | None = None
    also_on_server: bool = Field(
        description="Installed here, and players need it in their game too"
    )


class MapLink(Open):
    id: str
    name: str
    port: int | None = None
    source: str = Field(description="Where the port number came from")
    link: str | None = Field(
        None, description="Over this PC's Tailscale address; null: see problem"
    )
    problem: str | None = None


class Suggestions(Open):
    """The add-ons and performance pages' suggestions, checked against this
    server. Nothing here is installed by itself."""

    type: str
    minecraft_version: str | None = None
    problem: str | None = Field(None, description="Why nothing could be checked")
    read_at: float | None = None
    stale: str | None = Field(None, description="A saved copy was used: how old, which site")
    good_to_have: list[Suggestion]
    often_used_with: list[Suggestion]
    performance: list[SpeedUp]
    performance_reason: dict[str, Any] | None = Field(
        None, description="{why: lag or memory, evidence}; null: nothing measured, no speed-ups"
    )
    for_your_players: list[ForPlayers]
    maps: list[MapLink]


class CopyTarget(Open):
    id: str
    name: str
    type: str


class CopyTargets(Open):
    targets: list[CopyTarget] = Field(description="Other servers with add-ons you may change")


class CopyItem(Open):
    filename: str
    name: str
    project: str | None = None
    version: str | None = None
    settings: list[str] = []


class CantCome(Open):
    filename: str
    name: str
    why: str


class CopyCheck(Open):
    target: str
    minecraft_version: str
    coming: list[CopyItem]
    cant_come: list[CantCome]
    settings_note: str | None = None


class CopyDone(Open):
    ok: bool
    job_id: str
    copied: list[CopyItem]
    cant_come: list[CantCome]
    settings_note: str | None = None
    safety_backup: SafetyBackup | None = Field(None, description="The one backup made first")
    undo: Undo | None = None


# ------------------------------------------------------------ guides
class GuideField(Open):
    id: str
    kind: str = Field(description="number, player, toggle, choice, world or name")
    label: str
    min: int | None = None
    max: int | None = None
    default: int | None = None
    options: list[dict[str, str]] | None = Field(None, description="value and label, for choices")


class GuideButton(Open):
    id: str
    label: str
    help: str = ""
    command: str = Field(description="The template; Technical mode shows it")
    fields: list[GuideField] = []
    changes_world: bool = Field(description="Ask before pressing it")
    warning: str | None = None


class Guide(Open):
    id: str
    title: str
    what: str
    steps: list[str]
    source: str = Field(description="The add-on's own page the guide was written from")
    filename: str = Field(description="The installed add-on it is for")
    addon_version: str
    buttons: list[GuideButton]


class GuideList(Open):
    guides: list[Guide]


class GuideAnswer(Open):
    command: str = Field(description="What was sent to the console")
    lines: list[dict[str, str]] = Field(description="What the console said next: raw, level")
    answered: bool = Field(description="False: the console said nothing within a few seconds")


# ------------------------------------------------------------ protection
class ProtectionLine(Open):
    id: str
    level: str = Field(description="worst, problem, suggest or info")
    words: str = Field(description="Its key in shared/strings.json")
    values: dict[str, Any]
    fix: str | None = Field(None, description="The fix id this account may use, or null")
    permission: str | None = None
    addons: list[dict[str, Any]] = Field(
        description="Add-ons to offer: project, name, small (a small community project)"
    )
    link: str | None = None
    technical: bool = Field(description="Only shown in Technical mode")


class ProtectionCard(Open):
    lines: list[ProtectionLine]
    running: bool


class ProtectionFixed(Open):
    ok: bool
    fix: str
    undo: dict[str, Any] | None = None


class JoinProtectionStatus(Open):
    on: bool = Field(description="The setting")
    running: bool = Field(description="The guard holds the port for the running server now")
    restart_needed: bool
    why_not: str | None = Field(None, description="java_only or start_command, or null")
    blocked_today: int = Field(description="Connections turned away today")
    size: str = Field(description="small or big, as used now")
    limits: dict[str, int]
    launch_until: float | None = None
    real_addresses: bool | None = Field(
        None, description="The server gets PROXY headers; null when the guard isn't running"
    )
    playit_seen: bool = Field(description="playit sent a real address within 7 days")
    asleep: bool = Field(description="Answering for a slept server")


class JoinBlock(Open):
    address: str
    until: float | None = Field(None, description="null: stays until removed")
    why: str
    by: str


class JoinLogRow(Open):
    name: str
    address: str = Field(description="Masked, like 1.2.x.x")
    result: str
    ts: float


class JoinProtection(Open):
    status: JoinProtectionStatus
    blocks: list[JoinBlock]
    log: list[JoinLogRow]


class FractureiserCheck(Open):
    found: list[str] = Field(description="Known markers found on this PC; empty: none found")
    checked: list[str] = Field(description="What was looked at")


# ------------------------------------------------------------ settings files
class ConfigFileRow(Open):
    path: str = Field(description="Inside the server folder, with / between folders")
    format: str = Field(description="json, toml, yaml, properties or text")
    size: int
    modified: float
    too_big: bool


class ConfigFileList(Open):
    name: str | None = Field(None, description="The add-on, for its own files")
    files: list[ConfigFileRow]
    truncated: bool = False


class ConfigFile(Open):
    path: str
    format: str
    text: str = Field(description="Hidden values show as ••••••")
    hidden: list[dict[str, Any]] = Field(description="line and key of each hidden value")
    version: str = Field(description="The file's sha256, sent back with a save")
    size: int
    running: bool


class ConfigFileSaved(Open):
    ok: bool
    path: str
    nothing_changed: bool | None = None
    version: str | None = None
    safety_backup: str | None = None
    undo: Undo | None = None
    running: bool
    restart_needed: bool = False


# ------------------------------------------------------------ modpacks
class ModpackFile(Open):
    name: str
    path: str | None = None
    size: int | None = None
    status: str = Field(description="download, client_only or refused")
    reason: str | None = None


class Modpack(Open):
    name: str
    version: str | None = None
    summary: str | None = None
    minecraft_version: str | None = None
    type: str | None = None
    type_name: str | None = None
    loader_version: str | None = None
    files: list[ModpackFile] = []
    download_count: int = 0
    download_bytes: int = 0
    client_only: list[str] = []
    skipped_overrides: list[str] = []
    problems: list[str] = []
    can_import: bool


class ModpackInto(Open):
    version_change: bool
    running: bool | None = None


class ModpackInspected(Open):
    token: str
    pack: Modpack
    into: ModpackInto | None = Field(None, description="What importing into server_id would do")


class ModpackImported(Open):
    ok: bool
    job_id: str | None = None
    modpack: dict[str, Any]
    version_change: bool | None = None
    undo: Undo | None = None


# ------------------------------------------------------------ game settings
class GameSetting(Open):
    key: str
    kind: str = Field(description="choice, bool, int, text, port or seed")
    value: Any = Field(None, description="What the file says, typed; null when unset or unreadable")
    set: bool
    problem: str | None = Field(None, description="Why the file's value couldn't be read")
    raw: str | None = Field(None, description="The file's own text for it; null when unset")
    minecraft_default: str
    choices: list[str] = []
    min: int | None = None
    max: int | None = None
    max_length: int | None = None
    read_only: str | None = Field(
        None, description='"world_exists" for the seed once there is a world'
    )


class GameSettings(Open):
    file: str
    exists: bool
    world_exists: bool
    fields: list[GameSetting]
    running: bool
    restart_needed: bool


class GameSettingsSaved(Open):
    ok: bool
    changed: dict[str, Any] = {}
    keys: list[str] = []
    nothing_changed: bool | None = None
    safety_backup: str | None = Field(None, description="The backup of the old file, by name")
    undo: Undo | None = None
    running: bool
    restart_needed: bool


# ------------------------------------------------------------ server settings
class PaletteColor(Open):
    id: str
    name: str
    hex: str


class CpuStatus(Open):
    configured: list[int] | None = Field(None, description="null: every core")
    applied: list[int] | None = Field(
        None, description="Read from the running server; null when unknown"
    )
    applied_reason: str | None = None
    problems: list[str] = []


class CpuOther(Open):
    id: str
    name: str
    cores: list[int] = []


class CpuOverview(Open):
    logical_cores: int | None = None
    supported: bool
    unsupported_reason: str | None = None
    others: list[CpuOther] = []
    status: CpuStatus


class SleepStatus(Open):
    enabled: bool
    minutes: int
    players_online: int | None = None
    stops_in: float | None = None
    list_answered: bool | None = None


class ServerSettings(Open):
    """One server's own settings: name and launch settings under "server",
    then its crash, backup and mod settings with its overrides applied."""

    server_id: str
    server: dict[str, Any]
    color: str | None = None
    palette: list[PaletteColor]
    monitor: dict[str, Any]
    backups: dict[str, Any]
    mods: dict[str, Any]
    overrides: dict[str, Any]
    editable: list[str]
    cpu: CpuOverview
    sleep: SleepStatus | None = None


class CpuApplied(Open):
    ok: bool
    applied: list[int] | None = Field(None, description="Read back from the running server")
    reason: str | None = None


class ServerSettingsSaved(Open):
    ok: bool
    applied: dict[str, Any]
    rejected: dict[str, str] = Field(
        description="Each refused key with the reason, said to the person"
    )
    cpu_cores: CpuApplied | None = Field(None, description="Only when the server is running")


class ColorSaved(Open):
    ok: bool
    color: str


class MemoryServer(Open):
    id: str
    name: str
    limit_mb: int | None = Field(None, description="null: no -Xmx set")
    running: bool
    this: bool


class MemoryOverview(Open):
    """The memory slider: bounded by the PC's measured memory."""

    total_mb: int | None = Field(None, description="null when it couldn't be measured")
    limit_mb: int | None = None
    jvm_args: list[str]
    takes_memory_limit: bool
    min_mb: int
    max_mb: int | None = None
    step_mb: int
    together_mb: int
    servers: list[MemoryServer]
    over: bool
    running: bool


class MemorySaved(MemoryOverview):
    ok: bool


# ------------------------------------------------------------ server types and versions
class ServerType(Open):
    id: str
    name: str
    edition: str = "java"
    content: str | None = None
    ease: str | None = None
    snapshots: bool = False
    picks_loader: bool = False
    loader_name: str | None = None
    latest_only: bool = False
    takes_memory_limit: bool = True
    crossplay: bool = False
    has_content: bool = False
    recommended: bool = False
    modrinth: bool = False
    metadata_files: list[str] = []
    version_source: str | None = None
    goals: list[str] = Field([], description="vanilla, plugins and/or mods: what it suits")
    technical_only: bool = Field(False, description="Offered for new servers only in Technical")
    changes_farms: bool = Field(False, description="Some farms work differently than in vanilla")
    never_recommended: bool = False
    like: str | None = Field(None, description="The built-in type a notes-file type copies")
    hint: str | None = Field(
        None, description="A notes-file type's one line; built-in ones are in the strings table"
    )


class ServerTypes(Open):
    types: list[ServerType]
    recommended: str
    default: str


class TypeAdvice(Open):
    """The New server picker's recommendation for what someone wants."""

    goal: str = Field(description="vanilla, plugins or mods")
    types: list[str] = Field(description="The Java types that fit, in the table's order")
    recommended: list[str]
    warn: list[str] = Field(
        description="Types that change how some farms work: picking one after saying vanilla shows the warning"
    )


class CommandButtonRow(Open):
    name: str
    command: str
    danger_reason: str | None = Field(None, description="set: ask before sending it")


class CommandButtons(Open):
    buttons: list[CommandButtonRow]
    max: int


class PlayerHour(Open):
    hour: int = Field(description="0 to 23, this PC's time")
    average: float | None = Field(None, description="null: never measured (Unknown)")
    busiest: int | None = None
    measured_days: int


class PlayerHours(Open):
    days: int
    hours: list[PlayerHour]
    quietest_hour: int | None = Field(None, description="null: no hour measured yet")
    measured_hours: int
    total_hours: int


class BackedUpServer(Open):
    """A server in an off-PC backup folder, with its newest copy."""

    id: str
    name: str
    type: str | None = None
    type_name: str | None = None
    minecraft: str | None = Field(None, description="null: its copies don't say")
    backup_name: str
    backup_at: float
    size_bytes: int
    copies: int
    here: bool = Field(description="a server on this PC has the same name")
    problem: str | None = Field(None, description="why it can't come back")


class BackupFolderServers(Open):
    servers: list[BackedUpServer]


class BringInFound(Open):
    """A server folder or zip, read before anything is copied."""

    source: str = Field(description="folder or zip")
    path: str
    type: str | None = Field(None, description="null: a kind the app can't tell (a custom jar)")
    type_name: str | None = None
    jar: str = ""
    jars: list[str] = []
    minecraft: str | None = Field(
        None, description="What its files say; confirmed only once the console prints it"
    )
    loader: str | None = None
    needs_install: bool = False
    content: str | None = None
    addons: int = 0
    worlds: int = 0
    size_bytes: int = 0
    files: int = 0
    problem: str | None = None
    can_bring_in: bool
    summary: str


class OfferedVersion(Open):
    minecraft: str
    stable: bool = True
    loaders: list[str] = []
    java_required: int | None = Field(
        None, description="The oldest Java it runs on; null when unknown"
    )
    kept: bool = False
    latest: bool = False


class SafetyNotesStatus(Open):
    """The signed notes file as the agent knows it. With no good file the
    message is "Couldn't check for safety notes.", never "no problems"."""

    state: str = Field(description="good, expired, refused or none")
    message: str
    reason: str | None = Field(None, description="Why the newest try gave no good file")
    serial: int | None = None
    issued: str | None = None
    expires: str | None = None
    fetched_at: float | None = None
    checked_at: float | None = None
    needs_newer_app: bool = False
    update_app: str | None = None
    advisories: int = 0
    known_bad_files: int = 0


class TypeVersions(Open):
    type: str
    type_name: str
    latest_only: bool = False
    snapshots: bool = False
    picks_loader: bool = False
    loader_name: str | None = None
    latest_stable: str | None = None
    versions: list[OfferedVersion]
    source_problem: str | None = Field(
        None,
        description='Why the list may be out of date ("From 3 days ago, couldn\'t reach PaperMC.")',
    )
    saved_at: float | None = Field(
        None, description="When the saved list shown was read; null when it was read just now"
    )


class LastVersionChange(Open):
    at: float | None = None
    type: str | None = None
    previous_type: str | None = None
    minecraft_version: str | None = None
    can_roll_back: bool = False


class ServerVersion(Open):
    """One server's type and version as its console reported them; null
    means Unknown, never a guess from a file name."""

    type: str
    capabilities: ServerType
    custom_jar: bool = Field(False, description="The owner's own jar: never checked or updated")
    minecraft_version: str | None = None
    minecraft_version_source: str | None = None
    loader_version: str | None = None
    loader_name: str | None = None
    detected_type: str | None = None
    pending_version: dict[str, Any] | None = None
    eula_required: bool = False
    eula_url: str
    last_change: LastVersionChange | None = None


class PreflightSide(Open):
    type: str
    type_name: str
    minecraft_version: str | None = None
    loader_version: str | None = None


class JavaCheck(Open):
    verdict: str = Field(description="ok, incompatible, unknown or not_needed")
    detail: str | None = None


class VersionPreflight(Open):
    """What a version or type change would do, before anything happens."""

    current: PreflightSide
    target: PreflightSide
    type_change: bool
    gentle_type_change: bool
    direction: str
    downgrade_warning: bool
    java: JavaCheck
    java_required: int | None = None
    moved_aside: list[str] = []
    unknown_support: list[str] = []
    not_supporting: list[str] = []
    crossplay: bool = False
    crossplay_available: bool = False


class TypeChangeTarget(Open):
    id: str
    name: str
    changes_farms: bool = False


class WorldCopy(Open):
    """A suggested name and folder for a Bedrock/Java copy, and what it leaves out."""

    name: str
    directory: str
    warning: str
    left_out: list[str]


class TypeChangeOptions(Open):
    """What the Change server type card offers (owner only)."""

    type: str
    targets: list[TypeChangeTarget]
    minecraft_version: str | None = Field(None, description="null: not seen yet, so no change")
    problem: str | None = None
    running: bool
    players_online: int = 0
    copy_to_other_edition: WorldCopy | None = Field(None, description="Technical mode only")


class SimilarAddon(Open):
    kind: str = Field(description="same (that add-on's build for the new type) or look_alike")
    name: str
    project: str = Field(description="The id to send to /mods/install")


class SimilarAddons(Open):
    """After Change server type: add-ons to offer with Add buttons. Each also
    says which old add-on it is for ("for")."""

    type: str
    suggestions: list[SimilarAddon]
    problems: list[str] = []


class VersionChanged(Open):
    ok: bool
    job_id: str
    verified: bool | None = None
    crossplay_turned_off: bool | None = None


class VersionRolledBack(Open):
    ok: bool
    job_id: str
    type: str


class EulaAccepted(Open):
    ok: bool
    url: str


# ------------------------------------------------------------ crossplay and duplicate
class Crossplay(Open):
    """Whether Bedrock players can join this Java server (Geyser)."""

    available: bool
    unavailable_reason: str | None = None
    enabled: bool
    port: int | None = None
    default_port: int
    suggested_port: int | None = None
    username_prefix: str = ""
    missing_mod: str | None = None
    ready: bool = False
    differences: list[str] = []
    address: str | None = Field(None, description="Only from a measured Tailscale address")
    address_unknown: str | None = None


class CrossplaySaved(Crossplay):
    ok: bool


class DuplicateOptions(Open):
    name: str
    directory: str
    running: bool
    crossplay: bool
    world_folders: list[str] = []


class DuplicateStarted(Open):
    ok: bool
    job: Job


class ServerRemoved(Open):
    """A server taken off the list. Nothing on disk was touched."""

    ok: bool
    removed: str
    name: str
    directory: str


# ------------------------------------------------------------ folder picker
class PcDrive(Open):
    name: str
    path: str


class PcShortcut(Open):
    key: str
    path: str


class PcFolder(Open):
    name: str
    path: str
    world: bool = Field(description="It holds a Minecraft world (a level.dat inside)")


class FolderListing(Open):
    """The folders inside one folder, names only. With no folder asked for:
    the PC's drives and the usual shortcuts, and path is null."""

    path: str | None
    parent: str | None = Field(description="null at a drive's root: going up shows the drives")
    folders: list[PcFolder]
    drives: list[PcDrive] = []
    shortcuts: list[PcShortcut] = []
    truncated: bool = False
    world: bool = False


# ------------------------------------------------------------ adding a server
class ServerAdded(Open):
    """A server folder already on the PC, now on the list. Nothing in it changed."""

    ok: bool
    server: ServerRow
    warnings: list[str] = Field(
        [], description="Said in the agent's own words: no port set, a shared port"
    )


class BedrockNewOptions(Open):
    port: int | None = Field(None, description="null when no free pair of UDP ports was found")
    port_v6: int | None = None
    eula_url: str
    privacy_url: str
    terms: dict[str, Any] | None = Field(
        None, description="Who accepted Mojang's terms and when; null before anyone did"
    )


class NewServerOptions(Open):
    types: list[ServerType]
    recommended: str
    memory_mb: int | None = Field(
        None, description="The suggested memory; null when the PC's memory couldn't be read"
    )
    memory_reason: str | None = None
    total_mb: int | None = Field(
        None,
        description="The PC's measured memory, the most a new server can be given; null when it couldn't be read",
    )
    port: int
    color: str
    palette: list[PaletteColor]
    eula_url: str
    bedrock: BedrockNewOptions


class ServerCreated(Open):
    """A brand-new server, set up and not started."""

    ok: bool
    server_id: str
    name: str
    directory: str
    port: int | None = None
    memory_mb: int | None = None
    job_id: str
    server: ServerRow
    next: str | None = None
    modpack: dict[str, Any] | None = Field(
        None, description="Only for a server made from a modpack"
    )


# ------------------------------------------------------------ world import and download
class WorldInfo(Open):
    """A world read before it replaces anything."""

    source: str = Field(description="zip or folder")
    folder_name: str
    name: str | None = Field(None, description="LevelName from level.dat; null when unreadable")
    version: str | None = Field(
        None, description="The game version that last saved it; null when unknown"
    )
    last_played: float | None = None
    size_bytes: int
    files: int
    read_problem: str | None = None
    edition: str = "java"


class WorldRead(Open):
    """An uploaded or picked world and whether it fits this server. Nothing
    changes until the import is confirmed with the token."""

    token: str
    world: WorldInfo
    edition_matches: bool
    server_edition: str
    server_version: str | None = None
    newer_than_server: bool | None = Field(None, description="null when either version isn't known")
    level_name: str | None = None


class SavedWorld(Open):
    folder: str
    path: str
    modified: float


class SavedWorlds(Open):
    folder: str | None = Field(
        None, description="Single-player's saves folder; null when it isn't on this PC"
    )
    worlds: list[SavedWorld]


class WorldImported(Open):
    ok: bool
    job_id: str
    world: WorldInfo
    safety_backup: SafetyBackup | None = None
    undo: Undo | None = None


class NewSeason(Open):
    ok: bool
    job_id: str
    seed: str | None = Field(None, description="null: Minecraft picks one on the next start")
    safety_backup: SafetyBackup | None = None
    undo: Undo | None = None


class WorldPacked(Open):
    """The world zipped for single-player; download it with the token."""

    ok: bool
    token: str
    files: int
    size_bytes: int
    filename: str


# ------------------------------------------------------------ PC settings
class SettingsSecrets(Open):
    discord_webhook_configured: bool
    smtp_configured: bool
    api_token_configured: bool


class SettingsView(Open):
    """The PC's settings, with passwords and links left out (they stay in .env)."""

    config: dict[str, Any]
    editable: list[str] = Field(description="Key prefixes PUT /settings accepts")
    secrets: SettingsSecrets


class SettingsSaved(Open):
    ok: bool
    applied: dict[str, Any]
    rejected: dict[str, str] = Field(description="Each refused key with the reason")


class SeparateWindowsUser(Open):
    on: bool
    ok: bool = Field(description="The account and its saved password both work")
    problem: str | None = Field(
        description="account, password or windows_only when ok is false, else null"
    )
    account: str
    available: bool
    restarting: list[str] = []


class MaintenanceSet(Open):
    ok: bool
    maintenance: bool


class TestAlert(Open):
    channel: str
    sent: bool


class PowerStatus(Open):
    supported: bool
    enabled: bool
    active: bool
    running: list[str] = []
    problem: str | None = None
    battery: dict[str, Any] | None = None
    lid: dict[str, Any] | None = None
    still_sleeps: list[str] = []


class StartupReport(Open):
    """Read from Windows and the agent's startup log; nothing is guessed."""

    supported: bool
    verdict: str
    registered: bool | None = None
    points_to_current_app: bool | None = None
    mechanism: str | None = None
    problems: list[str] = []


class ExportServerSizes(Open):
    id: str
    name: str
    files: int
    worlds: int
    backups: int


class ExportSizes(Open):
    servers: list[ExportServerSizes]


class ExportStarted(Open):
    ok: bool
    job_id: str


class PortUse(Open):
    protocol: str
    port: int
    server_id: str


class PortSuggestion(Open):
    port: int | None = Field(None, description="null when no free port was found")
    protocol: str
    used: list[PortUse]


class HelpBundleFile(Open):
    name: str
    size: int | None = None


class HelpBundleList(Open):
    files: list[HelpBundleFile]


class HelpBundleMade(Open):
    ok: bool
    token: str
    filename: str
    files: list[dict[str, Any]]


class SupportAsk(Open):
    show: bool
    reason: str = Field(description="answered, never_started, too_soon, something_wrong or ready")


class SecurityOverview(Open):
    agent_connected: bool
    tls: dict[str, Any] = Field(description="The certificate as read from disk")
    https_enabled: bool
    hostname: str | None = None
    tailscale: dict[str, Any]
    authentication: dict[str, Any]
    active_sessions: list[dict[str, Any]]
    failed_logins_24h: int
    bind_address: str


class AuditLog(Open):
    entries: list[dict[str, Any]] = Field(description="Newest first")


class Rotated(Open):
    token: str = Field(description="The new one; the old one stops working")
    expires_at: float | None = Field(description="null for a sign-in kept until signed out")


class SessionsRevoked(Open):
    ok: bool
    revoked: int


class TailscaleState(Open):
    moving: bool = Field(description="the desktop app's Tailscale step asked the agent to move")
    skipped: bool
    moved: bool = Field(description="the agent listens on its Tailscale address")
    restarting: bool = Field(False, description="it just moved and is restarting to listen there")
    address: str | None = None
    installed: bool | None = None
    connected: bool | None = Field(None, description="what Tailscale says; null when unknown")
    account: str | None = None
    error: str | None = None


# ------------------------------------------------------------ Discord bot
class DiscordStatus(Open):
    token_set: bool
    state: str = Field(
        description="off (no token), connecting, connected, token_rejected, intents_refused, "
        "stopped, or elsewhere (another of the owner's PCs runs the bot)"
    )
    bot_pc: dict[str, Any] | None = Field(
        None,
        description="With several PCs: the Bot PC's id, name, this, and its reach "
        "(another PC) or each PC's Bot link (this PC)",
    )
    servers: list[dict[str, Any]] = Field(
        default_factory=list, description="Servers a Discord server can be set up for, every PC's"
    )
    detail: str = ""
    connected_at: float | None = None
    name: str | None = Field(None, description="The bot's name on Discord, once connected")
    application_id: str | None = None
    invite_url: str | None = None
    switches: dict[str, bool | None] = Field(
        default_factory=dict,
        description="message_content and members: on, off, or null until read from Discord",
    )
    features_off: list[str] = Field(default_factory=list)
    no_game_chat: list[str] = Field(
        default_factory=list,
        description="Servers whose game chat can't reach Discord (their console prints none)",
    )
    addresses: dict[str, str] = Field(
        default_factory=dict, description="The address to share in Discord, per server id"
    )
    webhook_with_bot: bool = Field(False, description="The webhook too while the bot posts alerts")
    guilds: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Each Discord server: id, name, servers it's for, grants, and report "
        "(problems, roles, channels, tied, checked_at) once checked",
    )


class DiscordLevels(Open):
    announce: bool = Field(description="Level-ups are posted in the quests channel")
    leaderboard_day: int | None = Field(
        description="The weekly leaderboard's day, 0 (Monday) to 6, or null for none"
    )
    roles: list[dict[str, Any]] = Field(
        description="Each Level role: level, role_id and name (null until the bot has read it)"
    )
    store_ranks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The Store ranks and their paired role_id, when a server here has a store",
    )


class PlayerStats(Open):
    player: str
    server_id: str
    source: str = Field(description="files (the world's stats files) or console (Bedrock)")
    values: dict[str, int] = Field(
        description="Only what was found: playtime (seconds), deaths, mobs, blocks, "
        "advancements, or on Bedrock playtime and joins"
    )
    other: list[dict[str, Any]] = Field(description="A mod's own stats: name and value")
    saved_at: float | None = Field(
        description="When the stats files were last saved; null when there are none (or Bedrock)"
    )


class DiscordSetupDone(Open):
    made: list[str]
    status: DiscordStatus


class StartRequestOpen(Open):
    request: dict[str, Any] | None = Field(
        description="The open start request (server_id, who, asked_at, expires_at), or null"
    )


class StartRequestAnswered(Open):
    answer: str = Field(description="started or no")


class DiscordLinks(Open):
    links: list[dict[str, Any]]
    requests: list[dict[str, Any]] = Field(description="Bedrock players waiting for approval")
    players: list[dict[str, Any]] = Field(description="Recent players to pick from: name and uuid")
    reads_chat: bool


class WhitelistHelp(Open):
    """The Players page's whitelist help for one server."""

    online_mode: bool | None = Field(None, description="null when server.properties isn't there")
    addon: str | None = Field(None, description="The whitelist add-on used (offline mode only)")
    suggested: list[str] = Field(description="Whitelist add-ons for an offline-mode server")
    ip_addon: str | None = Field(None, description="The IP whitelist add-on installed, if any")
    forms: list[dict[str, Any]] = Field(
        description="Requests to join: id, name, why, discord_name, created_at"
    )
    addresses: list[dict[str, Any]] = Field(
        description="Addresses turned away: id, name, masked (never the whole address), why "
        "(changed or new), state (new, allowed, ignored), seen_at, answered_by, answered_at"
    )
    action: PlayerAction | None = Field(None, description="The command an answer sent")


class QuestsPage(Open):
    """Everything the Quests and events page shows for one server."""

    quests: list[dict[str, Any]] = Field(
        description="Quests going on: id, title, details, kind, goal, count, repeat, ends_at, "
        "rewards, period, joined, saved_at (null unless from the stats files) and members "
        "(discord_id, name, progress, finished_at)"
    )
    claims: list[dict[str, Any]] = Field(
        description="/quest done claims waiting: id, quest_id, title, discord_name, note, "
        "screenshot (null for none), created_at"
    )
    suggestions: list[dict[str, Any]] = Field(
        description="/quest suggest ideas: id, discord_name, title, details, created_at"
    )
    rewards: list[dict[str, Any]] = Field(
        description="Rewards not given yet, or sent without the server confirming: id, title, "
        "kind, value, state (waiting, sending, sent, failed), answer"
    )
    kinds: list[str] = Field(description="The quest kinds this server can check")
    bot: bool = Field(description="The Discord bot is connected (needed for Discord roles)")
    guilds: list[dict[str, Any]] = Field(
        description="The Discord servers set up for this server: id, name, roles (id, name) "
        "and levels (as GET /discord/guilds/{id}/levels)"
    )


class QuestScreenshot(Open):
    type: str = Field(description="png or jpg")
    image: str = Field(description="The picture, base64")


class EventsPage(Open):
    """The events and polls on the Quests and events page for one server."""

    events: list[dict[str, Any]] = Field(
        description="Events coming up or ended in the last 8 days: id, title, details, kind "
        "(advancement, online, approval), goal, starts_at, ends_at, start_server, rewards, "
        "state (coming, on, counting, approving, ended, cancelled), answers (going, maybe, no "
        "counts), going (discord_id, discord_name, answer), attendance (discord_id, "
        "discord_name, minutes, attended, how: measured, suggested or fixed, fixed_by) and "
        "fixable (attendance can still be fixed)"
    )
    polls: list[dict[str, Any]] = Field(
        description="Polls open or closed in the last 8 days: id, question, answers, votes "
        "(a count per answer), total, closes_at, closed_at, open"
    )
    kinds: list[str] = Field(description="The event kinds this server can check")
    bot: bool = Field(description="The Discord bot is connected (events are answered there)")


# ------------------------------------------------------------ your PCs
class PcReach(Open):
    state: str = Field(description="this, ok, sending, unreachable or unknown")
    since: float | None = Field(None, description="out of reach since; null when it answers")
    last_reached: float | None = None
    waiting: bool = Field(description="changes saved here that it hasn't got yet")


class PcRow(Open):
    id: str
    name: str
    address: str | None = None
    port: int | None = None
    fingerprint: str | None = None
    this: bool
    reach: PcReach


class PcList(Open):
    this_pc: str
    joined: bool
    pcs: list[PcRow]
    joins: list[dict[str, Any]] | None = Field(None, description="owner only: PCs asking to join")
    resets: list[dict[str, Any]] | None = Field(None, description="owner only: resets to confirm")
    joining: bool | None = None


class JoinLink(Open):
    ok: bool
    url: str
    qr: list[str]
    expires_at: float


class JoinAnswer(Open):
    ok: bool
    waiting: bool | None = None
    allowed: bool | None = None


class SyncChanges(Open):
    records: list[dict[str, Any]]
    seq: int


class BotLinkAnswer(Open):
    ok: bool
    done: bool | None = None
    played: list[dict[str, Any]] | None = None
    members: list[str] | None = None


class SyncTaken(Open):
    ok: bool
    taken: int


class SyncPing(Open):
    connected: bool | None = Field(description="TCP took the connection; null for UDP")
    answer: dict[str, Any] | None = Field(description="The server's own reply, null for none")


class SyncHello(Open):
    pc: str
    signing_key: str
    records: list[dict[str, Any]]
    seq: int


class StoreView(Open):
    offered: bool = Field(
        description="False: no Store tab (kids, Test, not for a community, Bedrock)"
    )
    reason: str | None
    needs_type: bool = Field(
        description="Not Paper, Purpur, Leaf or Fabric: offer Change server type"
    )
    connected: bool
    installed: dict[str, bool]
    ranks: list[dict[str, Any]]
    store: dict[str, Any] = Field(description="What the last check read from Tebex; never the key")
    packages: list[dict[str, Any]] | None
    checklist: str


class StoreChecked(StoreView):
    remind: list[str] = Field(description="Packages the app didn't make, seen for the first time")


class DiagnoseLine(Open):
    group: str = Field(
        description="What the line belongs to, e.g. 'Join from this PC as CommandBlock'"
    )
    what: str
    ok: bool | None = Field(
        description="True worked, False failed, null not checked (detail says why)"
    )
    detail: str | None


class DiagnoseChange(Open):
    key: str
    title: str = Field(description="What changes, e.g. 'Accept the Minecraft EULA'")
    why: str
    safe: bool = Field(description="False: a risky change, made only by Apply all")


class DiagnoseDone(Open):
    title: str
    safe: bool
    outcome: str = Field(
        description="kept, put_back (didn't help), no_help (nothing to put back), failed or denied"
    )
    detail: str


class DiagnoseKnown(Open):
    id: str
    say: str = Field(description="What Diagnose recognised, in plain words")
    link: str | None = Field(description="An https page about it; shown only, never opened")
    change: str | None = Field(description="The title of the change offered for it, if any")


class Diagnosis(Open):
    running: bool
    started_at: float | None
    finished_at: float | None
    by: str | None
    lines: list[DiagnoseLine]
    sign_in: bool = Field(
        default=False, description="A join wasn't tried: nobody signed in with Microsoft yet"
    )
    round: int = Field(default=1, description="1, then one more after each Apply")
    changes: list[DiagnoseChange] = Field(
        default_factory=list,
        description="Offered, waiting for POST /server/diagnose/changes (Safe ones first)",
    )
    done: list[DiagnoseDone] = Field(default_factory=list)
    mods: bool = Field(default=False, description="The join with all mods runs, every round")
    sign_in_mods: bool = Field(
        default=False,
        description="The join with all mods wasn't tried: nobody signed in to HeadlessMC yet",
    )
    known: list[DiagnoseKnown] = Field(
        default_factory=list, description="Known problems this round saw, one line each"
    )


class DiagnoseStarted(Open):
    job_id: str | None = Field(description="null when nothing started: see ask")
    ask: str | None = Field(
        default=None,
        description="Not started: the owner is playing and a join would kick them out. "
        "Ask this, then POST again with go_ahead",
    )
    ask_mods: str | None = Field(
        default=None,
        description="Not started: a mod server. Ask this, then POST again with mods true or false",
    )


class MicrosoftStatus(Open):
    signed_in: bool
    java_name: str | None = Field(
        description="Minecraft: Java Edition name, if the account has one"
    )
    gamertag: str | None = Field(description="Xbox gamertag, if it could be read")
    code: str | None = Field(description="While signing in: the code to type on Microsoft's page")
    url: str | None = Field(description="While signing in: where to type it")
    error: str | None = Field(description="Why the last sign-in didn't finish")


class HeadlessMcStatus(Open):
    signed_in: bool
    name: str | None = Field(description="The Minecraft name HeadlessMC signed in with")
    code: str | None = Field(description="While signing in: the code to type on Microsoft's page")
    url: str | None = Field(description="While signing in: the page to open (holds the code)")
    error: str | None = Field(description="Why the last sign-in didn't finish")
