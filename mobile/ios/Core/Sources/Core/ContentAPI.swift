import Foundation

/// A file picked on the phone (a .jar, a pack or a modpack). `url` is a
/// local copy the app made; it is read only while it is sent, so a big one
/// is never held in memory whole.
public struct PickedFile: Equatable, Sendable {
    public let name: String
    public let url: URL

    public init(name: String, url: URL) {
        self.name = name
        self.url = url
    }
}

/// Mods / Plugins / Packs and Game settings (mobile/api-routes.txt). Mods
/// change only while the server is stopped; the agent refuses otherwise, in
/// its own words. Installs, updates and imports answer once done, so they
/// use the slow session. Mirrors the Android client.
extension AgentClient {
    public static let SEARCH_PAGE = 20

    public func mods(serverId: String) async throws -> ModList {
        try await call("GET", content(serverId) + "/mods", as: ModList.self)
    }

    /// "On all servers": who needs each mod on every server this account sees.
    public func modSides() async throws -> ModSides {
        try await call("GET", "/api/mod-sides", as: ModSides.self)
    }

    /// Turns a mod or plugin on or off without removing it.
    public func setModEnabled(serverId: String, filename: String, enabled: Bool) async throws -> ModChanged {
        try await call("POST", content(serverId) + "/mods/" + (enabled ? "enable" : "disable"), body: try fileBody(filename), as: ModChanged.self)
    }

    /// Server, Players or Both. Players moves the jar out of the mods folder.
    public func setModSide(serverId: String, filename: String, side: String) async throws {
        let body = try AgentClient.encode(["filename": filename, "side": side])
        _ = try await call("POST", content(serverId) + "/mods/side", body: body, as: Ok.self)
    }

    /// A mod, resource pack or shader pack for players only.
    public func uploadForPlayers(serverId: String, file: PickedFile) async throws {
        _ = try await upload(content(serverId) + "/mods/player-only/upload?confirm=true", file, as: Ok.self)
    }

    /// A missing player-only file, downloaded again from Modrinth and checked by its hash.
    public func fetchForPlayers(serverId: String, filename: String) async throws {
        _ = try await call("POST", content(serverId) + "/mods/player-only/fetch", body: try fileBody(filename), as: Ok.self)
    }

    public func setPlayerOnlyOptions(serverId: String, options: PlayerOnlyOptions) async throws {
        _ = try await call("POST", content(serverId) + "/mods/player-only/options", body: try AgentClient.encode(options), as: PlayerOnlyOptions.self)
    }

    public func removeForPlayers(serverId: String, filename: String) async throws {
        _ = try await call("POST", content(serverId) + "/mods/player-only/remove", body: try fileBody(filename), as: Ok.self)
    }

    /// What else needs a mod, asked before removing it.
    public func modImpact(serverId: String, filename: String) async throws -> ModImpact {
        try await call("GET", content(serverId) + "/mods/impact?filename=" + AgentClient.pathSegment(filename), as: ModImpact.self)
    }

    /// Removes a mod; its file is kept so it can be put back.
    public func removeMod(serverId: String, filename: String) async throws -> ModRemoved {
        try await call("POST", content(serverId) + "/mods/remove", body: try fileBody(filename), as: ModRemoved.self)
    }

    public func updateMod(serverId: String, filename: String, versionId: String?) async throws -> ModUpdated {
        let body = try AgentClient.encode(UpdateBody(filename: filename, versionId: versionId))
        return try await call("POST", content(serverId) + "/mods/update", body: body, slow: true, as: ModUpdated.self)
    }

    /// The copies of a mod kept on this server, newest first.
    public func modVersions(serverId: String, modId: String) async throws -> ModVersions {
        try await call("GET", content(serverId) + "/mods/versions/" + AgentClient.pathSegment(modId), as: ModVersions.self)
    }

    public func rollBackMod(serverId: String, modId: String, archivePath: String) async throws -> ModRolledBack {
        let body = try AgentClient.encode(RollBackBody(modId: modId, archivePath: archivePath))
        return try await call("POST", content(serverId) + "/mods/rollback", body: body, as: ModRolledBack.self)
    }

