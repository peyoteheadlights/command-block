import Foundation

/// One port the router is asked to open.
public struct RouterPort: Decodable, Equatable, Sendable {
    public var `protocol`: String
    public var port: Int
}

/// GET /internet: "Let friends join from the internet" (agent/internet.py).
public struct InternetReach: Decodable, Equatable, Sendable {
    public var on: Bool
    public var busy: Bool
    public var known: Bool
    public var works: Bool?
    public var testedBy: String?
    public var reason: String?
    public var address: String?
    public var port: Int?
    public var ports: [RouterPort]
    public var localAddress: String?
    public var detail: String?
    public var mcsrvstatOk: Bool
    public var playit: String?
    public var names: [DdnsNameShort]

    enum CodingKeys: String, CodingKey {
        case on, busy, known, works, reason, address, port, ports, detail, playit, names
        case testedBy = "tested_by"
        case localAddress = "local_address"
        case mcsrvstatOk = "mcsrvstat_ok"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        on = c.value(.on, false)
        busy = c.value(.busy, false)
        known = c.value(.known, false)
        works = c.maybe(.works)
        testedBy = c.maybe(.testedBy)
        reason = c.maybe(.reason)
        address = c.maybe(.address)
        port = c.maybe(.port)
        ports = c.value(.ports, [])
        localAddress = c.maybe(.localAddress)
        detail = c.maybe(.detail)
        mcsrvstatOk = c.value(.mcsrvstatOk, false)
        playit = c.maybe(.playit)
        names = c.value(.names, [])
    }
}

/// A DuckDNS or No-IP name that works right now.
public struct DdnsNameShort: Decodable, Equatable, Sendable {
    public var provider: String
    public var name: String
}

/// One saved name (agent/ddns.py). The token or password never comes back.
public struct DdnsName: Decodable, Equatable, Sendable {
    public var name: String
    public var ip: String?
    public var problem: String?
    public var confirmBy: Double?
    public var confirmSoon: Bool

    enum CodingKeys: String, CodingKey {
        case name, ip, problem
        case confirmBy = "confirm_by"
        case confirmSoon = "confirm_soon"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        ip = c.maybe(.ip)
        problem = c.maybe(.problem)
        confirmBy = c.maybe(.confirmBy)
        confirmSoon = c.value(.confirmSoon, false)
    }
}

/// GET /api/ddns: this PC's DuckDNS and No-IP names.
public struct DdnsState: Decodable, Equatable, Sendable {
    public var duckdns: DdnsName?
    public var noip: DdnsName?
    public var duckdnsSite: String
    public var noipSite: String

    enum CodingKeys: String, CodingKey {
        case duckdns, noip
        case duckdnsSite = "duckdns_site"
        case noipSite = "noip_site"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        duckdns = c.maybe(.duckdns)
        noip = c.maybe(.noip)
        duckdnsSite = c.value(.duckdnsSite, "")
        noipSite = c.value(.noipSite, "")
    }
}

/// The words for what the agent says, the same rules as Internet.kt and
/// the desktop app's Server address card.
public enum Internet {
    public struct Line: Equatable, Sendable {
        public let text: String
        public let tone: Tone
    }

    /// "1.2.3.4:25565", or nil while the router gave no public address.
    public static func address(_ info: InternetReach) -> String? {
        guard let host = info.address else { return nil }
        return info.port.map { "\(host):\($0)" } ?? host
    }

    public static func lines(_ strings: Strings, _ info: InternetReach, owner: Bool) -> [Line] {
        var out: [Line] = []
        let full = address(info) ?? ""
        if !owner { out.append(Line(text: strings.t("address.owner_only"), tone: .neutral)) }
        let reason = info.reason ?? ""
        if reason == "off" {
            out.append(Line(text: strings.t("address.internet_note", ["port": info.port.map(String.init) ?? ""]), tone: .neutral))
        } else if reason == "not_running" {
            out.append(Line(text: strings.t("address.not_running"), tone: .neutral))
        } else if info.busy {
            out.append(Line(text: strings.t("address.busy"), tone: .neutral))
        } else if reason == "tested" {
            let by = strings.t(info.testedBy == "phone" ? "address.by_phone" : "address.by_mcsrvstat")
            out.append(info.works == true
                ? Line(text: strings.t("address.works", ["address": full, "by": by]), tone: .success)
                : Line(text: strings.t("address.fails", ["address": full, "by": by]), tone: .danger))
        } else if reason == "shared" || reason == "private" {
            out.append(Line(text: strings.t("address.\(reason)"), tone: .danger))
        } else if reason == "no_router" || reason == "refused" {
            out.append(Line(text: strings.t("address.\(reason)", ["detail": info.detail ?? ""]), tone: .danger))
        } else if !full.isEmpty {
            out.append(Line(text: strings.t("address.open", ["address": full]), tone: .neutral))
            out.append(Line(text: strings.t("address.not_known"), tone: .neutral))
        }
        return out
    }

    /// The port forwarding steps: after the router said no or a failed test, and always in Technical.
    public static func forwardSteps(_ strings: Strings, _ info: InternetReach, technical: Bool) -> [String] {
        let reason = info.reason ?? ""
        let failed = reason == "no_router" || reason == "refused" || (reason == "tested" && info.works == false)
        guard failed || (technical && reason != "shared" && reason != "private") else { return [] }
        let local = info.localAddress ?? "?"
        return info.ports.map {
            strings.t("address.forward_step", ["protocol": $0.protocol, "port": String($0.port), "local": local])
        } + [strings.t("address.forward_help")]
    }

    /// "Friends can also type myserver.duckdns.org:25565", once an outside test passed.
    public static func nameLines(_ strings: Strings, _ info: InternetReach) -> [String] {
        guard info.reason == "tested", info.works == true, let port = info.port else { return [] }
        return info.names.map { strings.t("ddns.join", ["name": $0.name, "port": String(port)]) }
    }

    /// A saved name's lines: the name, why it stopped, and No-IP's confirm date.
    public static func savedLines(_ strings: Strings, kind: String, _ name: DdnsName, date: (Double) -> String) -> [Line] {
        var out = [Line(text: strings.t("ddns.\(kind)_saved", ["name": name.name]), tone: .neutral)]
        if let problem = name.problem {
            out.append(Line(text: strings.t("ddns.stopped", ["problem": problem]), tone: .danger))
        }
        if let by = name.confirmBy {
            out.append(Line(text: strings.t("ddns.noip_confirm_by", ["date": date(by)]), tone: name.confirmSoon ? .danger : .neutral))
        }
        return out
    }

    /// The outside tests make sense once the router gave a public address.
    public static func testable(_ info: InternetReach) -> Bool {
        address(info) != nil && ["not_tested", "not_checked", "tested"].contains(info.reason ?? "")
    }
}
