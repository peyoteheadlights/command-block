import XCTest
@testable import Core

/// All servers' groups, order and search, the console's completions, and
/// the new routes (mirrors ServerListingTest.kt).
final class ServerListingTests: XCTestCase {
    private let token = "secret-session-token-1234567890"

    override func setUp() {
        super.setUp()
        StubAgent.reset()
    }

    private func client() -> AgentClient {
        let value = token
        return AgentClient(
            pairing: Pairing(host: "localhost", port: 8765, fingerprint: String(repeating: "ab", count: 32), apiVersion: 1),
            token: { value },
            configuration: StubAgent.configuration()
        )
    }

    private func row(_ id: String, _ group: String = "") -> ServerRow {
        ServerRow(id: id, name: id.capitalized, group: group)
    }

    func testGroupsComeInListOrderServersWithoutOneLast() {
        let rows = [row("a"), row("b", "Friends"), row("c"), row("d", "Friends")]
        let grouped = ServerListing.grouped(rows, search: "")
        XCTAssertEqual(grouped.map(\.name), ["Friends", ""])
        XCTAssertEqual(grouped.map { $0.rows.map(\.id) }, [["b", "d"], ["a", "c"]])
        XCTAssertEqual(ServerListing.grouped([row("a"), row("c")], search: "A").map { $0.rows.map(\.id) }, [["a"]])
        XCTAssertEqual(ServerListing.grouped(rows, search: "friend").map(\.name), ["Friends"])
        XCTAssertTrue(ServerListing.grouped(rows, search: "zzz").isEmpty)
    }

    func testMovingIsOnePlaceAmongTheShownServers() {
        let order = ["a", "hidden", "b", "c"]
        XCTAssertEqual(ServerListing.moved(order: order, shown: ["a", "b", "c"], id: "b", up: true), ["b", "a", "hidden", "c"])
        XCTAssertEqual(ServerListing.moved(order: order, shown: ["a", "b", "c"], id: "a", up: false), ["hidden", "b", "a", "c"])
        XCTAssertNil(ServerListing.moved(order: order, shown: ["a", "b", "c"], id: "a", up: true))
        XCTAssertNil(ServerListing.moved(order: order, shown: ["a", "b", "c"], id: "c", up: false))
    }

    func testCompletionsAreTheServersOwnNamesForTheFirstWord() {
        let names = ["gamemode", "gamerule", "give", "say"]
        XCTAssertEqual(ServerListing.completions(names, typed: "GAM"), ["gamemode", "gamerule"])
        XCTAssertEqual(ServerListing.completions(names, typed: "gamerule "), [])
        XCTAssertEqual(ServerListing.completions(names, typed: "say"), [])
        XCTAssertEqual(ServerListing.completions(names, typed: ""), [])
    }

    func testStartAllGameRulesAndOlderLogsUseTheAgentsRoutes() async throws {
        StubAgent.json(#"{"servers": [{"id": "a", "name": "A", "result": "failed", "detail": "Port busy"}]}"#)
        let started = try await client().allServers(action: "start", ids: ["a"])
        XCTAssertEqual(started.servers.first?.detail, "Port busy")
        StubAgent.json(#"{"running": true, "rules": [{"name": "keepInventory", "value": false}], "presets": ["friendly"]}"#)
        let rules = try await client().gameRules(serverId: "a")
        XCTAssertEqual(rules.rules.first?.value, .bool(false))
        StubAgent.json(#"{"rules": [], "before": {"keepInventory": false}, "skipped": []}"#)
        let saved = try await client().saveGameRules(serverId: "a", rules: ["keepInventory": .bool(true)])
        XCTAssertEqual(saved.before, ["keepInventory": .bool(false)])
        StubAgent.json(#"{"name": "2026-11-19-1.log.gz", "lines": ["Done"], "cut": false}"#)
        let log = try await client().logFile(serverId: "a", name: "2026-11-19-1.log.gz")
        XCTAssertEqual(log.lines, ["Done"])
        XCTAssertEqual(StubAgent.requests.map { $0.request.url?.path }, [
            "/api/servers/start-all", "/api/servers/a/gamerules", "/api/servers/a/gamerules",
            "/api/servers/a/logs/files/2026-11-19-1.log.gz",
        ])
        XCTAssertEqual(String(data: StubAgent.requests[0].body, encoding: .utf8), #"{"ids":["a"]}"#)
        XCTAssertEqual(String(data: StubAgent.requests[2].body, encoding: .utf8), #"{"rules":{"keepInventory":true}}"#)
    }
}
