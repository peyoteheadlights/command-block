import Foundation

/// Setup: Server settings, version and type changes, the raw editors, new
/// servers and the PC folder picker (mobile/api-routes.txt). Mirrors the
/// Android client.
extension AgentClient {
    public func serverSettings(serverId: String) async throws -> ServerSettings {
        try await call("GET", setup(serverId) + "/settings", as: ServerSettings.self)
    }

    /// Saves `updates` (only what changed). Refused keys come back in
    /// `rejected` with the agent's reason.
    public func saveServerSettings(serverId: String, updates: [String: JSONValue]) async throws -> ServerSettingsSaved {
        let body = try AgentClient.encode(["updates": JSONValue.object(updates)])
        return try await call("PUT", setup(serverId) + "/settings", body: body, as: ServerSettingsSaved.self)
    }

    public func memory(serverId: String) async throws -> MemoryOverview {
        try await call("GET", setup(serverId) + "/memory", as: MemoryOverview.self)
    }

    /// Sets only the -Xmx limit; used from the next start.
    public func setMemory(serverId: String, mb: Int) async throws -> MemoryOverview {
        let body = try AgentClient.encode(["memory_mb": mb])
        return try await call("PUT", setup(serverId) + "/memory", body: body, as: MemoryOverview.self)
    }

    public func setColor(serverId: String, color: String) async throws -> ColorSaved {
        let body = try AgentClient.encode(["color": color])
        return try await call("PUT", setup(serverId) + "/color", body: body, as: ColorSaved.self)
    }

    public func serverTypes() async throws -> ServerTypes {
        try await call("GET", "/api/server-types", as: ServerTypes.self)
    }

    /// The versions a type offers, from its official source (read through the PC).
    public func typeVersions(typeId: String) async throws -> TypeVersions {
        try await call("GET", "/api/server-types/" + AgentClient.pathSegment(typeId) + "/versions", slow: true, as: TypeVersions.self)
    }

    public func serverVersion(serverId: String) async throws -> ServerVersion {
        try await call("GET", setup(serverId) + "/version", as: ServerVersion.self)
    }

    /// What a change would do, before anything happens.
    public func versionPreflight(serverId: String, type: String, minecraft: String, loader: String?) async throws -> VersionPreflight {
        let body = try versionBody(type: type, minecraft: minecraft, loader: loader, confirm: false)
        return try await call("POST", setup(serverId) + "/version/preflight", body: body, slow: true, as: VersionPreflight.self)
    }

    /// Stops the server, takes a backup, installs and checks; answers once done.
    public func changeVersion(serverId: String, type: String, minecraft: String, loader: String?) async throws -> VersionChanged {
        let body = try versionBody(type: type, minecraft: minecraft, loader: loader, confirm: true)
        return try await call("POST", setup(serverId) + "/version", body: body, slow: true, as: VersionChanged.self)
    }

    public func rollBackVersion(serverId: String) async throws -> VersionRolledBack {
        try await call("POST", setup(serverId) + "/version/roll-back", body: Data("{}".utf8), slow: true, as: VersionRolledBack.self)
    }

    /// Only ever sent from the person's own tick.
    public func acceptEula(serverId: String) async throws -> EulaAccepted {
        try await call("POST", setup(serverId) + "/eula", body: Data("{}".utf8), as: EulaAccepted.self)
    }

    public func crossplay(serverId: String) async throws -> CrossplayInfo {
        try await call("GET", setup(serverId) + "/crossplay", as: CrossplayInfo.self)
    }

    /// Installs or moves aside Geyser and Floodgate; the server must be stopped.
    public func setCrossplay(serverId: String, enabled: Bool, port: Int?) async throws -> CrossplayInfo {
        var body: [String: JSONValue] = ["enabled": .bool(enabled)]
        if let port { body["port"] = .number(Double(port)) }
        return try await call("PUT", setup(serverId) + "/crossplay", body: try AgentClient.encode(body), slow: true, as: CrossplayInfo.self)
    }

    /// The MCXboxBroadcast add-on; on only after the risks were shown.
    public func setConsoleFriends(serverId: String, enabled: Bool) async throws -> CrossplayInfo {
        let body: [String: JSONValue] = ["enabled": .bool(enabled), "accept_risks": .bool(enabled)]
        return try await call("PUT", setup(serverId) + "/crossplay/console-friends", body: try AgentClient.encode(body), slow: true, as: CrossplayInfo.self)
    }

    public func duplicateOptions(serverId: String) async throws -> DuplicateOptions {
        try await call("GET", setup(serverId) + "/duplicate", as: DuplicateOptions.self)
    }