    public func modHistory(serverId: String, limit: Int = 100) async throws -> ModHistory {
        let count = Swift.min(Swift.max(limit, 1), 500)
        return try await call("GET", content(serverId) + "/mods/history?limit=\(count)", as: ModHistory.self)
    }

    /// What Update all would change, checked on Modrinth now (a mod at a
    /// time on the PC, so it can take a while with many).
    public func updateAllPlan(serverId: String) async throws -> UpdateAllPlan {
        try await call("GET", content(serverId) + "/mods/update-all", slow: true, as: UpdateAllPlan.self)
    }

    /// Hides the "Still fit?" card until the next change checks again.
    public func hideFit(serverId: String) async throws {
        _ = try await call("POST", content(serverId) + "/mods/fit/hide", body: Data("{}".utf8), as: Ok.self)
    }

    /// Updates the ticked ones as one change (one backup, all or nothing).
    public func updateAll(serverId: String, _ body: UpdateAllBody) async throws -> UpdateAllResult {
        try await call("POST", content(serverId) + "/mods/update-all", body: try AgentClient.encode(body), slow: true, as: UpdateAllResult.self)
    }

    /// Modrinth results that fit this server's type and Minecraft version.
    public func searchMods(serverId: String, query: String, offset: Int = 0) async throws -> ModSearch {
        let path = content(serverId) + "/mods/search?q=" + AgentClient.pathSegment(query)
            + "&limit=\(AgentClient.SEARCH_PAGE)&offset=\(Swift.max(offset, 0))"
        return try await call("GET", path, as: ModSearch.self)
    }

    public func modProject(serverId: String, slug: String) async throws -> ModProject {
        try await call("GET", content(serverId) + "/mods/project/" + AgentClient.pathSegment(slug), as: ModProject.self)
    }

    public func installMod(serverId: String, slug: String, versionId: String?, withDependencies: Bool) async throws -> ModInstalled {
        let body = try AgentClient.encode(InstallBody(project: slug, versionId: versionId, installDependencies: withDependencies))
        return try await call("POST", content(serverId) + "/mods/install", body: body, slow: true, as: ModInstalled.self)
    }

    public func dependencies(serverId: String) async throws -> DependencyReport {
        try await call("GET", content(serverId) + "/mods/dependencies", as: DependencyReport.self)
    }

    /// What installing the missing ones would download. Nothing is yet.
    public func planDependencies(serverId: String) async throws -> DependencyPlan {
        try await call("POST", content(serverId) + "/mods/dependencies/plan", body: Data("{}".utf8), as: DependencyPlan.self)
    }

    public func installDependencies(serverId: String) async throws -> DependencyInstall {
        try await call("POST", content(serverId) + "/mods/dependencies/install", body: Data("{}".utf8), slow: true, as: DependencyInstall.self)
    }

    /// Adds a mod or plugin file (.jar) picked on the phone. Picking it counts as the
    /// yes for a file no site lists (the phone has no "Install anyway?" screen yet).
    public func uploadMod(serverId: String, file: PickedFile) async throws -> ModUploaded {
        try await upload(content(serverId) + "/mods/upload?confirm=true", file, as: ModUploaded.self)
    }

    /// Reads a modpack (.mrpack); with `serverId`, also what importing it there would do.
    public func inspectModpack(serverId: String?, file: PickedFile) async throws -> ModpackInspected {
        let path = serverId.map { "/api/modpacks/inspect?server_id=" + AgentClient.pathSegment($0) } ?? "/api/modpacks/inspect"
        return try await upload(path, file, as: ModpackInspected.self)
    }

    /// Modrinth's and CurseForge's modpacks side by side, each named.
    public func searchModpacks(query: String) async throws -> ModSearch {
        let path = "/api/modpacks/search?q=" + AgentClient.pathSegment(query) + "&limit=\(AgentClient.SEARCH_PAGE)"
        return try await call("GET", path, as: ModSearch.self)
    }

    /// Downloads a found modpack's newest release and reads it like a picked file.
    public func fetchModpack(_ hit: ModrinthHit, serverId: String?) async throws -> ModpackInspected {
        let body = try AgentClient.encode(FetchBody(project: hit.projectId ?? "", server_id: serverId ?? ""))
        return try await call("POST", "/api/modpacks/" + Modpacks.site(hit), body: body, slow: true, as: ModpackInspected.self)
    }

