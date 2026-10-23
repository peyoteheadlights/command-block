import XCTest
@testable import Core

/// Wake-on-LAN and alerts per device, from the iPhone (agent/wake.py, agent/api/routes/phoneapp.py).
final class WakeTests: XCTestCase {
    private let pairing = Pairing(host: "localhost", port: 8765, fingerprint: String(repeating: "ab", count: 32), apiVersion: 1)
    private let withHelper = """
        {"on": true, "pc": "GAMING-PC", "mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255",
         "helper": {"address": "100.64.0.9", "port": 47391}, "router_url": null, "has_key": false}
        """

    override func setUp() {
        super.setUp()
        StubAgent.reset()
    }

    private func client() -> AgentClient {
        AgentClient(pairing: pairing, token: { "t" }, onSignedOut: {}, log: NoLog(), configuration: StubAgent.configuration())
    }

    private func path(_ index: Int) -> String {
        StubAgent.requests[index].request.url?.path ?? ""
    }

    func testWithAHelpingPCThePhoneAsksForItsOwnKeyOnce() async throws {
        StubAgent.json(withHelper)
        StubAgent.json("{\"key\": \"secret-key\"}")
        StubAgent.json(withHelper.replacingOccurrences(of: "\"has_key\": false", with: "\"has_key\": true"))
        let c = client()
        let kept = try await KeptWake.keep(c, before: nil)
        XCTAssertEqual(kept.key, "secret-key")
        XCTAssertEqual([path(0), path(1)], ["/api/wake", "/api/wake/key"])
        XCTAssertEqual(StubAgent.requests[1].request.httpMethod, "POST")
        let again = try await KeptWake.keep(c, before: kept)
        XCTAssertEqual(again.key, "secret-key")
        XCTAssertEqual(StubAgent.requests.count, 3)
        XCTAssertTrue(kept.offered)
        XCTAssertEqual(kept.helperRequest, Data("CBWAKE1 secret-key".utf8))
    }

    func testWithoutAHelpingPCOrRouterAnIPhoneHasNoWake() async throws {
        StubAgent.json("{\"on\": true, \"pc\": \"PC\", \"mac\": \"AA:BB:CC:DD:EE:FF\", \"has_key\": false}")
        let kept = try await KeptWake.keep(client(), before: KeptWake(info: WakeInfo(), key: "old"))
        XCTAssertNil(kept.key)
        // It can't send the wake signal itself.
        XCTAssertFalse(kept.offered)
        var router = kept
        router.info.routerUrl = "https://fritz.box/wake"
        XCTAssertTrue(router.offered)
        router.info.on = false
        XCTAssertFalse(router.offered)
    }

    func testAlertsForThisDeviceFollowTheSharedListOrItsOwnChoice() async throws {
        StubAgent.json("{\"own\": false, \"events\": {\"server_crashed\": true, \"player_joined\": false}}")
        StubAgent.json("{\"own\": true, \"events\": {\"server_crashed\": true, \"player_joined\": true}}")
        let c = client()
        let shared = try await c.deviceAlerts()
        XCTAssertFalse(shared.own)
        XCTAssertEqual(shared.chosen, ["server_crashed"])
        let chosen = try await c.chooseDeviceAlerts(own: true, events: ["server_crashed", "player_joined"])
        XCTAssertTrue(chosen.own)
        XCTAssertEqual(StubAgent.requests[1].request.httpMethod, "PUT")
        let sent = try JSONDecoder().decode([String: JSONValue].self, from: StubAgent.requests[1].body)
        XCTAssertEqual(sent["own"], .bool(true))
        XCTAssertEqual(sent["events"], .array([.string("player_joined"), .string("server_crashed")]))
    }

    func testThePhoneAsksWhetherThePCCanWakeAnIPhone() async throws {
        StubAgent.json("{\"configured\": true, \"registered\": false}")
        _ = try await client().phone()
        XCTAssertEqual(StubAgent.requests[0].request.url?.query, "platform=ios")
    }
}
