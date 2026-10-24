import Core
import Foundation
import UserNotifications

/// Apple wakes this with "New alert" and the alert's number only
/// (agent/notifications/apns.py). It reads that one alert from the PC over
/// Tailscale, with the app's own sign-in, and shows its words instead.
/// If the PC can't be reached in time, the plain "New alert" is shown and
/// the app catches up when it's opened.
final class NotificationService: UNNotificationServiceExtension {
    private let lock = NSLock()
    private var handler: ((UNNotificationContent) -> Void)?
    private var content: UNMutableNotificationContent?

    override func didReceive(
        _ request: UNNotificationRequest,
        withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void
    ) {
        let content = (request.content.mutableCopy() as? UNMutableNotificationContent) ?? UNMutableNotificationContent()
        lock.withLock {
            handler = contentHandler
            self.content = content
        }
        let store = SharedStore()
        guard store.lockScreenAlerts,
              let id = (request.content.userInfo["alert"] as? String).flatMap(Int64.init),
              let pairing = store.pairing, TokenStore.read() != nil
        else {
            finish()
            return
        }
        let client = AgentClient(
            pairing: pairing,
            token: { TokenStore.read() },
            onSignedOut: { TokenStore.delete() }
        )
        Task {
            if let alert = try? await client.alerts(after: id - 1, limit: 1).alerts.first, alert.id == id {
                lock.withLock {
                    guard handler != nil else { return }  // too late, already shown
                    content.title = alert.title
                    content.body = alert.body
                    content.threadIdentifier = alert.serverId ?? "pc"
                    let link = alert.serverId.flatMap(DeepLink.server) ?? DeepLink.home
                    content.userInfo["link"] = link.absoluteString
                    // A start request from Discord: Start and Say no on the alert
                    // itself (PushDelegate sends them; the agent checks the permission).
                    if Community.answersStartRequest(alert, now: Date().timeIntervalSince1970, mayStart: true) {
                        content.categoryIdentifier = Community.START_REQUEST_CATEGORY
                        content.userInfo["server"] = alert.serverId
                    }
                }
            }
            finish()
        }
    }

    /// Apple gives the extension about 30 seconds.
    override func serviceExtensionTimeWillExpire() {
        finish()
    }

    /// Hands the words over once, whichever comes first.
    private func finish() {
        let done = lock.withLock { () -> ((UNNotificationContent) -> Void, UNNotificationContent)? in
            guard let handler = handler, let content = content else { return nil }
            self.handler = nil
            return (handler, content)
        }
        if let done = done {
            done.0(done.1)
        }
    }
}