    /// Throws away a modpack read but not imported.
    public func forgetModpack(token: String) async throws {
        _ = try await call("DELETE", "/api/modpacks/" + AgentClient.pathSegment(token), as: Ok.self)
    }

    /// `ownJars`: the pack's own jars were shown and said yes to.
    public func importModpack(serverId: String, token: String, ownJars: Bool = false) async throws -> ModpackImported {
        let body = try AgentClient.encode(ImportBody(token: token, confirm: true, ownJars: ownJars))
        return try await call("POST", content(serverId) + "/modpack", body: body, slow: true, as: ModpackImported.self)
    }

    /// Downloads the linked modpack's new version and says what Modpack update changes.
    public func previewModpackUpdate(serverId: String, versionId: String?) async throws -> ModpackUpdatePreview {
        let body = try AgentClient.encode(PreviewBody(versionId: versionId))
        return try await call("POST", content(serverId) + "/modpack/update/preview", body: body, slow: true, as: ModpackUpdatePreview.self)
    }

    /// Modpack update: one backup, all or nothing, one Undo.
    public func updateModpack(serverId: String, token: String, usePackConfigs: Bool, ownJars: Bool = false) async throws {
        let body = try AgentClient.encode(ModpackUpdateBody(token: token, usePackConfigs: usePackConfigs, confirm: true, ownJars: ownJars))
        _ = try await call("POST", content(serverId) + "/modpack/update", body: body, slow: true, as: Ok.self)
    }

    /// Unlink: the server keeps its mods and stops following the modpack.
    public func unlinkModpack(serverId: String) async throws {
        _ = try await call("DELETE", content(serverId) + "/modpack/link", as: Ok.self)
    }

    /// What Make a modpack offers: name, next version, config files, players' loaders.
    public func makeModpackOptions(serverId: String) async throws -> ModpackMakeOptions {
        try await call("GET", content(serverId) + "/modpack/make", as: ModpackMakeOptions.self)
    }

    /// Makes a modpack from this server, as a task; `downloadModpack` fetches it.
    public func makeModpack(serverId: String, _ answers: MakeModpack) async throws -> ModpackMade {
        let body = try AgentClient.encode(answers)
        return try await call("POST", content(serverId) + "/modpack/make", body: body, slow: true, as: ModpackMade.self)
    }

    /// Writes the made modpack to `destination`, for the share sheet.
    public func downloadModpack(serverId: String, made: ModpackMade, to destination: URL) async throws -> Int64 {
        try await downloadZip(content(serverId) + "/modpack/made/" + AgentClient.pathSegment(made.token), to: destination, progress: { _, _ in })
    }

    /// The Discord bot posts the made modpack in this server's chat channels.
    public func postModpack(serverId: String, token: String) async throws -> ModpackPosted {
        let body = try AgentClient.encode(["token": token])
        return try await call("POST", content(serverId) + "/modpack/made/discord", body: body, as: ModpackPosted.self)
    }

    /// The download link and the server resource pack.
    public func downloadLink(serverId: String) async throws -> DownloadLink {
        try await call("GET", content(serverId) + "/download-link", as: DownloadLink.self)
    }

    /// Turns the download link on (a new link) or off (the old link stops). Owner only.
    public func setDownloadLink(serverId: String, on: Bool) async throws -> DownloadLink {
        let body = try AgentClient.encode(["on": on])
        return try await call("POST", content(serverId) + "/download-link", body: body, slow: true, as: DownloadLink.self)
    }

    /// A new link: the old one stops working at once.
    public func newDownloadLink(serverId: String) async throws -> DownloadLink {
        try await call("POST", content(serverId) + "/download-link/new", body: Data("{}".utf8), slow: true, as: DownloadLink.self)
    }

    /// A resource pack (.zip) picked on the phone, served by the download link.
    /// The title-screen background (a PNG picked on the phone) for the next modpacks.
    public func uploadTitleBackground(serverId: String, file: PickedFile) async throws -> ModpackMakeOptions {
        try await upload(content(serverId) + "/modpack/title-background", file, as: ModpackMakeOptions.self)
    }

