import SwiftUI

enum Theme {
    // Palette
    static let backgroundTop = Color(hex: "04112A")
    static let backgroundBottom = Color(hex: "030A18")
    static let panel = Color(hex: "0D2754")
    static let panelSecondary = Color(hex: "113163")
    static let panelBorder = Color(hex: "1F4C8E").opacity(0.6)
    static let brandBlue = Color(hex: "27A5FF")
    static let brandBlueDark = Color(hex: "1350A5")
    static let okGreen = Color(hex: "18E471")
    static let pendingAmber = Color(hex: "FFAE00")
    static let faultRed = Color(hex: "FF4F4F")
    static let primaryText = Color(hex: "FFFFFF")
    static let secondaryText = Color(hex: "ACC2E4")
    static let mutedText = Color(hex: "7A96C3")

    static let backgroundGradient = LinearGradient(
        colors: [backgroundTop, backgroundBottom],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: // RGB (12-bit)
            (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: // RGB (24-bit)
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: // ARGB (32-bit)
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            (a, r, g, b) = (255, 0, 0, 0)
        }
        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}

struct PanelCard<Content: View>: View {
    let title: String?
    let content: Content

    init(title: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title = title {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(Theme.secondaryText)
            }
            content
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Theme.panel)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Theme.panelBorder, lineWidth: 1)
        )
    }
}

enum ChainDisplayStatus: String, Codable, CaseIterable {
    case ok, pending, fault, adbreak, unknown

    var color: Color {
        switch self {
        case .ok: return Theme.okGreen
        case .pending, .adbreak: return Theme.pendingAmber
        case .fault: return Theme.faultRed
        case .unknown: return Theme.mutedText
        }
    }

    var label: String { rawValue.uppercased() }
}

struct StatusPill: View {
    let status: ChainDisplayStatus

    var body: some View {
        Text(status.label)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule(style: .continuous)
                    .fill(status.color)
            )
            .accessibilityLabel("Status: \(status.label)")
    }
}

struct MetricChip: View {
    let icon: String?
    let text: String

    var body: some View {
        HStack(spacing: 6) {
            if let icon = icon {
                Image(systemName: icon)
                    .font(.caption2)
                    .foregroundStyle(Theme.brandBlue)
            }
            Text(text)
                .font(.caption2)
                .foregroundStyle(Theme.secondaryText)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Theme.panelSecondary.opacity(0.6))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Theme.panelBorder.opacity(0.6), lineWidth: 1)
        )
    }
}

struct PanelCard_Previews: PreviewProvider {
    static var previews: some View {
        PanelCard(title: "Panel Title") {
            Text("This is the content of the panel.")
                .foregroundColor(Theme.primaryText)
        }
        .padding()
        .background(Theme.backgroundGradient)
        .previewLayout(.sizeThatFits)
    }
}

struct StatusPill_Previews: PreviewProvider {
    static var previews: some View {
        VStack(spacing: 10) {
            ForEach(ChainDisplayStatus.allCases, id: \.self) { status in
                StatusPill(status: status)
            }
        }
        .padding()
        .background(Theme.backgroundGradient)
        .previewLayout(.sizeThatFits)
    }
}

struct MetricChip_Previews: PreviewProvider {
    static var previews: some View {
        VStack(spacing: 10) {
            MetricChip(icon: "bolt.fill", text: "Fast")
            MetricChip(icon: nil, text: "No Icon")
        }
        .padding()
        .background(Theme.backgroundGradient)
        .previewLayout(.sizeThatFits)
    }
}
