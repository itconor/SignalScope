import Foundation
import UserNotifications

@MainActor
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()

    func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            Task { @MainActor in
                UNUserNotificationCenter.current().delegate = self
            }
        }
    }

    func scheduleFaultNotification(chain: ChainSummary) {
        let content = UNMutableNotificationContent()
        content.title = "Fault: \(chain.name)"
        content.body = chain.fault_reason ?? "Chain entered FAULT state"
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: "fault_\(chain.id)_\(UUID().uuidString)",
            content: content,
            trigger: nil
        )

        UNUserNotificationCenter.current().add(request)
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
}