    public func uploadResourcePack(serverId: String, file: PickedFile, required: Bool) async throws -> DownloadLink {
        try await upload(content(serverId) + "/resource-pack/upload?required=\(required)", file, as: DownloadLink.self)
    }

    /// A resource pack someone else hosts, with the site's SHA-1 when it gives one.
    public func setResourcePackLink(serverId: String, url: String, sha1: String, required: Bool) async throws -> DownloadLink {
        let body = try AgentClient.encode(PackLink(url: url, sha1: sha1, required: required))
        return try await call("POST", content(serverId) + "/resource-pack", body: body, slow: true, as: DownloadLink.self)
    }

    /// "Players must accept the resource pack".
    public func setResourcePackRequired(serverId: String, required: Bool) async throws -> DownloadLink {
        let body = try AgentClient.encode(["required": required])
        return try await call("POST", content(serverId) + "/resource-pack", body: body, slow: true, as: DownloadLink.self)
    }

    public func removeResourcePack(serverId: String) async throws -> DownloadLink {
        try await call("DELETE", content(serverId) + "/resource-pack", slow: true, as: DownloadLink.self)
    }

    /// The anti-cheat picker. Installing goes through `installMod` and `uploadMod`.
    public func antiCheat(serverId: String) async throws -> AntiCheat {
        try await call("GET", content(serverId) + "/anticheat", as: AntiCheat.self)
    }

    /// The "Put it on Modrinth" card.
    public func modrinth(serverId: String) async throws -> ModrinthShare {
        try await call("GET", content(serverId) + "/modrinth", as: ModrinthShare.self)
    }

    /// Modrinth's sign-in page; it comes back to the app as mcsc://modrinth. Owner only.
    public func modrinthSignIn() async throws -> ModrinthSignInStarted {
        let body = try AgentClient.encode(["return_to": "phone"])
        return try await call("POST", "/api/modrinth/sign-in", body: body, as: ModrinthSignInStarted.self)
    }

    /// The code Modrinth gave: the PC swaps it and keeps the sign-in.
    public func modrinthFinish(code: String, state: String) async throws -> ModrinthAccount {
        let body = try AgentClient.encode(["code": code, "state": state])
        return try await call("POST", "/api/modrinth/sign-in/finish", body: body, slow: true, as: ModrinthAccount.self)
    }

    public func modrinthSignOut() async throws -> ModrinthAccount {
        try await call("POST", "/api/modrinth/sign-out", body: Data("{}".utf8), as: ModrinthAccount.self)
    }

    /// The newest modpack up as a draft; `slug` and `summary` only the first time.
    public func modrinthUpload(serverId: String, slug: String, summary: String) async throws -> ModrinthShare {
        let body = try AgentClient.encode(["slug": slug, "summary": summary])
        return try await call("POST", content(serverId) + "/modrinth/upload", body: body, slow: true, as: ModrinthShare.self)
    }

    /// One own jar of the newest modpack as its own Modrinth mod (a draft).
    public func modrinthOwnJar(serverId: String, path: String, slug: String, summary: String) async throws -> ModrinthShare {
        let body = try AgentClient.encode(["path": path, "slug": slug, "summary": summary])
        return try await call("POST", content(serverId) + "/modrinth/own-jar", body: body, slow: true, as: ModrinthShare.self)
    }

    /// The Modrinth Server Project's link (empty: forget it).
    public func modrinthServerProject(serverId: String, link: String) async throws -> ModrinthShare {
        let body = try AgentClient.encode(["link": link])
        return try await call("POST", content(serverId) + "/modrinth/server-project", body: body, slow: true, as: ModrinthShare.self)
    }

    /// A Bedrock server's behavior and resource packs.
    public func addons(serverId: String) async throws -> AddonList {
        try await call("GET", content(serverId) + "/addons", as: AddonList.self)
    }

    public func uploadAddon(serverId: String, file: PickedFile) async throws -> AddonResult {
        try await upload(content(serverId) + "/addons/upload", file, as: AddonResult.self)
    }

