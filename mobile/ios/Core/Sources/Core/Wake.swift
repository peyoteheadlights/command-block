import Foundation

/// The alerts this device gets (GET /api/account/alerts): `own` false means
/// it follows the shared list; `events` is every alert, on or off for it.
public struct DeviceAlerts: Codable, Equatable, Sendable {
    public var own: Bool
    public var events: [String: Bool]

    public init(own: Bool = false, events: [String: Bool] = [:]) {
        self.own = own
        self.events = events
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        own = try c.decodeIfPresent(Bool.self, forKey: .own) ?? false
        events = try c.decodeIfPresent([String: Bool].self, forKey: .events) ?? [:]
    }

    /// The events on for this device, in a stable order.
    public var chosen: [String] { events.filter { $0.value }.keys.sorted() }
}

/// What GET /api/wake answers (agent/wake.py for_device).
public struct WakeInfo: Codable, Equatable, Sendable {
    public var on: Bool
    public var pc: String
    public var mac: String?
    public var broadcast: String?
    public var helper: WakeHelper?
    public var routerUrl: String?
    public var hasKey: Bool

    enum CodingKeys: String, CodingKey {
        case on, pc, mac, broadcast, helper
        case routerUrl = "router_url"
        case hasKey = "has_key"
    }

    public init(on: Bool = false, pc: String = "", mac: String? = nil, broadcast: String? = nil,
                helper: WakeHelper? = nil, routerUrl: String? = nil, hasKey: Bool = false) {
        self.on = on
        self.pc = pc
        self.mac = mac
        self.broadcast = broadcast
        self.helper = helper
        self.routerUrl = routerUrl
        self.hasKey = hasKey
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        on = try c.decodeIfPresent(Bool.self, forKey: .on) ?? false
        pc = try c.decodeIfPresent(String.self, forKey: .pc) ?? ""
        mac = try c.decodeIfPresent(String.self, forKey: .mac)
        broadcast = try c.decodeIfPresent(String.self, forKey: .broadcast)
        helper = try c.decodeIfPresent(WakeHelper.self, forKey: .helper)
        routerUrl = try c.decodeIfPresent(String.self, forKey: .routerUrl)
        hasKey = try c.decodeIfPresent(Bool.self, forKey: .hasKey) ?? false
    }
}

public struct WakeHelper: Codable, Equatable, Sendable {
    public var address: String
    public var port: Int

    public init(address: String, port: Int) {
        self.address = address
        self.port = port
    }
}

struct WakeKey: Codable {
    let key: String
}

/// What the iPhone keeps, while the PC is awake, to wake it once it sleeps:
/// the agent's answer and its own wake key (only with a helping PC).
/// An iPhone can't send the wake signal itself (that needs Apple's
/// multicast permission), so Wake asks the helping PC or opens the
/// router's wake page.
public struct KeptWake: Codable, Equatable, Sendable {
    public var info: WakeInfo
    public var key: String?

    public init(info: WakeInfo, key: String? = nil) {
        self.info = info
        self.key = key
    }

    /// The helping PC, only when this phone has a key for it.
    public var helper: WakeHelper? { key == nil ? nil : info.helper }

    public var offered: Bool { info.on && (helper != nil || info.routerUrl != nil) }

    /// What the helping PC accepts (desktop/wake.py): "CBWAKE1 " and the key.
    public var helperRequest: Data? { key.map { Data(("CBWAKE1 " + $0).utf8) } }

    /// While the PC answers: what to keep, asking for this phone's own key
    /// when there's a helping PC and the agent doesn't know one for it yet.
    public static func keep(_ client: AgentClient, before: KeptWake?) async throws -> KeptWake {
        let info = try await client.wakeInfo()
        var key = before?.key
        if info.helper != nil, !(info.hasKey && key != nil) {
            key = try await client.wakeKey()
        }
        return KeptWake(info: info, key: info.helper == nil ? nil : key)
    }
}

extension AgentClient {
    public func deviceAlerts() async throws -> DeviceAlerts {
        try await call("GET", "/api/account/alerts", as: DeviceAlerts.self)
    }

    /// `own` false: follow the shared list again; true: only `events`.
    public func chooseDeviceAlerts(own: Bool, events: [String]) async throws -> DeviceAlerts {
        struct Body: Encodable {
            let own: Bool
            let events: [String]
        }
        let body = try AgentClient.encode(Body(own: own, events: events.sorted()))
        return try await call("PUT", "/api/account/alerts", body: body, as: DeviceAlerts.self)
    }

    public func wakeInfo() async throws -> WakeInfo {
        try await call("GET", "/api/wake", as: WakeInfo.self)
    }

    /// A new wake key for this device, shown once (the old one stops working).
    func wakeKey() async throws -> String {
        try await call("POST", "/api/wake/key", body: Data("{}".utf8), as: WakeKey.self).key
    }
}
