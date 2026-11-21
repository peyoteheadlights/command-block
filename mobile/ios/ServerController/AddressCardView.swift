import Core
import Network
import SwiftUI

/// Server address: "Let friends join from the internet" (owner only), what
/// the router did, the two outside tests (mcsrvstat.us after asking once,
/// or this phone over mobile data), port forwarding by hand, and playit.gg
/// when the internet provider blocks it (mirrors AddressCard in ServerSettings.kt).
struct AddressCardView: View {
    let setup: SetupPages
    let serverId: String
    let palette: Palette
    let ask: (SetupAsk) -> Void

    @Environment(AppModel.self) private var model
    @Environment(\.openURL) private var openURL
    @State private var mobileOnly = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let problem = setup.internet.problem {
                Text(problem.message(model.strings)).foregroundStyle(palette.danger)
            }
            if let info = setup.internet.value {
                details(info)
            }
        }
        .task { mobileOnly = await OutsidePing.onMobileDataOnly() }
    }

    @ViewBuilder
    private func details(_ info: InternetReach) -> some View {
        let owner = model.me?.isOwner == true
        Toggle(model.t("address.internet_label"), isOn: Binding(
            get: { info.on },
            set: { on in Task { await setup.setInternet(on, model.strings) } }
        ))
        .disabled(!owner || setup.busy != nil)
        if setup.busy == "internet" { Hint(text: model.t("address.busy"), palette: palette) }
        ForEach(Internet.lines(model.strings, info, owner: owner), id: \.text) { line in
            Text(line.text).foregroundStyle(palette.tone(line.tone))
        }
        if let link = info.playit, let url = URL(string: link) {
            Button(model.t("address.playit")) { openURL(url) }
        }
        if Internet.testable(info), let address = info.address, let port = info.port {
            if owner {
                Button(model.t("address.test")) {
                    if info.mcsrvstatOk {
                        Task { await setup.testInternet(allow: false, model.strings) }
                    } else {
                        ask(.mcsrvstat)
                    }
                }
                .disabled(setup.busy != nil)
            }
            let bedrock = model.servers.first(where: { $0.id == serverId })?.edition == "bedrock"
            Button(model.t("address.test_phone_button")) {
                Task { await setup.phoneTest(address: address, port: port, bedrock: bedrock, model.strings) }
            }
            .disabled(!mobileOnly || setup.busy != nil)
            if !mobileOnly { Hint(text: model.t("address.wifi_off"), palette: palette) }
        }
        let steps = Internet.forwardSteps(model.strings, info, technical: model.technical)
        if !steps.isEmpty {
            Text(model.t("address.forward_title")).foregroundStyle(palette.textPrimary)
            ForEach(steps, id: \.self) { Hint(text: $0, palette: palette) }
        }
    }
}

/// The phone's own ping, over mobile data, like a friend's game would ask
/// (mirrors OutsidePing in Internet.kt): Java's server list ping, or
/// Bedrock's NetherNet join page then RakNet. True only when the server
/// answered like a Minecraft server.
enum OutsidePing {
    static func ask(host: String, port: Int, bedrock: Bool) async -> Bool {
        if bedrock {
            if await netherNet(host: host, port: port) { return true }
            return await exchange(host: host, port: port, udp: true, send: rakNetPing()) { $0.first == 0x1C }
        }
        return await exchange(host: host, port: port, udp: false, send: javaPing(host: host, port: port)) { reply in
            // length, packet id 0, then the JSON's length and its "{"
            var bytes = reply[...]
            guard readVarInt(&bytes) != nil, readVarInt(&bytes) == 0, (readVarInt(&bytes) ?? 0) > 0 else { return false }
            return bytes.first == UInt8(ascii: "{")
        }
    }

    /// Wi-Fi off and mobile data on, so the phone asks from outside the home.
    static func onMobileDataOnly() async -> Bool {
        await withCheckedContinuation { done in
            let monitor = NWPathMonitor()
            monitor.pathUpdateHandler = { path in
                monitor.cancel()
                done.resume(returning: path.status == .satisfied
                    && path.availableInterfaces.contains { $0.type == .cellular }
                    && !path.availableInterfaces.contains { $0.type == .wifi })
            }
            monitor.start(queue: DispatchQueue(label: "mobile-data-check"))
        }
    }

