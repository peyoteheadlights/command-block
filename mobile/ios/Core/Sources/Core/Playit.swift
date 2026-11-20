import Foundation

/// A tunnel playit's program reports.
public struct PlayitTunnel: Decodable, Equatable, Sendable {
    public var address: String
    public var disabled: Bool
    public var reason: String?

    enum CodingKeys: String, CodingKey { case address, disabled, reason }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        address = c.value(.address, "")
        disabled = c.value(.disabled, false)
        reason = c.maybe(.reason)
    }
}

/// One tunnel this server needs: Java on its port, Bedrock on Geyser's.
public struct PlayitWay: Decodable, Equatable, Sendable {
    public var kind: String
    public var localPort: Int
    public var tunnel: PlayitTunnel?

    enum CodingKeys: String, CodingKey {
        case kind, tunnel
        case localPort = "local_port"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = c.value(.kind, "java")
        localPort = c.value(.localPort, 0)
        tunnel = c.maybe(.tunnel)
    }
}

public struct PlayitClaim: Decodable, Equatable, Sendable {
    public var url: String
    public var state: String
    public var detail: String?
}

public struct PlayitTest: Decodable, Equatable, Sendable {
    public var works: Bool?
    public var address: String?
}

/// GET /api/servers/{id}/playit (agent/playit.py). Never holds playit's key.
public struct PlayitState: Decodable, Equatable, Sendable {
    public var on: Bool
    public var phase: String?
    public var problem: String?
    public var claim: PlayitClaim?
    public var ways: [PlayitWay]
    public var supported: Bool
    public var proxy: String
    public var newTunnel: String
    public var test: PlayitTest?
    public var mcsrvstatOk: Bool
    public var stopProblem: String?

    enum CodingKeys: String, CodingKey {
        case on, phase, problem, claim, ways, supported, proxy, test
        case newTunnel = "new_tunnel"
        case mcsrvstatOk = "mcsrvstat_ok"
        case stopProblem = "stop_problem"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        on = c.value(.on, false)
        phase = c.maybe(.phase)
        problem = c.maybe(.problem)
        claim = c.maybe(.claim)
        ways = c.value(.ways, [])
        supported = c.value(.supported, true)
        proxy = c.value(.proxy, "none")
        newTunnel = c.value(.newTunnel, "")
        test = c.maybe(.test)
        mcsrvstatOk = c.value(.mcsrvstatOk, false)
        stopProblem = c.maybe(.stopProblem)
    }
}

/// playit.gg on the phones: the same words and next step as the desktop
/// app's playit part (mirrors Playit.kt). Installing it is on the server's PC.
public enum Playit {
    /// What to say, and the one main button: use, link, reconnect, make or nil.
    public struct Step: Equatable, Sendable {
        public let lines: [Internet.Line]
        public let button: String?
        public let checkAgain: Bool
    }

    public static func step(_ strings: Strings, _ s: PlayitState, owner: Bool) -> Step {
        var out: [Internet.Line] = []
        func say(_ key: String, _ tone: Tone = .neutral, _ values: [String: String] = [:]) {
            out.append(Internet.Line(text: strings.t(key, values), tone: tone))
        }
        if !s.on {
            say("playit.note")
            if s.phase == "not_installed" {
                if owner { say("playit.not_here") }
                return Step(lines: out, button: nil, checkAgain: false)
            }
            return Step(lines: out, button: owner ? "use" : nil, checkAgain: false)
        }
        if let claim = s.claim, claim.state == "waiting" {
            say("playit.link")
            return Step(lines: out, button: "link", checkAgain: true)
        } else if let claim = s.claim {
            say("playit.link_\(claim.state)", .danger, ["detail": claim.detail ?? ""])
        } else if s.phase == "waiting_for_secret" {
            say("playit.not_linked", .danger)
        } else if s.phase != "running" {
            let words = strings.t("playit.stopped", ["phase": s.phase ?? "", "problem": s.problem ?? ""])
            out.append(Internet.Line(text: words.trimmingCharacters(in: .whitespaces), tone: .danger))
            return Step(lines: out, button: owner && s.phase != "not_installed" ? "reconnect" : nil, checkAgain: false)
        } else {
            for way in s.ways {
                if let tunnel = way.tunnel {
                    say("playit.join_\(way.kind)", .success, ["address": tunnel.address, "port": String(way.localPort)])
                    if tunnel.disabled { say("playit.disabled", .danger, ["reason": tunnel.reason ?? ""]) }
                } else {
                    let proxy = strings.t(s.proxy == "v2" ? "playit.proxy_v2" : "playit.proxy_none")
                    say("playit.make_tunnel", .neutral, ["kind": strings.t("playit.kind_\(way.kind)"), "port": String(way.localPort), "proxy": proxy])
                }
            }
            if let works = s.test?.works {
                let values = ["address": s.test?.address ?? "", "by": strings.t("address.by_mcsrvstat")]
                say(works ? "playit.works" : "playit.fails", works ? .success : .danger, values)
            }
            let missing = s.ways.contains { $0.tunnel == nil }
            return Step(lines: out, button: owner && missing ? "make" : nil, checkAgain: missing)
        }
        return Step(lines: out, button: owner ? "use" : nil, checkAgain: false)
    }

    /// The Java tunnel's address, for the outside test.
    public static func javaAddress(_ s: PlayitState) -> String? {
        guard let tunnel = s.ways.first(where: { $0.kind == "java" })?.tunnel, !tunnel.disabled else { return nil }
        return tunnel.address
    }
}