    public func setAddonsRequired(serverId: String, required: Bool) async throws -> AddonResult {
        let body = try AgentClient.encode(["required": required])
        return try await call("PUT", content(serverId) + "/addons/required", body: body, as: AddonResult.self)
    }

    public func setAddonEnabled(serverId: String, uuid: String, enabled: Bool) async throws -> AddonResult {
        let body = try AgentClient.encode(["enabled": enabled])
        return try await call("PUT", content(serverId) + "/addons/" + AgentClient.pathSegment(uuid), body: body, as: AddonResult.self)
    }

    public func removeAddon(serverId: String, uuid: String) async throws -> AddonResult {
        try await call("DELETE", content(serverId) + "/addons/" + AgentClient.pathSegment(uuid), as: AddonResult.self)
    }

    public func gameSettings(serverId: String) async throws -> GameSettings {
        try await call("GET", content(serverId) + "/game-settings", as: GameSettings.self)
    }

    /// Saves only `values`, the ones changed. A refused one comes back as
    /// `AgentError.failed` with each value's reason in its problems.
    public func saveGameSettings(serverId: String, values: [String: JSONValue]) async throws -> GameSettingsSaved {
        let body = try AgentClient.encode(["values": JSONValue.object(values)])
        return try await call("PUT", content(serverId) + "/game-settings", body: body, as: GameSettingsSaved.self)
    }

    /// Saves the whole server.properties as typed in Technical mode. The
    /// agent still checks the known keys, and writes nothing when one fails.
    public func saveGameSettingsText(serverId: String, text: String) async throws -> GameSettingsSaved {
        let body = try AgentClient.encode(["text": JSONValue.string(text)])
        return try await call("PUT", content(serverId) + "/game-settings/raw", body: body, as: GameSettingsSaved.self)
    }

    /// The server icon as base64 PNG, or nil when there is none.
    public func serverIcon(serverId: String) async throws -> String? {
        try await call("GET", content(serverId) + "/server-icon", as: ServerIcon.self).png
    }

    /// A new server icon: a 64 x 64 PNG in base64 (the app resizes it first).
    public func saveServerIcon(serverId: String, png: String) async throws -> ServerIconSaved {
        let body = try AgentClient.encode(["png": JSONValue.string(png)])
        return try await call("PUT", content(serverId) + "/server-icon", body: body, as: ServerIconSaved.self)
    }

    public func removeServerIcon(serverId: String) async throws -> ServerIconSaved {
        try await call("DELETE", content(serverId) + "/server-icon", as: ServerIconSaved.self)
    }

    // MARK: - Plumbing

    private func content(_ id: String) -> String {
        "/api/servers/" + AgentClient.pathSegment(id)
    }

    private func fileBody(_ filename: String) throws -> Data {
        try AgentClient.encode(["filename": filename])
    }

    /// Sends a picked file as a form upload (field
    /// "file"). The form is written to a file next to it and sent from
    /// there, so the phone never holds the whole file in memory.
    /// `sent` gets the bytes sent so far and the form's size.
    func upload<T: Decodable>(
        _ path: String,
        _ file: PickedFile,
        sent: (@Sendable (Int64, Int64?) -> Void)? = nil,
        as type: T.Type
    ) async throws -> T {
        let boundary = "mcsc-" + UUID().uuidString
        let form = FileManager.default.temporaryDirectory.appendingPathComponent(boundary + ".form")
        defer { try? FileManager.default.removeItem(at: form) }
        try AgentClient.writeForm(file, boundary: boundary, to: form)
        return try await call(
            "POST", path, slow: true, upload: (form, "multipart/form-data; boundary=" + boundary), sent: sent, as: type
        )
    }

    /// The multipart form for one file, written to `destination`.
    static func writeForm(_ file: PickedFile, boundary: String, to destination: URL) throws {
        let safeName = file.name.replacingOccurrences(of: "\"", with: "_").replacingOccurrences(of: "\r", with: "").replacingOccurrences(of: "\n", with: "")
        FileManager.default.createFile(atPath: destination.path, contents: nil)
        let out = try FileHandle(forWritingTo: destination)
        defer { try? out.close() }
        let head = "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        try out.write(contentsOf: Data(head.utf8))
        let input = try FileHandle(forReadingFrom: file.url)
        defer { try? input.close() }
        while let chunk = try input.read(upToCount: 1 << 20), !chunk.isEmpty {
            try out.write(contentsOf: chunk)
        }
        try out.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    }

