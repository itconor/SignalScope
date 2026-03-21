import Foundation

struct ChainsListResponse: Codable {
    let ok: Bool
    let results: [ChainSummary]
    let generated_at: TimeInterval
}

struct ChainDetailResponse: Codable {
    let ok: Bool
    let chain: ChainSummary
}

struct ActiveFaultsResponse: Codable {
    let ok: Bool
    let results: [ChainSummary]
    let count: Int
    let generated_at: TimeInterval
}

struct ChainSummary: Codable, Identifiable {
    let id: String
    let name: String
    let status: String
    let display_status: String
    let fault_index: Int?
    let fault_at: String?
    let fault_reason: String?
    let pending: Bool
    let adbreak: Bool
    let adbreak_remaining: Double?
    let maintenance: Bool
    let maintenance_nodes: [String]
    let flapping: Bool
    let shared_fault_chains: [String]
    let sla_pct: Double
    let updated_at: TimeInterval
    let age_secs: Double
    let nodes: [ChainNode]

    var displayStatus: ChainDisplayStatus {
        let normalized = display_status.lowercased()

        switch normalized {
        case "ok", "active", "healthy", "good":
            return .ok
        case "fault", "faulted", "failed", "down":
            return .fault
        case "pending", "starting", "warmup":
            return .pending
        case "adbreak", "ad_break":
            return .adbreak
        case "inactive", "disabled", "idle":
            return .pending
        default:
            if adbreak { return .adbreak }
            if pending { return .pending }
            return .unknown
        }
    }

    var nodeCount: Int {
        nodes.reduce(0) { $0 + $1.deepNodeCount }
    }

    var sortPriority: Int {
        if displayStatus == .fault { return 0 }
        if flapping { return 1 }
        if maintenance { return 2 }
        if pending || adbreak { return 3 }
        return 4
    }

    var healthLabel: String {
        if displayStatus == .fault { return "Fault" }
        if flapping { return "Unstable" }
        if maintenance { return "Maintenance" }
        if pending { return "Pending" }
        if adbreak { return "Adbreak" }
        if sla_pct >= 99.95 { return "Excellent" }
        if sla_pct >= 99.0 { return "Healthy" }
        return "Watch"
    }

    var headlineReason: String {
        if let reason = fault_reason, !reason.isEmpty { return reason }
        if flapping { return "Intermittent state changes detected" }
        if maintenance { return "Maintenance mode active" }
        if pending { return "Awaiting confirmation logic" }
        return displayStatus.label.capitalized
    }

    var activeFlags: [String] {
        var flags: [String] = []
        if pending { flags.append("Pending") }
        if adbreak { flags.append("Adbreak") }
        if maintenance { flags.append("Maintenance") }
        if flapping { flags.append("Flapping") }
        return flags
    }

    var diagramNodes: [ChainNode] { nodes }

    var faultNodeID: String? {
        guard let fault_at, !fault_at.isEmpty else { return nil }
        for node in nodes {
            if let found = node.findNodeID(matchingLabel: fault_at) {
                return found
            }
        }
        return nil
    }
}

struct ChainNode: Codable, Identifiable, Hashable {
    let type: String
    let label: String
    let stream: String?
    let site: String?
    let status: String
    let reason: String?
    let machine: String?
    let live_url: String?
    let level_dbfs: Double?
    let ts: TimeInterval?
    let mode: String?
    let nodes: [ChainNode]?

    var id: String {
        [type, label, stream ?? "", site ?? "", machine ?? ""].joined(separator: "|")
    }

    var childNodes: [ChainNode] {
        nodes ?? []
    }

    var isStack: Bool {
        type.lowercased() == "stack"
    }

    var deepNodeCount: Int {
        if childNodes.isEmpty { return 1 }
        return childNodes.reduce(isStack ? 0 : 1) { $0 + $1.deepNodeCount }
    }

    var statusLabel: String {
        status.uppercased()
    }

    var normalizedStatus: String {
        status.lowercased()
    }

    var isFaultLike: Bool {
        ["down", "offline"].contains(normalizedStatus)
    }

    var isMaintenance: Bool {
        normalizedStatus == "maintenance"
    }

    var signalFraction: Double? {
        guard let level_dbfs else { return nil }
        let clamped = min(max(level_dbfs, -60), 0)
        return (clamped + 60) / 60
    }

    var signalLabel: String {
        guard let level_dbfs else { return "No level" }
        if level_dbfs >= -12 { return "Hot" }
        if level_dbfs >= -24 { return "Healthy" }
        if level_dbfs >= -42 { return "Low" }
        return "Near silence"
    }

    func resolvedLiveURL(baseURL: URL?) -> URL? {
        guard let live_url else { return nil }

        let trimmed = live_url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        if let absolute = URL(string: trimmed), absolute.scheme != nil {
            return absolute
        }

        if let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
           let absolute = URL(string: encoded), absolute.scheme != nil {
            return absolute
        }

        guard let baseURL else { return nil }
        if let relative = URL(string: trimmed, relativeTo: baseURL)?.absoluteURL {
            return relative
        }
        if let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) {
            return URL(string: encoded, relativeTo: baseURL)?.absoluteURL
        }
        return nil
    }

    func findNodeID(matchingLabel label: String) -> String? {
        if self.label.caseInsensitiveCompare(label) == .orderedSame {
            return id
        }
        for child in childNodes {
            if let found = child.findNodeID(matchingLabel: label) {
                return found
            }
        }
        return nil
    }
}

extension TimeInterval {
    var asDate: Date { Date(timeIntervalSince1970: self) }
}

extension Double {
    func formattedSeconds() -> String {
        if self < 60 { return String(format: "%.0fs", self) }
        let minutes = Int(self) / 60
        let seconds = Int(self) % 60
        return String(format: "%dm %02ds", minutes, seconds)
    }

    func formattedPercent() -> String {
        String(format: "%.1f%%", self)
    }

    func formattedDbfs() -> String {
        String(format: "%.1f dBFS", self)
    }
}
