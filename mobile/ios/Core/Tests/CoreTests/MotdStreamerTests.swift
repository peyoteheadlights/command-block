import XCTest
@testable import Core

/// The MOTD builder's codes, streamer mode's rules and the server icon's
/// routes (mirrors MotdStreamerTest.kt).
final class MotdStreamerTests: XCTestCase {
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

    func testColourCodesBecomePiecesTheWayTheServerListDrawsThem() {
        let pieces = Motd.pieces("§6§lGold\n§rplain§cred")
        XCTAssertEqual(pieces.map(\.text), ["Gold\n", "plain", "red"])
        XCTAssertEqual(pieces[0].color, 0xFFAA00)
        XCTAssertTrue(pieces[0].bold)
        XCTAssertEqual(pieces[1].color, Motd.plain)
        XCTAssertFalse(pieces[1].bold)
        XCTAssertFalse(Motd.pieces("§lA§cB")[1].bold)  // a colour ends bold, as in Java Edition
    }

    func testStreamerModeHidesWhatTheSharedCasesSay() {
        let streamer = Streamer(Shared.data("streamer.json"))
        XCTAssertFalse(streamer.cases.isEmpty)
        for (shown, hidden) in streamer.cases {
            XCTAssertEqual(streamer.hide(shown), hidden, shown)
        }
    }

    func testTheServerIconGoesToTheAgentsRouteAsBase64() async throws {
        StubAgent.json(#"{"png": null}"#)
        let none = try await client().serverIcon(serverId: "s")
        XCTAssertNil(none)
        StubAgent.json(#"{"ok": true, "icon": true, "running": true}"#)
        let saved = try await client().saveServerIcon(serverId: "s", png: "iVBORw0K")
        XCTAssertTrue(saved.running)
        StubAgent.json(#"{"ok": true, "icon": false, "running": false}"#)
        let removed = try await client().removeServerIcon(serverId: "s")
        XCTAssertFalse(removed.icon)
        XCTAssertEqual(StubAgent.requests.map { $0.request.httpMethod }, ["GET", "PUT", "DELETE"])
        XCTAssertEqual(String(data: StubAgent.requests[1].body, encoding: .utf8), #"{"png":"iVBORw0K"}"#)
    }
}
