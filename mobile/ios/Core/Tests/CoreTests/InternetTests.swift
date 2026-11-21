import XCTest
@testable import Core

final class InternetTests: XCTestCase {
    private let s = Strings(Shared.strings)

    private func reach(_ text: String) throws -> InternetReach {
        try JSONDecoder().decode(InternetReach.self, from: Data(text.utf8))
    }

    func testAnOpenPortIsNotKnownUntilTested() throws {
        let info = try reach("""
        {"on": true, "known": false, "reason": "not_tested", "address": "1.2.3.4", "port": 25565,
         "ports": [{"protocol": "TCP", "port": 25565}], "local_address": "192.168.1.20"}
        """)
        XCTAssertEqual(Internet.lines(s, info, owner: true).map(\.text),
                       [s.t("address.open", ["address": "1.2.3.4:25565"]), s.t("address.not_known")])
        XCTAssertTrue(Internet.testable(info))
        XCTAssertEqual(Internet.forwardSteps(s, info, technical: false), [])
    }

    func testANameIsOfferedOnlyOnceTheOutsideTestPassed() throws {
        let names = #""names": [{"provider": "duckdns", "name": "myserver.duckdns.org"}]"#
        let untested = try reach(#"{"on": true, "reason": "not_tested", "address": "1.2.3.4", "port": 25565, "# + names + "}")
        XCTAssertEqual(Internet.nameLines(s, untested), [])
        let works = try reach(#"{"on": true, "reason": "tested", "works": true, "address": "1.2.3.4", "port": 25565, "# + names + "}")
        XCTAssertEqual(Internet.nameLines(s, works), [s.t("ddns.join", ["name": "myserver.duckdns.org", "port": "25565"])])
    }

    func testANoIpNameShowsItsConfirmDateAndTurnsRedAWeekBefore() throws {
        let text = #"{"noip": {"name": "mine.ddns.net", "confirm_by": 1000.0, "confirm_soon": true}, "duckdns_site": "a", "noip_site": "b"}"#
        let state = try JSONDecoder().decode(DdnsState.self, from: Data(text.utf8))
        let lines = Internet.savedLines(s, kind: "noip", try XCTUnwrap(state.noip)) { _ in "1 Jan" }
        XCTAssertEqual(lines.first?.text, s.t("ddns.noip_saved", ["name": "mine.ddns.net"]))
        XCTAssertEqual(lines.last?.tone, .danger)
    }

    func testNoRouterGivesTheForwardingStepsWithThisPcsAddress() throws {
        let info = try reach("""
        {"on": true, "reason": "no_router", "port": 25565,
         "ports": [{"protocol": "TCP", "port": 25565}], "local_address": "192.168.1.20"}
        """)
        XCTAssertEqual(Internet.lines(s, info, owner: true).map(\.tone), [.danger])
        XCTAssertEqual(Internet.forwardSteps(s, info, technical: false).first,
                       s.t("address.forward_step", ["protocol": "TCP", "port": "25565", "local": "192.168.1.20"]))
        XCTAssertFalse(Internet.testable(info))
    }

    func testATestedAddressSaysWhoTestedIt() throws {
        let info = try reach("""
        {"on": true, "known": true, "works": true, "tested_by": "phone", "reason": "tested", "address": "1.2.3.4", "port": 25565}
        """)
        let line = try XCTUnwrap(Internet.lines(s, info, owner: false).last)
        XCTAssertEqual(line.tone, .success)
        XCTAssertTrue(line.text.contains(s.t("address.by_phone")))
    }
}