    private struct UpdateBody: Encodable {
        let filename: String
        let versionId: String?

        enum CodingKeys: String, CodingKey {
            case filename
            case versionId = "version_id"
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(filename, forKey: .filename)
            try c.encode(versionId, forKey: .versionId)
        }
    }

    private struct RollBackBody: Encodable {
        let modId: String
        let archivePath: String

        enum CodingKeys: String, CodingKey {
            case modId = "mod_id"
            case archivePath = "archive_path"
        }
    }

    /// The Far view card.
    public func farView(serverId: String) async throws -> FarView {
        try await call("GET", content(serverId) + "/far-view", as: FarView.self)
    }

    /// Far view "off", "distanthorizons" or "voxy": installs what it needs. The server must be stopped.
    public func chooseFarView(serverId: String, choice: String) async throws -> FarViewChanged {
        let body = try AgentClient.encode(["choice": choice])
        return try await call("POST", content(serverId) + "/far-view", body: body, slow: true, as: FarViewChanged.self)
    }

    public func farViewOptions(serverId: String, unvisited: Bool, loadAhead: Bool, radius: Int, world: String) async throws -> FarView {
        let body = try AgentClient.encode(FarViewBody(unvisited: unvisited, loadAhead: loadAhead, radius: radius, world: world))
        return try await call("POST", content(serverId) + "/far-view/options", body: body, as: FarView.self)
    }

    /// Deletes the far-view data; it's built again from the world.
    public func rebuildFarView(serverId: String) async throws -> FarViewRebuilt {
        try await call("POST", content(serverId) + "/far-view/rebuild", body: try AgentClient.encode([String: String]()), slow: true, as: FarViewRebuilt.self)
    }

    private struct FarViewBody: Encodable {
        let unvisited: Bool
        let loadAhead: Bool
        let radius: Int
        let world: String

        enum CodingKeys: String, CodingKey {
            case unvisited, radius, world
            case loadAhead = "load_ahead"
        }
    }

    private struct InstallBody: Encodable {
        let project: String
        let versionId: String?
        let installDependencies: Bool

        enum CodingKeys: String, CodingKey {
            case project
            case versionId = "version_id"
            case installDependencies = "install_dependencies"
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(project, forKey: .project)
            try c.encode(versionId, forKey: .versionId)
            try c.encode(installDependencies, forKey: .installDependencies)
        }
    }

    private struct PreviewBody: Encodable {
        let versionId: String?
        enum CodingKeys: String, CodingKey { case versionId = "version_id" }
    }

    private struct ModpackUpdateBody: Encodable {
        let token: String
        let usePackConfigs: Bool
        let confirm: Bool
        let ownJars: Bool
        enum CodingKeys: String, CodingKey {
            case token, confirm
            case usePackConfigs = "use_pack_configs"
            case ownJars = "own_jars"
        }
    }

    private struct ImportBody: Encodable {
        let token: String
        let confirm: Bool
        let ownJars: Bool
        enum CodingKeys: String, CodingKey {
            case token, confirm
            case ownJars = "own_jars"
        }
    }

    private struct FetchBody: Encodable {
        let project: String
        let server_id: String
    }
}

/// What Update all sends: the ticked ones, in the plan's order, each with
/// the version the plan showed (null: the newest that fits).
public struct UpdateAllBody: Encodable, Equatable, Sendable {
    public struct Item: Encodable, Equatable, Sendable {
        public let filename: String
        public let versionId: String?

        enum CodingKeys: String, CodingKey {
            case filename
            case versionId = "version_id"
        }

        public func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(filename, forKey: .filename)
            try c.encode(versionId, forKey: .versionId)
        }
    }

    public let mods: [Item]
}

private struct PackLink: Encodable {
    var url: String
    var sha1: String
    var required: Bool
}