    /// Starts the copy as a task; the new server appears once it is checked.
    public func duplicate(serverId: String, name: String, directory: String, world: String, fromBackup: Int64? = nil) async throws -> DuplicateStarted {
        var fields: [String: JSONValue] = ["name": .string(name), "directory": .string(directory), "world": .string(world)]
        // Restore as a new server: the backup's world, settings and add-ons.
        if let fromBackup = fromBackup { fields["from_backup"] = .number(Double(fromBackup)) }
        let body = try AgentClient.encode(fields)
        return try await call("POST", setup(serverId) + "/duplicate", body: body, as: DuplicateStarted.self)
    }

    /// Takes a server off the list. Nothing on the PC is deleted.
    public func removeServer(serverId: String) async throws -> ServerRemoved {
        try await call("DELETE", setup(serverId), as: ServerRemoved.self)
    }

    /// "Don't show again" on a warning or tip, for this account on every device.
    public func hide(_ key: String) async throws -> Hidden {
        try await call("POST", "/api/account/hidden", body: try AgentClient.encode(["key": key]), as: Hidden.self)
    }

    /// Settings' "Show all warnings again".
    public func showAllAgain() async throws -> Hidden {
        try await call("DELETE", "/api/account/hidden", as: Hidden.self)
    }

    public func newServerOptions() async throws -> NewServerOptions {
        try await call("GET", "/api/new-server/options", as: NewServerOptions.self)
    }

    /// Downloads and sets up a new server as a task; it isn't started.
    public func createServer(_ new: NewServerRequest) async throws -> ServerCreated {
        var body: [String: JSONValue] = [
            "name": .string(new.name), "directory": .string(new.directory), "type": .string(new.type),
            "minecraft_version": .string(new.minecraft), "eula_accepted": .bool(new.eulaAccepted),
            "played_by": .string(new.playedBy), "players": .array(new.players.map { .string($0) }),
        ]
        if let loader = new.loader { body["loader_version"] = .string(loader) }
        if let memory = new.memoryMb { body["memory_mb"] = .number(Double(memory)) }
        if let color = new.color { body["color"] = .string(color) }
        return try await call("POST", "/api/new-server", body: try AgentClient.encode(body), slow: true, as: ServerCreated.self)
    }

    /// Puts a server folder already on the PC on the list; nothing in it changes.
    public func addServer(name: String, directory: String, type: String, jar: String) async throws -> ServerAdded {
        let body = try AgentClient.encode(["name": name, "directory": directory, "type": type, "jar": jar])
        return try await call("POST", "/api/servers", body: body, as: ServerAdded.self)
    }

    /// Makes a new server from a pack already read by `inspectModpack`.
    /// `ownJars`: its own jars were shown and said yes to.
    public func modpackServer(token: String, name: String, directory: String, memoryMb: Int?, color: String?, eulaAccepted: Bool, ownJars: Bool = false) async throws -> ServerCreated {
        var body: [String: JSONValue] = [
            "name": .string(name), "directory": .string(directory), "eula_accepted": .bool(eulaAccepted), "own_jars": .bool(ownJars),
        ]
        if let memory = memoryMb { body["memory_mb"] = .number(Double(memory)) }
        if let color { body["color"] = .string(color) }
        return try await call("POST", "/api/modpacks/" + AgentClient.pathSegment(token) + "/new-server", body: try AgentClient.encode(body), slow: true, as: ServerCreated.self)
    }

    /// The folders inside `path` on the PC, or the drives and shortcuts when nil.
    public func folders(path: String?) async throws -> FolderListing {
        guard let path, !path.isEmpty else { return try await call("GET", "/api/folders", as: FolderListing.self) }
        return try await call("GET", "/api/folders?path=" + AgentClient.pathSegment(path), as: FolderListing.self)
    }

    /// Chooses the folder for second copies; the agent checks it first.
    public func setOffsite(serverId: String, directory: String) async throws -> OffsiteSaved {
        let body = try AgentClient.encode(["directory": JSONValue.string(directory)])
        return try await call("PUT", setup(serverId) + "/backups/offsite", body: body, as: OffsiteSaved.self)
    }

    private func versionBody(type: String, minecraft: String, loader: String?, confirm: Bool) throws -> Data {
        var body: [String: JSONValue] = ["type": .string(type), "minecraft_version": .string(minecraft), "confirm": .bool(confirm)]
        if let loader { body["loader_version"] = .string(loader) }
        return try AgentClient.encode(body)
    }

    private func setup(_ id: String) -> String {
        "/api/servers/" + AgentClient.pathSegment(id)
    }
}
