import SwiftUI

struct ChainsListView: View {
    @EnvironmentObject private var appModel: AppModel

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.backgroundGradient
                    .ignoresSafeArea()

                if appModel.chains.isEmpty, !appModel.isLoading {
                    emptyState
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            headerSummary

                            if let error = appModel.errorMessage {
                                errorBanner(error)
                            }

                            ForEach(appModel.sortedChains) { chain in
                                NavigationLink {
                                    ChainDetailView(chainID: chain.id, initialChain: chain)
                                } label: {
                                    ChainRowView(chain: chain)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding()
                    }
                    .refreshable {
                        await appModel.fetchChains()
                    }
                }
            }
            .navigationTitle("Chains")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if appModel.isLoading {
                        ProgressView()
                            .tint(Theme.brandBlue)
                    }
                }
            }
        }
        .task {
            if appModel.chains.isEmpty {
                await appModel.fetchChains()
            }
        }
    }

    private var headerSummary: some View {
        PanelCard {
            HStack(spacing: 10) {
                summaryBubble(title: "Faults", value: "\(appModel.displayedFaults.count)", color: Theme.faultRed)
                summaryBubble(title: "Flapping", value: "\(appModel.chains.filter { $0.flapping }.count)", color: Theme.pendingAmber)
                summaryBubble(title: "Maintenance", value: "\(appModel.chains.filter { $0.maintenance }.count)", color: Theme.mutedText)
                Spacer(minLength: 0)
            }
        }
    }

    private func summaryBubble(title: String, value: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value)
                .font(.title3.weight(.bold))
                .foregroundStyle(Theme.primaryText)
            Text(title)
                .font(.caption)
                .foregroundStyle(Theme.secondaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(color.opacity(0.14))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(color.opacity(0.55), lineWidth: 1)
        )
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "dot.radiowaves.left.and.right")
                .font(.system(size: 46))
                .foregroundStyle(Theme.brandBlue)

            Text("No chains yet")
                .font(.title3.weight(.semibold))
                .foregroundStyle(Theme.primaryText)

            Text(appModel.errorMessage ?? "Add your Hub URL and token in Settings, then pull to refresh.")
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.secondaryText)
                .padding(.horizontal)
        }
        .padding()
    }

    private func errorBanner(_ text: String) -> some View {
        PanelCard {
            Text(text)
                .font(.subheadline)
                .foregroundStyle(Theme.primaryText)
        }
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Theme.faultRed.opacity(0.8), lineWidth: 1)
        )
    }
}

private struct ChainRowView: View {
    let chain: ChainSummary

    var body: some View {
        PanelCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(chain.name)
                            .font(.headline)
                            .foregroundStyle(Theme.primaryText)

                        Text(chain.healthLabel)
                            .font(.subheadline)
                            .foregroundStyle(Theme.secondaryText)
                    }

                    Spacer()
                    StatusPill(status: chain.displayStatus)
                }

                ChainDiagramStrip(nodes: chain.diagramNodes)

                HStack(spacing: 8) {
                    MetricChip(icon: "clock", text: "Age \(chain.age_secs.formattedSeconds())")
                    MetricChip(icon: "chart.line.uptrend.xyaxis", text: "SLA \(chain.sla_pct.formattedPercent())")
                    MetricChip(icon: "point.3.connected.trianglepath.dotted", text: "\(chain.nodeCount) nodes")
                }

                if !chain.activeFlags.isEmpty {
                    FlowLayout(spacing: 8) {
                        ForEach(chain.activeFlags, id: \.self) { flag in
                            MetricChip(icon: iconName(for: flag), text: chipText(for: flag, chain: chain))
                        }
                    }
                }

                Text(chain.headlineReason)
                    .font(.footnote)
                    .foregroundStyle(Theme.secondaryText)
            }
            .overlay(alignment: .bottomTrailing) {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.mutedText)
            }
        }
    }

    private func iconName(for flag: String) -> String {
        switch flag {
        case "Pending": return "hourglass"
        case "Adbreak": return "play.rectangle"
        case "Maintenance": return "wrench.and.screwdriver"
        case "Flapping": return "arrow.triangle.2.circlepath"
        default: return "circle"
        }
    }

    private func chipText(for flag: String, chain: ChainSummary) -> String {
        if flag == "Adbreak" {
            let remaining = chain.adbreak_remaining.map { $0.formattedSeconds() } ?? "Live"
            return "Adbreak \(remaining)"
        }
        return flag
    }
}

private struct FlowLayout<Content: View>: View {
    let spacing: CGFloat
    let content: Content

    init(spacing: CGFloat = 8, @ViewBuilder content: () -> Content) {
        self.spacing = spacing
        self.content = content()
    }

    var body: some View {
        HStack(spacing: spacing) {
            content
            Spacer(minLength: 0)
        }
    }
}