    /// Asked over a plain connection, not URLSession, so App Transport Security
    /// doesn't refuse a plain http address.
    private static func netherNet(host: String, port: Int) async -> Bool {
        let get = Data("GET /v1/join HTTP/1.0\r\nHost: \(host)\r\n\r\n".utf8)
        return await exchange(host: host, port: port, udp: false, send: get, whole: true) { reply in
            let text = String(decoding: reply, as: UTF8.self)
            return text.hasPrefix("HTTP/1.") && text.contains(" 200 ") && text.contains("\"version\"")
        }
    }

    private static func exchange(host: String, port: Int, udp: Bool, send: Data, whole: Bool = false,
                                 answered: @escaping @Sendable (Data) -> Bool) async -> Bool {
        guard let where_ = NWEndpoint.Port(rawValue: UInt16(clamping: port)) else { return false }
        let connection = NWConnection(host: NWEndpoint.Host(host), port: where_, using: udp ? .udp : .tcp)
        return await withCheckedContinuation { done in
            let once = Once(done)
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    connection.send(content: send, completion: .contentProcessed { _ in
                        read(connection, Data(), whole: whole) { reply in
                            once.finish(answered(reply))
                            connection.cancel()
                        }
                    })
                case .failed, .cancelled:
                    once.finish(false)
                default:
                    break
                }
            }
            connection.start(queue: DispatchQueue(label: "outside-ping"))
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) {
                once.finish(false)
                connection.cancel()
            }
        }
    }

    /// One answer, or with `whole` everything until the other side closes (64 KB at most).
    private static func read(_ connection: NWConnection, _ got: Data, whole: Bool, done: @escaping @Sendable (Data) -> Void) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, complete, error in
            let all = got + (data ?? Data())
            if !whole || complete || error != nil || all.count >= 65536 {
                done(all)
            } else {
                read(connection, all, whole: whole, done: done)
            }
        }
    }

    private static func javaPing(host: String, port: Int) -> Data {
        var body = Data([0])
        body += varInt(-1) // protocol -1: just asking
        body += varInt(host.utf8.count) + Data(host.utf8)
        body += Data([UInt8(port >> 8 & 0xFF), UInt8(port & 0xFF)])
        body += varInt(1) // next: status
        return varInt(body.count) + body + Data([1, 0])
    }

    private static func rakNetPing() -> Data {
        var ping = Data(count: 33)
        ping[0] = 0x01
        let magic: [UInt8] = [0x00, 0xFF, 0xFF, 0x00, 0xFE, 0xFE, 0xFE, 0xFE, 0xFD, 0xFD, 0xFD, 0xFD, 0x12, 0x34, 0x56, 0x78]
        ping.replaceSubrange(9..<25, with: magic)
        return ping
    }

    private static func varInt(_ number: Int) -> Data {
        var value = UInt32(truncatingIfNeeded: number)
        var out = Data()
        repeat {
            var byte = UInt8(value & 0x7F)
            value >>= 7
            if value != 0 { byte |= 0x80 }
            out.append(byte)
        } while value != 0
        return out
    }

    private static func readVarInt(_ bytes: inout Data.SubSequence) -> Int? {
        var value = 0
        for shift in stride(from: 0, to: 35, by: 7) {
            guard let byte = bytes.popFirst() else { return nil }
            value |= Int(byte & 0x7F) << shift
            if byte & 0x80 == 0 { return value }
        }
        return nil
    }
}

/// Resumes a continuation once, whichever of the answer or the time limit comes first.
private final class Once: @unchecked Sendable {
    private var continuation: CheckedContinuation<Bool, Never>?
    private let lock = NSLock()

    init(_ continuation: CheckedContinuation<Bool, Never>) { self.continuation = continuation }

    func finish(_ value: Bool) {
        lock.lock()
        let waiting = continuation
        continuation = nil
        lock.unlock()
        waiting?.resume(returning: value)
    }
}
