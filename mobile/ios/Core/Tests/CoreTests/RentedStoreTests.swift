import XCTest
@testable import Core

/// Rented servers, the Store tab and the Bedrock switch on the phones
/// (mirrors RentedStoreTest.kt).
final class RentedStoreTests: XCTestCase {
    private let token = "secret-session-token-1234567890"
    private let strings = Strings(Shared.strings)

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

    func testTheRentedListReadsServersAndPanelsAndKillSendsConfirm() async throws {
        StubAgent.json(#"{"servers": [{"id": "r-1", "kind": "pterodactyl", "name": "Survival", "host": "panel.x", "panel_id": "p1", "state": "UNKNOWN", "state_reason": "The panel refused the key.", "resources": null, "discord": "status"}], "panels": [{"id": "p1", "host": "panel.x", "address": "https://panel.x"}]}"#)
        let list = try await client().rented()
        let row = try XCTUnwrap(list.servers.first)
        XCTAssertEqual(row.panelId, "p1")
        XCTAssertEqual(Rented.stateLine(strings, row), "Unknown: The panel refused the key.")
        XCTAssertEqual(Rented.usage(strings, row.resources), "Memory Unknown, CPU Unknown, disk Unknown")
        StubAgent.json(#"{"result": "REQUESTED", "detail": "The panel was asked."}"#)
        _ = try await client().rentedPower("r-1", signal: "kill", confirm: true)
        XCTAssertEqual(StubAgent.requests.map { $0.request.url?.path }, ["/api/rented", "/api/rented/r-1/power"])
        XCTAssertEqual(String(decoding: StubAgent.requests[1].body, as: UTF8.self), #"{"confirm":true,"signal":"kill"}"#)
    }

    func testPowerButtonsFollowTheStateAndTheOwnerConnects() {
        XCTAssertTrue(Rented.enabled(States.OFFLINE, "start"))
        XCTAssertFalse(Rented.enabled(States.OFFLINE, "stop"))
        XCTAssertTrue(Rented.enabled(States.ONLINE, "kill"))
        for action in Rented.POWER + ["kill"] { XCTAssertFalse(Rented.enabled(States.UNKNOWN, action)) }
        XCTAssertFalse(Rented.mayConnect(Me(user: "mod", role: "moderator")))
        XCTAssertTrue(Rented.mayConnect(Me(user: "mark")))
        for key in Rented.NEEDS_THIS_PC { XCTAssertNotNil(Shared.strings[key], key) }
        XCTAssertTrue(String(decoding: Shared.data("links.json"), as: UTF8.self).contains("\"aternos\": \"" + Rented.ATERNOS + "\""))
    }

    func testABackupStillBeingMadeSaysSo() {
        XCTAssertEqual(Rented.backupLine(strings, RentedBackup(name: "Nightly")), "Nightly: the host is still making it")
        let failed = RentedBackup(name: "Nightly", bytes: 2048, completedAt: "2026-11-20T10:15:00+00:00", succeeded: false)
        XCTAssertEqual(Rented.backupLine(strings, failed), "Nightly, 2.0 KB, 2026-11-20 10:15 (failed)")
    }

    func testTheStoreShowsOnlyForTheOwnerInTechnicalOnAJavaCommunityServer() {
        let row = ServerRow(id: "s", name: "S", edition: "java", playedBy: "community")
        let owner = Me(user: "mark")
        XCTAssertTrue(Store.shown(row, me: owner, technical: true))
        XCTAssertFalse(Store.shown(row, me: owner, technical: false))
        XCTAssertFalse(Store.shown(row, me: Me(user: "mod", role: "moderator"), technical: true))
        var test = row
        test.test = true
        XCTAssertFalse(Store.shown(test, me: owner, technical: true))
        var bedrock = row
        bedrock.edition = "bedrock"
        XCTAssertFalse(Store.shown(bedrock, me: owner, technical: true))
    }

    func testTheStoreViewReadsPackagesAndConnectSendsOnlyTheKey() async throws {
        StubAgent.json(#"{"offered": true, "reason": null, "needs_type": false, "connected": true, "installed": {"tebex": true}, "ranks": [{"id": "supporter", "name": "Supporter", "made": true}], "store": {"name": "Shop", "link": "https://shop.tebex.io", "currency": "EUR"}, "packages": [{"id": "1", "name": "Supporter", "price": 5.0, "rank": "supporter"}, {"id": "2", "name": "Kit", "price": 4.99, "rank": null}], "checklist": "1.", "remind": ["Kit"]}"#)
        let view = try await client().connectStore(serverId: "survival", key: " tbx-key ")
        XCTAssertEqual(StubAgent.requests[0].request.url?.path, "/api/servers/survival/store/connect")
        XCTAssertEqual(String(decoding: StubAgent.requests[0].body, as: UTF8.self), #"{"key":"tbx-key"}"#)
        XCTAssertTrue(Store.usable(view))
        let packages = try XCTUnwrap(view.packages)
        XCTAssertEqual(Store.packageLine(view, packages[0]), "✓ Supporter  5 EUR")
        XCTAssertEqual(Store.packageLine(view, packages[1]), "Kit  4.99 EUR")
        XCTAssertEqual(view.remind, ["Kit"])
    }

    func testTheBedrockSwitchIsTheOwnersOnBedrockOnly() throws {
        let group = try XCTUnwrap(ServerForm.GROUPS.first(where: \.bedrockOwner))
        XCTAssertEqual(group.fields.map(\.key), ["server.bedrock_auto_update"])
        XCTAssertTrue(ServerForm.shown(group, technical: false, me: Me(user: "mark"), edition: "bedrock"))
        XCTAssertFalse(ServerForm.shown(group, technical: false, me: Me(user: "mark"), edition: "java"))
        XCTAssertFalse(ServerForm.shown(group, technical: false, me: Me(user: "mod", role: "moderator"), edition: "bedrock"))
    }

    func testAnUpdateAllRowSaysWhenItsListWasASavedCopy() throws {
        let row = try JSONDecoder().decode(UpdateAllRow.self, from: Data(#"{"filename": "a.jar", "saved": "Saved list from 3 days ago."}"#.utf8))
        XCTAssertEqual(row.saved, "Saved list from 3 days ago.")
    }
}
