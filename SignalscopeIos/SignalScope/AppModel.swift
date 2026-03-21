import Foundation
import SwiftUI
import Combine

@MainActor
final class AppModel: ObservableObject {
    @AppStorage("SignalScope.baseURL") private var baseURLString: String = ""
    @AppStorage("SignalScope.token") private var storedToken: String = ""
    @AppStorage("SignalScope.refreshInterval") private var storedRefresh: Double = 20

    @Published var chains: [ChainSummary] = []
    @Published var activeFaults: [ChainSummary] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    let api = APIClient()
    private var pollTask: Task<Void, Never>?

    init() {
        api.baseURL = URL(string: baseURLString.trimmed)
        api.token = storedToken
        NotificationManager.shared.requestAuthorization()
        startPolling()
    }

    var sortedChains: [ChainSummary] {
        chains.sorted { lhs, rhs in
            if lhs.sortPriority != rhs.sortPriority { return lhs.sortPriority < rhs.sortPriority }
            if lhs.age_secs != rhs.age_secs { return lhs.age_secs < rhs.age_secs }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    var displayedFaults: [ChainSummary] {
        let source = activeFaults.isEmpty ? chains.filter { $0.displayStatus == .fault } : activeFaults
        return source.sorted { lhs, rhs in
            if lhs.age_secs != rhs.age_secs { return lhs.age_secs < rhs.age_secs }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    func updateSettings(baseURL: String, token: String, refresh: Double) {
        baseURLString = baseURL
        storedToken = token
        storedRefresh = max(5, refresh)
        api.baseURL = URL(string: baseURL.trimmed)
        api.token = token
        restartPolling()
    }

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await refreshAll()
                let refresh = max(5, storedRefresh)
                try? await Task.sleep(nanoseconds: UInt64(refresh * 1_000_000_000))
            }
        }
    }

    func restartPolling() {
        startPolling()
    }

    func stopPolling() {
        pollTask?.cancel()
    }

    func refreshAll() async {
        await fetchChains()
    }

    func fetchChains() async {
        guard api.baseURL != nil else {
            errorMessage = "Enter your SignalScope Hub URL in Settings."
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            async let chainsTask = api.fetchChains()
            async let faultsTask = api.fetchActiveFaults()
            let (newChains, fetchedFaults) = try await (chainsTask, faultsTask)
            handleNotifications(old: chains, new: newChains)
            chains = newChains
            activeFaults = fetchedFaults
            errorMessage = nil
        } catch {
            do {
                let newChains = try await api.fetchChains()
                handleNotifications(old: chains, new: newChains)
                chains = newChains
                activeFaults = newChains.filter { $0.displayStatus == .fault }
                errorMessage = nil
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func handleNotifications(old: [ChainSummary], new: [ChainSummary]) {
        let oldMap = Dictionary(uniqueKeysWithValues: old.map { ($0.id, $0) })

        for item in new {
            if let previous = oldMap[item.id] {
                if previous.displayStatus != .fault && item.displayStatus == .fault {
                    NotificationManager.shared.scheduleFaultNotification(chain: item)
                }
            } else if item.displayStatus == .fault {
                NotificationManager.shared.scheduleFaultNotification(chain: item)
            }
        }
    }
}

private extension String {
    var trimmed: String {
        trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
