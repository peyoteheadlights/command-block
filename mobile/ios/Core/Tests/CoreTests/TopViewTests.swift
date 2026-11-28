import XCTest
@testable import Core

final class TopViewTests: XCTestCase {
    func testTheOpenTabIsInFrontThenTheOnesAfterItThenAround() {
        let strip: [String?] = [nil, "a", "b", "c"]
        XCTAssertEqual(TopView.order(strip, open: "b"), ["b", "c", nil, "a"])
        XCTAssertEqual(TopView.order(strip, open: nil), strip)
        XCTAssertEqual(TopView.order(strip, open: "c"), ["c", nil, "a", "b"])
    }

    func testAServerThatIsGoneLeavesTheStripAsItIs() {
        XCTAssertEqual(TopView.order([nil, "a"] as [String?], open: "removed"), [nil, "a"])
    }

    func testAPullOpensItOnlyWhenItWentFarEnough() {
        XCTAssertFalse(TopView.opens(wasOpen: false, pulled: 40, distance: 300))
        XCTAssertTrue(TopView.opens(wasOpen: false, pulled: 90, distance: 300))
        XCTAssertFalse(TopView.opens(wasOpen: false, pulled: -200, distance: 300))
    }

    func testAnOpenOneClosesOnlyWhenPushedBackFarEnough() {
        XCTAssertTrue(TopView.opens(wasOpen: true, pulled: -40, distance: 300))
        XCTAssertFalse(TopView.opens(wasOpen: true, pulled: -90, distance: 300))
        XCTAssertTrue(TopView.opens(wasOpen: true, pulled: 120, distance: 300))
    }
}
